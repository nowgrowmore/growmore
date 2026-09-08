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
    """One symbol's wheel. `realized` accumulates the CASH outcome of the whole
    wheel -- premiums kept, shares bought, shares sold -- so that when the
    position finally closes there is a real trade P&L that can be negative.

    Scoring each written leg by its own premium would make every leg a winner
    by construction: a put assigned 40% underwater still "collected" its
    premium. The completed wheel is the only unit that can actually lose.
    """

    symbol: str
    state: str = "flat"          # flat | short_put | holding_shares | short_call
    basis: Optional[float] = None
    shares: int = 0
    lots: int = 0
    leg: Optional[Leg] = None
    realized: float = 0.0


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
    mean_sector_share: float
    defence_share: float
    peak_deployed_fraction: float
    peak_entry_exposure_fraction: float
    max_put_reserve_ratio: float
    time_underwater_pct: float
    time_frozen_pct: float
    max_concurrent_by_sector: dict
    max_concurrent_positions: int
    #: Wheels still open when the run ended. THIS is where the strategy's
    #: losses live. A wheel only COMPLETES by being called away, which happens
    #: above the assignment basis and is therefore always profitable -- so
    #: `win_rate_pct` over completed wheels is ~100% by construction and says
    #: nothing. An assignment that fell and never recovered simply never
    #: closes; it sits frozen, marked at spot, and shows up in drawdown and in
    #: `time_frozen_pct` instead. Read those, not the win rate.
    unfinished_positions: int
    symbols_traded: set
    closed_pnls: list = field(default_factory=list)
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
    underwater_days = 0
    frozen_fraction_sum = 0.0
    frozen_observations = 0
    peak_deployed_fraction = 0.0
    peak_entry_exposure_fraction = 0.0
    max_put_reserve_ratio = 0.0
    peak_gross_capital = 0.0
    weighted_sector_share = 0.0
    weighted_capital = 0.0
    best_sector_share = 0.0
    best_defence_share = 0.0
    closed_pnls: list = []
    max_concurrent_by_sector: dict = {}
    max_concurrent_positions = 0
    last_equity = float(initial_capital)
    symbols_traded: set = set()

    if config.buy_and_hold:
        return _buy_and_hold(panels, config.tag, initial_capital, all_days, day_to_cycles)

    for day in all_days:
        today = day_to_cycles[day]

        # ---- 1. settle anything expiring today ---------------------------
        finished: list = []
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
                    position.realized -= leg.strike * quantity + cost
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
                    position.realized += leg.strike * sold - cost
                    total_cost += cost
                    position.shares -= sold
                    called_away += 1
                    if position.shares == 0:
                        # The wheel is COMPLETE. Remove it rather than resetting
                        # it in place: a zombie left behind gets closed a second
                        # time on a later cycle and books a trade worth exactly
                        # zero, which is neither a win nor a loss and sent
                        # profit factor to infinity against a 78% win rate.
                        closed_pnls.append(position.realized)
                        finished.append(symbol)
                    else:
                        position.state = "holding_shares"
                else:
                    position.state = "holding_shares"
            position.leg = None
        for symbol in finished:
            positions.pop(symbol, None)

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
                        position.realized += credit
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
                        closed_pnls.append(position.realized)
                        del positions[symbol]     # frees the slot for 2b
                        continue
                    if symbol in eligible and not _gated(config, regime):
                        free = cash - _reserved(positions, panels)
                        opened = _open_put(
                            config, symbol, cycle, index, panels[symbol].lot_size,
                            regime, per_slot=free * _deploy_fraction(config, regime),
                            available_cash=free, fixed_lots=position.lots or None,
                        )
                        if opened is not None:
                            leg, credit, cost = opened
                            cash += credit
                            position.realized += credit
                            total_cost += cost
                            position.leg, position.lots = leg, leg.lots
                            position.state = "short_put"
                            legs.append(leg)
                            cycle_count += 1
                    else:
                        closed_pnls.append(position.realized)
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
                    # Fixed once, before any of this round's entries, so every
                    # candidate is offered the same budget.
                    budget = (cash - _reserved(positions, panels)) * _deploy_fraction(
                        config, regime
                    )
                    if config.target_positions is not None:
                        room = config.target_positions - len(positions)
                        order = order[:max(room, 0)]
                        # Equal weight against CURRENT equity, so position size
                        # compounds with the book instead of staying fixed --
                        # a constant lot count over seven years is escalating
                        # leverage, the bug that invalidated the first run of
                        # the F&O equity study.
                        budget = (
                            last_equity
                            * _deploy_fraction(config, regime)
                            * len(order) / config.target_positions
                        )
                    unopened = list(order)
                    while unopened:
                        # Everyone gets a fair share of what is still free,
                        # then leftovers are redistributed in another pass.
                        #
                        # The rejected alternative was sizing slot i as
                        # `free / remaining` inside a single pass. That grows
                        # each successive slot as earlier candidates underspend,
                        # so the END of the queue gets the most capital -- and
                        # since round-robin deliberately puts the crowded
                        # sector's 2nd and 3rd names last, the two mechanisms
                        # together CONCENTRATED the book (measured: mean sector
                        # share 0.52 against 0.29 for no round-robin at all),
                        # which is the exact opposite of the point.
                        free = cash - _reserved(positions, panels)
                        if free <= 0:
                            break
                        if config.target_positions is not None:
                            per_slot = min(budget / config.target_positions, free)
                        elif config.carryover_fill:
                            per_slot = (
                                free * _deploy_fraction(config, regime) / len(unopened)
                            )
                        else:
                            per_slot = budget / len(order)
                        still_unopened = []
                        opened_this_pass = 0
                        for symbol in unopened:
                            cycle = deciding[symbol]
                            opened = _open_put(
                                config, symbol, cycle, 0, panels[symbol].lot_size,
                                regime, per_slot=per_slot,
                                available_cash=cash - _reserved(positions, panels),
                            )
                            if opened is None:
                                still_unopened.append(symbol)
                                continue
                            leg, credit, cost = opened
                            cash += credit
                            total_cost += cost
                            positions[symbol] = Position(
                                symbol=symbol, state="short_put", lots=leg.lots,
                                leg=leg, realized=credit,
                            )
                            legs.append(leg)
                            cycle_count += 1
                            symbols_traded.add(symbol)
                            opened_this_pass += 1
                        if not config.carryover_fill or not opened_this_pass:
                            break
                        unopened = still_unopened

            live_sectors: dict = {}
            for symbol in positions:
                sector = sector_by_symbol.get(symbol, symbol)
                live_sectors[sector] = live_sectors.get(sector, 0) + 1
            max_concurrent_positions = max(max_concurrent_positions, len(positions))
            for sector, count in live_sectors.items():
                max_concurrent_by_sector[sector] = max(
                    max_concurrent_by_sector.get(sector, 0), count
                )

        # ---- 3. mark to market -------------------------------------------
        equity = cash
        deployed = 0.0
        frozen_capital = 0.0
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
                # Capital STUCK below its basis, not a book-level boolean. A
                # boolean saturates the moment more than a couple of positions
                # are open and stops telling configs apart.
                frozen_capital += position.shares * spot

        # Concentration is a statement about what is held AT ONCE, so it is
        # measured on the live book each day and kept as a running maximum.
        # Summing one leg per symbol over the whole run describes a portfolio
        # that existed on no single day, and can make a 1-per-sector cap look
        # MORE concentrated than no cap at all.
        live_capital = {}
        for symbol, position in positions.items():
            if symbol not in today:
                continue
            cycle, index = today[symbol]
            lot_size = panels[symbol].lot_size
            value = position.shares * float(cycle.spot[index])
            if position.leg is not None and position.leg.opt_type == "PE":
                value += position.leg.strike * position.leg.lots * lot_size
            if value > 0:
                live_capital[symbol] = value
        # Reported AT PEAK DEPLOYMENT, not as a max over all days. A plain
        # daily maximum is dominated by sparse days: whenever exactly one
        # position happens to be live, its sector share is trivially 100%, so
        # every config scores ~1.0 and the metric stops discriminating. The day
        # the book is most invested is the day concentration actually matters.
        gross = sum(live_capital.values())
        if live_capital and gross > 0:
            # Capital-weighted across every open day: the primary number, and
            # the one the study's risk claim rests on.
            weighted_sector_share += gross * max_sector_share(
                live_capital, sector_by_symbol
            )
            weighted_capital += gross
        if live_capital and gross > peak_gross_capital:
            peak_gross_capital = gross
            best_sector_share = max_sector_share(live_capital, sector_by_symbol)
            best_defence_share = defence_share(live_capital, is_defence_by_symbol)

        # The leverage invariant is about CASH, not equity: a cash-secured put
        # is secured by cash on hand. Equity nets off the option's own mark, so
        # exposure/equity sits a hair above 1.0 by construction and would fail
        # a leverage test that is not actually being violated.
        if cash > 0:
            put_reserve = _reserved(positions, panels)
            max_put_reserve_ratio = max(max_put_reserve_ratio, put_reserve / cash)
        if equity > 0:
            peak_entry_exposure_fraction = max(
                peak_entry_exposure_fraction, gross / equity
            )

        last_equity = equity
        equity_curve.append(equity)
        curve_days.append(day)
        min_cash = min(min_cash, cash)
        peak = max(peak, equity)
        if equity < peak:
            underwater_days += 1
        if deployed > 0:
            frozen_fraction_sum += frozen_capital / deployed
            frozen_observations += 1
        if equity > 0:
            peak_deployed_fraction = max(peak_deployed_fraction, deployed / equity)

    returns = [(b / a - 1) for a, b in zip(equity_curve, equity_curve[1:]) if a]
    years = max((all_days[-1] - all_days[0]) / 365.25, 1e-9)
    per_year = _per_year_returns(curve_days, equity_curve)
    # Positions still open at the end are excluded rather than marked and
    # counted: an unfinished wheel has no outcome yet, and forcing one would
    # book a paper loss as if it had been realised.
    pnls = closed_pnls
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
        max_sector_share=best_sector_share,
        mean_sector_share=(
            weighted_sector_share / weighted_capital if weighted_capital else 0.0
        ),
        defence_share=best_defence_share,
        peak_deployed_fraction=peak_deployed_fraction,
        peak_entry_exposure_fraction=peak_entry_exposure_fraction,
        max_put_reserve_ratio=max_put_reserve_ratio,
        time_underwater_pct=100.0 * underwater_days / max(len(equity_curve), 1),
        time_frozen_pct=(
            100.0 * frozen_fraction_sum / frozen_observations
            if frozen_observations else 0.0
        ),
        max_concurrent_by_sector=max_concurrent_by_sector,
        max_concurrent_positions=max_concurrent_positions,
        unfinished_positions=len(positions),
        symbols_traded=symbols_traded,
        closed_pnls=closed_pnls,
        legs=legs,
        per_year_return_pct=per_year,
    )


