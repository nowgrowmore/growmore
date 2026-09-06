"""A monthly stock-option engine: sell premium, take delivery, write calls.

`growmore_bot.backtest.engine.BacktestEngine` cannot run any of this. It
models one instrument, one position, one leg, no expiry, no settlement and no
assignment, and its P&L is `(exit - entry) * qty` -- correct only for a
symmetric linear instrument, which a short option is not. So this is a new
engine that shares only `growmore_bot.backtest.metrics` and the cost models.

WHAT MAKES THIS TRACTABLE is that Indian stock options are **physically
settled** (SEBI, fully effective from the October 2019 expiry). An ITM short
put delivers real shares at the strike, so the wheel is a literal strategy
rather than a synthetic one, and the covered call that follows is genuinely
covered.

THE STATE MACHINE, which is the whole engine:

    FLAT ---- sell put, expires OTM ----------------> FLAT   (keep premium)
    FLAT ---- sell put, expires ITM  ----------------> HOLDING (take delivery
                                                       at the strike; that
                                                       strike is the basis)
    HOLDING - sell call, expires OTM ---------------> HOLDING (keep premium,
                                                       write another)
    HOLDING - sell call, expires ITM ---------------> FLAT   (called away at
                                                       the call strike)

MARKED TO MARKET DAILY, and that is not negotiable. The "never realise a
loss" rule prevents *realising* a loss, not incurring one: assigned at 1,400
with the stock at 1,000, every strike at or above the basis is 40% away and
pays almost nothing, so the position sits frozen and underwater. Marking the
stock at its cost basis would draw a beautifully smooth equity curve that is
a lie. `time_underwater_pct` and `time_frozen_pct` are reported as
first-class metrics because that is where this strategy's risk actually
lives, and Sharpe hides it completely.

POSITION SIZE SCALES WITH EQUITY. A fixed lot count over seven years of
compounding is escalating leverage, which is the bug that invalidated the
first run of the F&O equity study -- and here it would be worse, because a
cash-secured put must actually hold `strike * lot` in cash. Lots are
therefore re-derived from current equity at every entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

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
from growmore_bot.options.strike_selection import (
    MIN_STRIKE_VOLUME,
    RSI_BASIS_BUFFER_TIERS,
    select_strike,
)
from growmore_bot.options.strike_selection import (
    rsi_scaled_basis_buffer as _rsi_scaled_basis_buffer,
)
from research.stock_options.pricing import implied_vol, option_delta

#: Fraction of the premium given up to the spread on entry. Options slip on
#: premium, not in ticks: Rs 0.10 of tick slip is 5bps on a Rs 200 premium and
#: 6.7% on a Rs 1.50 one, so the commodity tick model ranks strikes backwards.
PREMIUM_SLIPPAGE_PCT = 0.02


@dataclass(frozen=True)
class StrategyConfig:
    """One declared hypothesis. Nothing here is swept."""

    tag: str
    #: Target distance out of the money for the short leg, as a fraction.
    put_otm: float = 0.05
    call_otm: float = 0.05
    #: Take delivery and write covered calls (the wheel), or liquidate the
    #: delivered shares immediately. Note there is no third option: physical
    #: settlement means assignment CANNOT be declined, so "sell puts and never
    #: hold stock" only exists if you sell what you are given.
    wheels: bool = True
    #: Write calls only at strikes >= the assignment basis (the "no loss" rule).
    call_at_or_above_basis: bool = True
    #: Own the stock throughout and only ever write calls (buy-write).
    always_long: bool = False
    #: Buy a protective put this far OTM as well (put credit spread).
    long_put_otm: Optional[float] = None
    #: Choose the strike by richest IV-vs-realised-vol instead of fixed OTM.
    dynamic_strike: bool = False
    #: Refuse to write a call whose premium is below this fraction of spot.
    #:
    #: Targets the defect the first run exposed: with the no-loss rule the
    #: position sits below its basis a median 53% of all days, and a call
    #: struck at or above basis is then far out of the money and pays almost
    #: nothing -- while still capping the recovery that would end the
    #: predicament. Writing it trades all of the upside for none of the
    #: income. Above this floor the call is worth having; below it, holding
    #: uncovered is strictly better.
    min_call_premium_pct: float = 0.0
    #: Skip writing the call while the trend rule says the stock is running.
    #: The F&O equity study found trend rules only helped on stocks where
    #: holding LOST money; a covered call has the same shape, since it costs
    #: you exactly when the stock runs. So the signal that failed as a
    #: directional rule is tested here as a filter on where to sell upside.
    trend_conditioned: bool = False
    #: Raise the no-loss floor to basis * (1 + this), instead of exactly
    #: basis. A fixed override; ignored when `call_basis_buffer_rsi_scaled`.
    call_basis_buffer_pct: float = 0.0
    #: Size the basis buffer off the stock's own RSI at the moment the call
    #: is written (see RSI_BASIS_BUFFER_TIERS) instead of a flat percentage.
    #: Only as large as the stock's current momentum justifies; falls back to
    #: exactly G's rule (0% buffer) when RSI shows no real strength, rather
    #: than chasing upside that is not there.
    call_basis_buffer_rsi_scaled: bool = False


@dataclass
class CycleRecord:
    expiry: date
    action: str                 # "sell_put" | "sell_call" | "hold" | "buy_write"
    strike: Optional[float]
    premium: float
    lots: int
    assigned: bool
    called_away: bool
    pnl: float


@dataclass
class WheelResult:
    symbol: str
    tag: str
    cycles: int
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    profit_factor: Optional[float]
    win_rate_pct: float
    final_equity: float
    initial_capital: float
    total_cost: float
    assignment_rate_pct: float
    time_underwater_pct: float
    time_frozen_pct: float
    equity_curve: list[float] = field(default_factory=list)
    #: Trading dates aligned 1:1 with `equity_curve`, so the curve can be
    #: sliced by period without re-running the engine.
    days: list = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    records: list[CycleRecord] = field(default_factory=list)


HEADER = (
    f"{'symbol':<13}{'strategy':<30}{'cycles':>7}{'CAGR':>8}{'Sharpe':>7}"
    f"{'MaxDD':>8}{'assign%':>8}{'undrwtr%':>9}"
)


def as_row(r: "WheelResult") -> str:
    return (
        f"{r.symbol:<13}{r.tag:<30}{r.cycles:>7}{r.cagr_pct:>7.1f}%{r.sharpe:>7.2f}"
        f"{r.max_drawdown_pct:>7.1f}%{r.assignment_rate_pct:>7.1f}%"
        f"{r.time_underwater_pct:>8.1f}%"
    )


def monthly_expiries(chain: pd.DataFrame) -> list[pd.Timestamp]:
    """Every expiry present, in order. Stock options are monthly only."""
    return sorted(pd.to_datetime(chain["expiry"]).unique())


#: D will not write closer to the money than this delta, so "richest premium"
#: cannot simply collapse to the at-the-money strike where premium is always
#: largest. It is a risk cap, not a tuned parameter.
MAX_SHORT_DELTA = 0.35


def select_strike_by_iv_richness(
    day_chain: pd.DataFrame,
    opt_type: str,
    spot: float,
    years: float,
    stock_realised_vol: float,
    floor_strike: Optional[float] = None,
) -> Optional[pd.Series]:
    """Strategy D's strike: where implied vol stands furthest above realised.

    This is the variance risk premium measured strike by strike, rather than
    a fixed distance out of the money. The delta cap stops it drifting to the
    money; the realised-vol reference is the stock's own, so a chronically
    volatile name is not flattered for simply being volatile.

    Returns None when nothing clears the cap or no strike yields a usable
    implied vol -- a real outcome, recorded as a skipped month.
    """
    side = day_chain[
        (day_chain["opt_type"] == opt_type)
        & (day_chain["volume"] >= MIN_STRIKE_VOLUME)
        & (day_chain["settle"] > 0)
    ]
    if side.empty or years <= 0:
        return None
    if floor_strike is not None:
        side = side[side["strike"] >= floor_strike]
        if side.empty:
            return None

    best = None
    best_score = float("-inf")
    for _, row in side.iterrows():
        strike = float(row["strike"])
        price = float(row["settle"])
        iv = implied_vol(price, spot, strike, years, opt_type)
        if iv is None:
            continue
        if abs(option_delta(spot, strike, years, iv, opt_type)) > MAX_SHORT_DELTA:
            continue
        score = iv - stock_realised_vol
        if score > best_score:
            best_score, best = score, row
    return best


def _option_cost(premium_turnover: float, side: str) -> float:
    """Charged on PREMIUM turnover, never on strike x lot."""
    return leg_cost(max(premium_turnover, 0.0), side, NSE_OPTION_COST_MODEL)


def _delivery_cost(delivery_value: float, side: str) -> float:
    """Assignment is an equity delivery trade and is taxed as one."""
    return leg_cost(max(delivery_value, 0.0), side, NSE_EQUITY_DELIVERY_COST_MODEL)


def run_wheel(
    symbol: str,
    chain: pd.DataFrame,
    config: StrategyConfig,
    initial_capital: float = 1_000_000.0,
    realised_vol_by_day: Optional[dict] = None,
    trend_bullish_by_day: Optional[dict] = None,
    rsi_by_day: Optional[dict] = None,
) -> Optional[WheelResult]:
    """Walk one stock's monthly option history through the state machine.

    Returns None when the stock has too little option history to measure --
    "unmeasured", not "weak", which is the treatment
    docs/walk-forward-results.md gave the short-history MCX contracts.
    """
    chain = chain.copy()
    chain["trade_date"] = pd.to_datetime(chain["trade_date"])
    chain["expiry"] = pd.to_datetime(chain["expiry"])
    expiries = monthly_expiries(chain)
    if len(expiries) < 6:
        return None

    # Spot per trading day, from the option rows themselves (unadjusted, and
    # identical across every strike of a given day).
    spot_by_day = (
        chain.groupby("trade_date")["underlying"].first().sort_index()
    )
    days = list(spot_by_day.index)
    if not days:
        return None

    cash = float(initial_capital)
    shares = 0
    basis: Optional[float] = None          # assignment price per share
    lot_size = int(chain["lot_size"].replace(0, pd.NA).dropna().median() or 0)
    if lot_size <= 0:
        return None

    short: Optional[dict] = None           # the written option
    long_put: Optional[dict] = None        # protective leg, strategy F only
    #: The expiry we have already acted on. Without this, a month in which we
    #: deliberately write nothing (strategy E's trend skip) re-decides every
    #: single day, producing one record per DAY instead of one per month --
    #: 210 cycles where there were 7, and every per-cycle statistic wrong.
    handled_expiry = None
    equity_curve: list[float] = []
    curve_days: list = []
    records: list[CycleRecord] = []
    total_cost = 0.0
    peak = float(initial_capital)
    underwater_days = frozen_days = 0

    by_day = {d: g for d, g in chain.groupby("trade_date")}
    expiry_set = {pd.Timestamp(e) for e in expiries}

    for day in days:
        spot = float(spot_by_day.loc[day])
        if spot <= 0:
            continue
        day_chain = by_day[day]

        # ---- settle anything expiring today -------------------------------
        if day in expiry_set and short is not None and short["expiry"] == day:
            k = short["strike"]
            qty = short["lots"] * lot_size
            if short["opt_type"] == "PE":
                if spot < k:
                    # Assigned: real shares delivered at the strike.
                    cost = _delivery_cost(k * qty, "buy")
                    cash -= k * qty + cost
                    total_cost += cost
                    shares += qty
                    basis = k if basis is None else (basis + k) / 2
                    records[-1].assigned = True
            else:  # a call we wrote
                if spot > k and shares > 0:
                    sold = min(shares, qty)
                    cost = _delivery_cost(k * sold, "sell")
                    cash += k * sold - cost
                    total_cost += cost
                    shares -= sold
                    if shares == 0:
                        basis = None
                    records[-1].called_away = True
            short = None

        # The protective leg settles on its own terms, not nested inside the
        # short's block: if the two ever came apart, a long put left open here
        # would be marked forever and never pay out.
        if long_put is not None and long_put["expiry"] == day:
            lk = long_put["strike"]
            lqty = long_put["lots"] * lot_size
            if spot < lk and shares > 0:
                # Deep enough to bite: exercise, selling the delivered shares
                # at the long strike. This is what caps strategy F's loss at
                # the width of the spread however far the stock has fallen.
                sold = min(shares, lqty)
                cost = _delivery_cost(lk * sold, "sell")
                cash += lk * sold - cost
                total_cost += cost
                shares -= sold
                if shares == 0:
                    basis = None
            long_put = None

        # ---- open a position for the next expiry --------------------------
        if short is None:
            future = [e for e in expiries if e > day]
            if future and future[0] != handled_expiry:
                nxt = future[0]
                nxt_chain = day_chain[day_chain["expiry"] == nxt]
                if not nxt_chain.empty:
                    rv = 0.0
                    if realised_vol_by_day is not None:
                        rv = float(realised_vol_by_day.get(day, 0.0))
                    bullish = False
                    if trend_bullish_by_day is not None:
                        bullish = bool(trend_bullish_by_day.get(day, False))
                    rsi = None
                    if rsi_by_day is not None:
                        rsi = rsi_by_day.get(day)
                    opened = _open_position(
                        config, nxt, nxt_chain, spot, cash, shares, basis,
                        lot_size, realised_vol=rv, today=day, trend_bullish=bullish,
                        rsi=rsi,
                    )
                    if opened is not None:
                        # A None short is a deliberate skip, not a failure;
                        # either way this expiry is now decided. When
                        # `_open_position` returns None outright no strike was
                        # tradeable, and retrying tomorrow is the right move.
                        handled_expiry = nxt
                        short = opened["short"]
                        long_put = opened.get("long_put")
                        cash += opened["cash_delta"]
                        total_cost += opened["cost"]
                        if opened.get("bought_shares"):
                            shares += opened["bought_shares"]
                            basis = opened["basis"]
                        records.append(opened["record"])

        # ---- mark to market ----------------------------------------------
        short_value = _mark(short, day_chain, spot) + _mark(
            long_put, day_chain, spot, sign=-1
        )
        equity = cash + shares * spot - short_value
        equity_curve.append(equity)
        curve_days.append(day)
        peak = max(peak, equity)
        if equity < peak:
            underwater_days += 1
        if shares > 0 and basis is not None and spot < basis:
            frozen_days += 1

    if len(equity_curve) < 30:
        return None

    returns = [(b / a - 1) for a, b in zip(equity_curve, equity_curve[1:]) if a != 0]
    years = max((days[-1] - days[0]).days / 365.25, 0.0)
    pnls = [r.pnl for r in records if r.pnl]
    pf = profit_factor(pnls)
    assigned = sum(1 for r in records if r.assigned)
    puts_written = sum(1 for r in records if r.action == "sell_put") or 1

    return WheelResult(
        symbol=symbol,
        tag=config.tag,
        cycles=len(records),
        cagr_pct=cagr_pct(initial_capital, equity_curve[-1], years) if years else 0.0,
        sharpe=sharpe_ratio(returns),
        max_drawdown_pct=max_drawdown_pct(equity_curve),
        profit_factor=None if pf == float("inf") else pf,
        win_rate_pct=win_rate_pct(pnls),
        final_equity=equity_curve[-1],
        initial_capital=initial_capital,
        total_cost=total_cost,
        assignment_rate_pct=100.0 * assigned / puts_written,
        time_underwater_pct=100.0 * underwater_days / len(equity_curve),
        time_frozen_pct=100.0 * frozen_days / len(equity_curve),
        equity_curve=equity_curve,
        days=curve_days,
        returns=returns,
        records=records,
    )


def _open_position(
    config: StrategyConfig,
    expiry: pd.Timestamp,
    day_chain: pd.DataFrame,
    spot: float,
    cash: float,
    shares: int,
    basis: Optional[float],
    lot_size: int,
    realised_vol: float = 0.0,
    today: Optional[pd.Timestamp] = None,
    trend_bullish: bool = False,
    rsi: Optional[float] = None,
) -> Optional[dict]:
    """Write the next month's option, sizing to CURRENT equity.

    Returns None when no tradeable strike exists -- a real outcome in a thin
    month, recorded as a skipped cycle rather than a fabricated fill.

    Which option gets written is the whole difference between the six
    strategies, and it collapses to two questions: are we holding stock
    (write a call) or not (write a put), and is the call floored at the
    assignment basis.
    """
    holding = shares > 0
    years = 0.0
    if today is not None:
        years = max((expiry - today).days, 0) / 365.25

    if (holding or config.always_long) and config.trend_conditioned and trend_bullish:
        # The stock is running. Writing a call here caps exactly the move the
        # position exists to capture, so hold it uncovered this month and
        # forgo the premium. Recorded as a real decision, not a skipped cycle.
        #
        # The filter suppresses the CALL, never the stock: a buy-write that
        # skipped its first purchase because the trend was up would sit in
        # cash forever and measure nothing.
        skip = {
            "short": None, "cash_delta": 0.0, "cost": 0.0,
            "record": CycleRecord(
                expiry=expiry.date(), action="hold_uncovered", strike=None,
                premium=0.0, lots=max(1, shares // lot_size), assigned=False,
                called_away=False, pnl=0.0,
            ),
        }
        if config.always_long and shares == 0:
            lots = max(1, int(cash // (spot * lot_size)))
            buy_qty = lots * lot_size
            buy_cost = _delivery_cost(spot * buy_qty, "buy")
            skip["cash_delta"] = -(spot * buy_qty + buy_cost)
            skip["cost"] = buy_cost
            skip["bought_shares"] = buy_qty
            skip["basis"] = spot
            skip["record"].lots = lots
        return skip

    if holding or config.always_long:
        buffer_pct = (
            _rsi_scaled_basis_buffer(rsi) if config.call_basis_buffer_rsi_scaled
            else config.call_basis_buffer_pct
        )
        floor = (
            basis * (1 + buffer_pct)
            if (config.call_at_or_above_basis and basis is not None) else None
        )
        if config.dynamic_strike and realised_vol > 0:
            row = select_strike_by_iv_richness(
                day_chain, "CE", spot, years, realised_vol, floor_strike=floor
            )
        else:
            row = select_strike(day_chain, "CE", spot, config.call_otm, floor_strike=floor)
        if row is None and floor is not None:
            # The "never below basis" rule can leave nothing sellable at all.
            # That IS the constraint biting, and it is recorded as a frozen
            # month rather than quietly relaxed.
            return None
        if row is None:
            return None
        if config.min_call_premium_pct > 0 and spot > 0 and (
            float(row["settle"]) < config.min_call_premium_pct * spot
        ):
            # Too cheap to be worth the upside it forfeits. Hold the stock
            # uncovered this month rather than capping a recovery in exchange
            # for a premium that rounds to nothing.
            return {
                "short": None, "cash_delta": 0.0, "cost": 0.0,
                "record": CycleRecord(
                    expiry=expiry.date(), action="hold_uncovered", strike=None,
                    premium=0.0, lots=max(1, shares // lot_size), assigned=False,
                    called_away=False, pnl=0.0,
                ),
            }
        # Buy-write starting flat sizes its stock purchase to equity; a
        # covered call on an existing holding is sized by the holding.
        lots = (
            max(1, shares // lot_size)
            if holding
            else max(1, int(cash // (spot * lot_size)))
        )
        opt_type = "CE"
        action = "sell_call"
    else:
        if config.dynamic_strike and realised_vol > 0:
            row = select_strike_by_iv_richness(day_chain, "PE", spot, years, realised_vol)
        else:
            row = select_strike(day_chain, "PE", spot, config.put_otm)
        if row is None:
            return None
        strike = float(row["strike"])
        # Cash-secured: one lot needs strike * lot_size of cash behind it.
        # Sizing to current equity keeps leverage at ~1x for seven years
        # instead of drifting as the stock compounds.
        lots = max(1, int(cash // (strike * lot_size)))
        opt_type = "PE"
        action = "sell_put"

    strike = float(row["strike"])
    premium = float(row["settle"]) * (1 - PREMIUM_SLIPPAGE_PCT)
    qty = lots * lot_size
    credit = premium * qty
    cost = _option_cost(credit, "sell")

    result = {
        "short": {
            "strike": strike, "expiry": expiry, "opt_type": opt_type,
            "premium": premium, "lots": lots, "lot_size": lot_size,
        },
        "cash_delta": credit - cost,
        "cost": cost,
        "record": CycleRecord(
            expiry=expiry.date(), action=action, strike=strike, premium=credit,
            lots=lots, assigned=False, called_away=False, pnl=credit - cost,
        ),
    }

    # Strategy F: buy the protective put underneath the one we just sold.
    if opt_type == "PE" and config.long_put_otm is not None:
        prot = select_strike(day_chain, "PE", spot, config.long_put_otm)
        if prot is not None and float(prot["strike"]) < strike:
            debit_price = float(prot["settle"]) * (1 + PREMIUM_SLIPPAGE_PCT)
            debit = debit_price * qty
            prot_cost = _option_cost(debit, "buy")
            result["long_put"] = {
                "strike": float(prot["strike"]), "expiry": expiry, "opt_type": "PE",
                "premium": debit_price, "lots": lots, "lot_size": lot_size,
            }
            result["cash_delta"] -= debit + prot_cost
            result["cost"] += prot_cost
            result["record"].pnl -= debit + prot_cost

    # Strategy C: buy-write starts by owning the stock outright.
    if config.always_long and shares == 0:
        buy_qty = lots * lot_size
        buy_cost = _delivery_cost(spot * buy_qty, "buy")
        result["cash_delta"] -= spot * buy_qty + buy_cost
        result["cost"] += buy_cost
        result["bought_shares"] = buy_qty
        result["basis"] = spot
        result["record"].action = "buy_write"

    return result


def _mark(
    leg: Optional[dict], day_chain: pd.DataFrame, spot: float, sign: int = 1
) -> float:
    """Current value of an open option leg, at today's settlement price.

    Strikes are matched on nearest-within-tolerance rather than exact float
    equality. Two things defeat equality: prices are rescaled into adjusted
    space by a factor that CHANGES at a corporate action, so a leg opened
    before one carries a strike the post-action chain no longer contains; and
    float equality on a product of two floats is fragile regardless.

    When the strike genuinely is not quoted that day -- an illiquid contract
    that simply did not print -- the fallback is INTRINSIC value, not the
    entry premium. A stale entry price would hold the liability frozen at
    what it was worth a month ago, hiding exactly the move that matters.
    """
    if leg is None:
        return 0.0
    quantity = leg["lots"] * leg["lot_size"]
    same = day_chain[
        (day_chain["expiry"] == leg["expiry"])
        & (day_chain["opt_type"] == leg["opt_type"])
    ]
    price = None
    if not same.empty:
        gap = (same["strike"] - leg["strike"]).abs()
        nearest = gap.idxmin()
        if float(gap.loc[nearest]) <= max(0.005 * leg["strike"], 0.01):
            price = float(same.loc[nearest, "settle"])
    if price is None:
        strike = leg["strike"]
        price = max(
            (spot - strike) if leg["opt_type"] == "CE" else (strike - spot), 0.0
        )
    return sign * price * quantity


__all__ = [
    "StrategyConfig", "CycleRecord", "WheelResult", "HEADER", "as_row",
    "monthly_expiries", "select_strike", "select_strike_by_iv_richness",
    "run_wheel", "STRATEGIES", "MAX_SHORT_DELTA", "PREMIUM_SLIPPAGE_PCT",
    "MIN_STRIKE_VOLUME", "cagr_pct", "max_drawdown_pct", "profit_factor",
    "sharpe_ratio", "win_rate_pct", "RSI_BASIS_BUFFER_TIERS",
]


#: The six declared hypotheses. Nothing here is swept -- the strike rule is
#: fixed at 5% for every strategy so the matched-pair comparisons stay clean,
#: and D is the only one permitted to choose its own strike.
STRATEGIES: list[StrategyConfig] = [
    StrategyConfig(tag="A-wheel-constrained", call_at_or_above_basis=True),
    StrategyConfig(tag="B-wheel-unconstrained", call_at_or_above_basis=False),
    StrategyConfig(tag="C-buy-write", always_long=True, call_at_or_above_basis=False),
    StrategyConfig(tag="D-iv-rich-dynamic", call_at_or_above_basis=False,
                   dynamic_strike=True),
    StrategyConfig(tag="E-trend-conditioned-calls", call_at_or_above_basis=False,
                   always_long=True, trend_conditioned=True),
    StrategyConfig(tag="F-put-credit-spread", call_at_or_above_basis=False,
                   long_put_otm=0.10),

    # --- ATM family. G is the owner's own manually-traded strategy: sell the
    # at-the-money put for the coming month, and if assigned write calls at or
    # above the assignment price. H and I each differ from G by exactly ONE
    # decision, so whatever they gain or lose is attributable.
    StrategyConfig(tag="G-atm-wheel", put_otm=0.0, call_otm=0.0,
                   call_at_or_above_basis=True),
    StrategyConfig(tag="H-atm-wheel-min-premium", put_otm=0.0, call_otm=0.0,
                   call_at_or_above_basis=True, min_call_premium_pct=0.005),
    StrategyConfig(tag="I-atm-wheel-trend-skip", put_otm=0.0, call_otm=0.0,
                   call_at_or_above_basis=True, trend_conditioned=True),
    # L differs from G by exactly one decision too: how far above basis the
    # post-assignment call is struck, sized by the stock's own RSI rather
    # than a flat percentage (see RSI_BASIS_BUFFER_TIERS).
    StrategyConfig(tag="L-atm-wheel-rsi-buffer", put_otm=0.0, call_otm=0.0,
                   call_at_or_above_basis=True, call_basis_buffer_rsi_scaled=True),
]
