"""The basket: one capital pool, many stocks, one decision per monthly cycle.

WHY A NEW ENGINE. `research/stock_options/wheel_engine.run_wheel` is
per-symbol with its own capital, so it cannot express the three things a
basket IS -- a shared pool that symbols contend for, rotation between
symbols, and any cross-sectional constraint (which is what a sector cap is).
`growmore_bot.backtest.BacktestEngine` is further away still: one instrument,
one linear position, no expiry and no assignment.

WHY NOT REUSE THE LIVE ENGINE DIRECTLY. `WheelBasketEngine` writes ORM rows
against a live session and reads a real-time Dhan chain, which has no history
for expired contracts. So the decision logic is mirrored here rather than
shared, and `tests/research/wheel_basket/test_live_parity.py` asserts the two
reach the same decisions from the same inputs. That test is the only thing
keeping this from describing a system that is not the one running.

TWO DELIBERATE DIVERGENCES FROM THE LIVE ENGINE, both recorded rather than
hidden:

  * The live engine sizes with `max(1, ...)` lots, which can commit more than
    the pool holds -- harmless against virtual capital, but here it would let
    the book spend money it does not have and report leverage as alpha. This
    engine SKIPS a candidate it cannot fully cash-secure.
  * The live engine re-reads positions from the DB between steps; this one
    holds them in memory. Ordering is identical.

MARKED TO MARKET DAILY, at spot, never at cost basis. An assigned holding
20% underwater has lost that money whether or not the loss is realised, and
marking at basis draws a smooth curve that is a lie. `time_frozen_pct` and
`time_underwater_pct` are reported as first-class numbers for the same
reason: Sharpe hides exactly where this strategy's risk lives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from growmore_bot.backtest.metrics import (
    cagr_pct,
    max_drawdown_pct,
    profit_factor,
    sharpe_ratio,
    win_rate_pct,
)
from growmore_bot.costs import (
    NSE_EQUITY_DELIVERY_COST_MODEL,
    NSE_OPTION_COST_MODEL,
    leg_cost,
)
from growmore_bot.options.strike_selection import rsi_scaled_basis_buffer
from growmore_bot.wheel_basket.scoring import (
    eligible_candidates,
    iv_percentile_ranks,
    should_rotate,
)
from research.wheel_basket.allocation import (
    defence_share,
    flat_order,
    max_sector_share,
    round_robin_order,
)
from research.wheel_basket.config import (
    REGIME_CALL_OTM,
    REGIME_DEPLOY_FRACTION,
    REGIME_GATE_BLOCKS,
    REGIME_PUT_OTM,
    BasketConfig,
)

#: Options slip on premium, not in ticks: Rs 0.10 of tick slip is 5bps on a
#: Rs 200 premium and 6.7% on a Rs 1.50 one. Same figure wheel_engine uses.
PREMIUM_SLIPPAGE_PCT = 0.02


@dataclass
class Leg:
    symbol: str
    opt_type: str
    strike: float
    premium: float
    lots: int
    expiry: int
    basis: Optional[float] = None


@dataclass
class Position:
    symbol: str
    state: str = "flat"          # flat | short_put | holding_shares | short_call
    basis: Optional[float] = None
    shares: int = 0
    lots: int = 0
    leg: Optional[Leg] = None


@dataclass
class BasketResult:
    tag: str
    initial_capital: float
    final_equity: float
    equity_curve: list
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: Optional[float]
    cycles: int
    assignments: int
    called_away: int
    total_cost: float
    min_cash: float
    max_sector_share: float
    defence_share: float
    peak_deployed_fraction: float
    time_underwater_pct: float
    time_frozen_pct: float
    max_concurrent_by_sector: dict
    symbols_traded: set
    legs: list = field(default_factory=list)
    per_year_return_pct: dict = field(default_factory=dict)


def _option_cost(premium_turnover: float, side: str) -> float:
    return leg_cost(max(premium_turnover, 0.0), side, NSE_OPTION_COST_MODEL)


def _delivery_cost(value: float, side: str) -> float:
    """Assignment is an equity delivery trade and is taxed as one."""
    return leg_cost(max(value, 0.0), side, NSE_EQUITY_DELIVERY_COST_MODEL)


def _pick_put(chain, spot: float, target_otm: float, ceiling: Optional[float]) -> Optional[tuple]:
    """Highest strike at or below the target, so "more OTM" is never breached.

    A put is out of the money BELOW spot, so the target is a ceiling, and the
    best fill is the closest strike under it. `ceiling` additionally caps the
    strike at a support level for T2.
    """
    target = spot * (1.0 - target_otm)
    if ceiling is not None:
        target = min(target, ceiling)
    usable = [(k, p) for k, p in chain if k <= target + 1e-9]
    if not usable:
        return None
    return max(usable, key=lambda kp: (kp[0], kp[1]))


def _pick_call(chain, spot: float, target_otm: float, floor: Optional[float]) -> Optional[tuple]:
    """Lowest strike at or above the target -- and never below `floor`, which
    is the assignment basis plus its buffer (the no-loss rule).
    """
    target = spot * (1.0 + target_otm)
    if floor is not None:
        target = max(target, floor)
    usable = [(k, p) for k, p in chain if k >= target - 1e-9]
    if not usable:
        return None
    return min(usable, key=lambda kp: (kp[0], -kp[1]))


def run_basket(
    panels: dict,
    sector_by_symbol: dict,
    is_defence_by_symbol: dict,
    config: BasketConfig,
    initial_capital: float = 10_000_000.0,
    regime_by_day: Optional[dict] = None,
    market_return_by_day: Optional[dict] = None,
) -> BasketResult:
    """Replay one config over the whole panel.

    `regime_by_day` and `market_return_by_day` are INJECTED, so every module
    and test here works without the index data (which needs a live Dhan
    token). A day with no regime label is treated as "no opinion" and behaves
    exactly like the baseline -- a data gap must never become a decision.
    """
    regime_by_day = regime_by_day or {}
    market_return_by_day = market_return_by_day or {}

    cycles_by_decision: dict = {}
    for symbol, panel in panels.items():
        for cycle in panel.cycles:
            cycles_by_decision.setdefault(int(cycle.decision_day), {})[symbol] = cycle

    # Every day any symbol has data for, so marking to market is daily rather
    # than only on decision days.
    day_to_cycles: dict = {}
    for symbol, panel in panels.items():
        for cycle in panel.cycles:
            for index, day in enumerate(cycle.days.tolist()):
                day_to_cycles.setdefault(int(day), {})[symbol] = (cycle, index)
    all_days = sorted(day_to_cycles)
    if not all_days:
        return _empty_result(config.tag, initial_capital)

    cash = float(initial_capital)
    positions: dict = {}
    equity_curve, curve_days = [], []
    legs: list = []
    total_cost = 0.0
    assignments = called_away = cycle_count = 0
    min_cash = cash
    peak = cash
    underwater_days = frozen_days = 0
    peak_deployed_fraction = 0.0
    max_concurrent_by_sector: dict = {}
    symbols_traded: set = set()

    if config.buy_and_hold:
        return _buy_and_hold(panels, config.tag, initial_capital, all_days, day_to_cycles)

    for day in all_days:
        today = day_to_cycles[day]

        # ---- 1. settle anything expiring today ---------------------------
        for symbol, position in list(positions.items()):
            leg = position.leg
            if leg is None or leg.expiry != day or symbol not in today:
                continue
            cycle, index = today[symbol]
            spot = float(cycle.spot[index])
            lot_size = panels[symbol].lot_size
            quantity = leg.lots * lot_size

            if leg.opt_type == "PE":
                if spot < leg.strike:
                    cost = _delivery_cost(leg.strike * quantity, "buy")
                    cash -= leg.strike * quantity + cost
                    total_cost += cost
                    position.shares += quantity
                    position.basis = (
                        leg.strike if position.basis is None
                        else (position.basis + leg.strike) / 2
                    )
                    position.state = "holding_shares"
                    assignments += 1
                else:
                    position.state = "flat"
            else:  # a call we wrote
                if spot > leg.strike and position.shares > 0:
                    sold = min(position.shares, quantity)
                    cost = _delivery_cost(leg.strike * sold, "sell")
                    cash += leg.strike * sold - cost
                    total_cost += cost
                    position.shares -= sold
                    called_away += 1
                    if position.shares == 0:
                        position.basis = None
                        position.state = "flat"
                    else:
                        position.state = "holding_shares"
                else:
                    position.state = "holding_shares"
            position.leg = None

        # ---- 2. decisions, only on a decision day ------------------------
        if day in cycles_by_decision:
            deciding = cycles_by_decision[day]
            regime = regime_by_day.get(day)

            ivs = {s: c.atm_iv for s, c in deciding.items() if c.atm_iv}
            percentiles = iv_percentile_ranks(ivs)
            eligible = eligible_candidates(percentiles, config.top_iv_frac)

            if config.headwind_filter:
                eligible = {
                    s for s in eligible
                    if not _is_headwind(deciding[s])
                }

            # 2a. holdings write a covered call; flat positions re-sell or rotate
            for symbol, position in list(positions.items()):
                if symbol not in deciding or position.leg is not None:
                    continue
                cycle = deciding[symbol]
                index = 0
                if position.state == "holding_shares":
                    written = _write_call(
                        config, position, cycle, index, panels[symbol].lot_size, regime,
                    )
                    if written is not None:
                        leg, credit, cost = written
                        cash += credit
                        total_cost += cost
                        position.leg, position.state = leg, "short_call"
                        legs.append(leg)
                elif position.state == "flat":
                    current = percentiles.get(symbol, 0.0)
                    challengers = {
                        s: p for s, p in percentiles.items()
                        if s in eligible and s != symbol and s not in positions
                    }
                    best = max(challengers.items(), key=lambda kv: kv[1]) if challengers else None
                    if best is not None and should_rotate(
                        current, best[1], config.rotation_hysteresis_pct
                    ):
                        del positions[symbol]     # frees the slot for 2b
                        continue
                    if symbol in eligible and not _gated(config, regime):
                        opened = _open_put(
                            config, symbol, cycle, index, panels[symbol].lot_size,
                            regime, cash, _reserved(positions, panels),
                            slots=1, capital=initial_capital,
                        )
                        if opened is not None:
                            leg, credit, cost = opened
                            cash += credit
                            total_cost += cost
                            position.leg, position.lots = leg, leg.lots
                            position.state = "short_put"
                            legs.append(leg)
                            cycle_count += 1
                    else:
                        del positions[symbol]

            # 2b. free capital goes to candidates not already held
            if not _gated(config, regime):
                available = [
                    s for s in eligible
                    if s not in positions and deciding[s].atm_iv
                ]
                tiebreak = None
                if config.relative_strength_tiebreak:
                    market = market_return_by_day.get(day, 0.0)
                    tiebreak = {
                        s: (deciding[s].trailing_return or 0.0) - market
                        for s in available
                    }
                if config.sector_round_robin:
                    order = round_robin_order(
                        available, percentiles, sector_by_symbol,
                        max_per_sector=config.max_per_sector, tiebreak=tiebreak,
                    )
                else:
                    order = flat_order(available, percentiles, tiebreak=tiebreak)

                order = _respect_concurrent_cap(order, positions, sector_by_symbol, config)
                if order:
                    for symbol in order:
                        cycle = deciding[symbol]
                        opened = _open_put(
                            config, symbol, cycle, 0, panels[symbol].lot_size,
                            regime, cash, _reserved(positions, panels),
                            slots=len(order), capital=initial_capital,
                        )
                        if opened is None:
                            continue
                        leg, credit, cost = opened
                        cash += credit
                        total_cost += cost
                        positions[symbol] = Position(
                            symbol=symbol, state="short_put", lots=leg.lots, leg=leg,
                        )
                        legs.append(leg)
                        cycle_count += 1
                        symbols_traded.add(symbol)

            live_sectors: dict = {}
            for symbol in positions:
                sector = sector_by_symbol.get(symbol, symbol)
                live_sectors[sector] = live_sectors.get(sector, 0) + 1
            for sector, count in live_sectors.items():
                max_concurrent_by_sector[sector] = max(
                    max_concurrent_by_sector.get(sector, 0), count
                )

        # ---- 3. mark to market -------------------------------------------
        equity = cash
        deployed = 0.0
        frozen = False
        for symbol, position in positions.items():
            if symbol not in today:
                continue
            cycle, index = today[symbol]
            spot = float(cycle.spot[index])
            lot_size = panels[symbol].lot_size
            equity += position.shares * spot
            deployed += position.shares * spot
            leg = position.leg
            if leg is not None:
                value = cycle.settle_of(leg.opt_type == "CE", index, leg.strike)
                equity -= value * leg.lots * lot_size
                if leg.opt_type == "PE":
                    deployed += leg.strike * leg.lots * lot_size
            if position.shares and position.basis and spot < position.basis:
                frozen = True

        equity_curve.append(equity)
        curve_days.append(day)
        min_cash = min(min_cash, cash)
        peak = max(peak, equity)
        if equity < peak:
            underwater_days += 1
        if frozen:
            frozen_days += 1
        if equity > 0:
            peak_deployed_fraction = max(peak_deployed_fraction, deployed / equity)

    deployed_by_symbol = {
        leg.symbol: leg.strike * leg.lots * panels[leg.symbol].lot_size for leg in legs
    }
    returns = [(b / a - 1) for a, b in zip(equity_curve, equity_curve[1:]) if a]
    years = max((all_days[-1] - all_days[0]) / 365.25, 1e-9)
    pnls = _leg_pnls(legs)
    pf = profit_factor(pnls)

    return BasketResult(
        tag=config.tag,
        initial_capital=initial_capital,
        final_equity=equity_curve[-1],
        equity_curve=equity_curve,
        cagr_pct=cagr_pct(initial_capital, equity_curve[-1], years),
        sharpe=sharpe_ratio(returns),
        max_drawdown_pct=max_drawdown_pct(equity_curve),
        win_rate_pct=win_rate_pct(pnls),
        profit_factor=None if pf == float("inf") else pf,
        cycles=cycle_count,
        assignments=assignments,
        called_away=called_away,
        total_cost=total_cost,
        min_cash=min_cash,
        max_sector_share=max_sector_share(deployed_by_symbol, sector_by_symbol),
        defence_share=defence_share(deployed_by_symbol, is_defence_by_symbol),
        peak_deployed_fraction=peak_deployed_fraction,
        time_underwater_pct=100.0 * underwater_days / max(len(equity_curve), 1),
        time_frozen_pct=100.0 * frozen_days / max(len(equity_curve), 1),
        max_concurrent_by_sector=max_concurrent_by_sector,
        symbols_traded=symbols_traded,
        legs=legs,
    )


def _is_headwind(cycle) -> bool:
    """T1: bearish MACD AND below its own SMA200. Both, not either -- a single
    weak signal across 210 names is mostly noise.
    """
    if cycle.sma200 is None:
        return False
    return (not cycle.macd_bullish) and float(cycle.spot[0]) < cycle.sma200


def _gated(config: BasketConfig, regime: Optional[str]) -> bool:
    return bool(config.regime_gate and regime in REGIME_GATE_BLOCKS)


def _deploy_fraction(config: BasketConfig, regime: Optional[str]) -> float:
    if not config.regime_sizing or regime is None:
        return 1.0
    return REGIME_DEPLOY_FRACTION.get(regime, 1.0)


def _reserved(positions: dict, panels: dict) -> float:
    """Cash a short put must actually be securing, and the cost of shares held."""
    total = 0.0
    for symbol, position in positions.items():
        leg = position.leg
        if leg is not None and leg.opt_type == "PE":
            total += leg.strike * leg.lots * panels[symbol].lot_size
    return total


def _respect_concurrent_cap(order, positions, sector_by_symbol, config):
    """A per-sector cap has to hold across CONCURRENT positions, not merely
    within one cycle's new entries -- otherwise three cycles in a row could
    each add one financial and the book still ends up concentrated.
    """
    if config.max_per_sector is None:
        return order
    held: dict = {}
    for symbol in positions:
        sector = sector_by_symbol.get(symbol, symbol)
        held[sector] = held.get(sector, 0) + 1
    out = []
    for symbol in order:
        sector = sector_by_symbol.get(symbol, symbol)
        if held.get(sector, 0) >= config.max_per_sector:
            continue
        held[sector] = held.get(sector, 0) + 1
        out.append(symbol)
    return out


def _open_put(config, symbol, cycle, index, lot_size, regime, cash, reserved,
              slots, capital):
    spot = float(cycle.spot[index])
    if spot <= 0:
        return None
    target_otm = config.put_otm
    if config.regime_strikes and regime is not None:
        target_otm = REGIME_PUT_OTM.get(regime, target_otm)
    ceiling = cycle.swing_low if config.support_strikes else None

    chain = cycle.tradeable(is_call=False, day_index=index)
    picked = _pick_put(chain, spot, target_otm, ceiling)
    if picked is None:
        return None
    strike, premium = picked

    free = (cash - reserved) * _deploy_fraction(config, regime)
    per_slot = free / max(slots, 1)
    notional = strike * lot_size
    if notional <= 0:
        return None
    lots = int(per_slot // notional)
    if lots < 1:
        return None

    fill = premium * (1 - PREMIUM_SLIPPAGE_PCT)
    turnover = fill * lots * lot_size
    cost = _option_cost(turnover, "sell")
    leg = Leg(symbol=symbol, opt_type="PE", strike=strike, premium=fill,
              lots=lots, expiry=int(cycle.expiry))
    return leg, turnover - cost, cost


def _write_call(config, position, cycle, index, lot_size, regime):
    spot = float(cycle.spot[index])
    assert position.basis is not None, "a holding must have an assignment basis"
    buffer_pct = (
        rsi_scaled_basis_buffer(cycle.rsi)
        if config.call_basis_buffer_rsi_scaled else 0.0
    )
    floor = position.basis * (1 + buffer_pct)
    target_otm = config.call_otm
    if config.regime_strikes and regime is not None:
        target_otm = REGIME_CALL_OTM.get(regime, target_otm)

    chain = cycle.tradeable(is_call=True, day_index=index)
    picked = _pick_call(chain, spot, target_otm, floor)
    if picked is None:
        # No strike clears the no-loss floor: held UNCOVERED this cycle. This
        # is the "frozen" outcome, recorded rather than quietly relaxed.
        return None
    strike, premium = picked
    lots = max(position.shares // lot_size, 0)
    if lots < 1:
        return None
    fill = premium * (1 - PREMIUM_SLIPPAGE_PCT)
    turnover = fill * lots * lot_size
    cost = _option_cost(turnover, "sell")
    leg = Leg(symbol=position.symbol, opt_type="CE", strike=strike, premium=fill,
              lots=lots, expiry=int(cycle.expiry), basis=position.basis)
    return leg, turnover - cost, cost


def _leg_pnls(legs) -> list:
    """Premium collected per leg. Assignment P&L lands on the equity curve
    rather than here -- a leg's own outcome is the credit it kept.
    """
    return [leg.premium * leg.lots for leg in legs]


def _buy_and_hold(panels, tag, initial_capital, all_days, day_to_cycles):
    """The mandatory control: own the identical universe over the identical
    window. Every post-mortem in this repo turns on a missing benchmark.
    """
    per_symbol = initial_capital / max(len(panels), 1)
    first_spot: dict = {}
    curve = []
    for day in all_days:
        equity = 0.0
        for symbol, panel in panels.items():
            entry = day_to_cycles[day].get(symbol)
            if entry is None:
                equity += first_spot.get(symbol, (None, per_symbol))[1]
                continue
            cycle, index = entry
            spot = float(cycle.spot[index])
            if symbol not in first_spot:
                first_spot[symbol] = (spot, per_symbol)
            base, _ = first_spot[symbol]
            equity += per_symbol * (spot / base if base else 1.0)
        curve.append(equity)
    returns = [(b / a - 1) for a, b in zip(curve, curve[1:]) if a]
    years = max((all_days[-1] - all_days[0]) / 365.25, 1e-9)
    return BasketResult(
        tag=tag, initial_capital=initial_capital, final_equity=curve[-1],
        equity_curve=curve, cagr_pct=cagr_pct(initial_capital, curve[-1], years),
        sharpe=sharpe_ratio(returns), max_drawdown_pct=max_drawdown_pct(curve),
        win_rate_pct=0.0, profit_factor=None, cycles=0, assignments=0,
        called_away=0, total_cost=0.0, min_cash=initial_capital,
        max_sector_share=0.0, defence_share=0.0, peak_deployed_fraction=1.0,
        time_underwater_pct=0.0, time_frozen_pct=0.0,
        max_concurrent_by_sector={}, symbols_traded=set(panels),
    )


def _empty_result(tag, initial_capital):
    return BasketResult(
        tag=tag, initial_capital=initial_capital, final_equity=initial_capital,
        equity_curve=[initial_capital], cagr_pct=0.0, sharpe=0.0,
        max_drawdown_pct=0.0, win_rate_pct=0.0, profit_factor=None, cycles=0,
        assignments=0, called_away=0, total_cost=0.0, min_cash=initial_capital,
        max_sector_share=0.0, defence_share=0.0, peak_deployed_fraction=0.0,
        time_underwater_pct=0.0, time_frozen_pct=0.0,
        max_concurrent_by_sector={}, symbols_traded=set(),
    )


__all__ = ["Leg", "Position", "BasketResult", "run_basket"]