def _per_year_returns(days, curve) -> dict:
    """Calendar-year returns, for the "wins in at least 4 of 7 years" clause.

    A single aggregate CAGR can be one good year carrying six flat ones, which
    is what the per-year win rate in docs/stock-options-results.md Sec 7.1 was
    reporting alongside its edge and the reason it is repeated here.
    """
    import datetime

    by_year: dict = {}
    for day, equity in zip(days, curve):
        year = datetime.date(1970, 1, 1) + datetime.timedelta(days=int(day))
        by_year.setdefault(year.year, []).append(equity)
    return {
        year: round(100.0 * (values[-1] / values[0] - 1), 2)
        for year, values in sorted(by_year.items())
        if values and values[0]
    }


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


def _open_put(config, symbol, cycle, index, lot_size, regime, per_slot,
              available_cash, fixed_lots=None):
    """Open one cash-secured put, sized from a budget fixed BEFORE the round.

    `per_slot` is computed once per allocation round rather than re-derived
    per symbol. Re-dividing the REMAINING free capital by the FULL slot count
    on every iteration compounds: with four equal candidates the first took
    250 lots and the fourth got 3, which is a capital-allocation artefact
    masquerading as a concentration result.
    """
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

    notional = strike * lot_size
    if notional <= 0:
        return None
    if fixed_lots is not None:
        # A re-sell continues an EXISTING position, so it keeps its own size --
        # what `WheelBasketEngine._decide_put_rotation` does by writing the new
        # leg with `lots=position.lots`. Sizing it from the free pool instead
        # let the first position to re-sell each cycle swallow the entire
        # remaining budget.
        lots = fixed_lots
        if lots * notional > available_cash:
            return None
    else:
        lots = int(min(per_slot, available_cash) // notional)
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
        max_sector_share=0.0, mean_sector_share=0.0, defence_share=0.0,
        peak_deployed_fraction=1.0,
        peak_entry_exposure_fraction=1.0, max_put_reserve_ratio=0.0,
        time_underwater_pct=0.0, time_frozen_pct=0.0,
        max_concurrent_by_sector={}, max_concurrent_positions=len(panels), unfinished_positions=0,
        symbols_traded=set(panels), closed_pnls=[],
    )


def _empty_result(tag, initial_capital):
    return BasketResult(
        tag=tag, initial_capital=initial_capital, final_equity=initial_capital,
        equity_curve=[initial_capital], cagr_pct=0.0, sharpe=0.0,
        max_drawdown_pct=0.0, win_rate_pct=0.0, profit_factor=None, cycles=0,
        assignments=0, called_away=0, total_cost=0.0, min_cash=initial_capital,
        max_sector_share=0.0, mean_sector_share=0.0, defence_share=0.0,
        peak_deployed_fraction=0.0,
        peak_entry_exposure_fraction=0.0, max_put_reserve_ratio=0.0,
        time_underwater_pct=0.0, time_frozen_pct=0.0,
        max_concurrent_by_sector={}, max_concurrent_positions=0, unfinished_positions=0,
        symbols_traded=set(), closed_pnls=[],
    )


__all__ = ["Leg", "Position", "BasketResult", "run_basket"]
