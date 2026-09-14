"""Cycle-based state machine for the MCX Goldmini/Silvermini options-SELLING
strategy: sell OTM puts for premium, take assignment into a futures position
if the put finishes ITM, then sell covered calls against that futures
position until it is either called away or the option cycle ends.

Studied closely against `research.stock_options.wheel_engine` (see that
module's docstring for the template this mirrors: a `*Config` dataclass, a
per-cycle record type, a top-level `run(...)`), but the MECHANICS differ in
ways that matter:

  * Indian stock options are physically settled into SHARES; MCX commodity
    options settle into a FUTURES position, which itself has its own monthly
    expiry, independent of the option's expiry, and must be rolled if that
    contract expiry arrives before the option cycle resolves.
  * A futures position is marked to market EVERY trading day it is held, not
    only at cycle boundaries -- `wheel_engine`'s stock position is marked
    daily too, but never needs to roll.
  * There is no stop-loss anywhere in this state machine, by deliberate
    design (confirmed with the plan owner): a short leg is only ever closed
    by expiry (OTM), assignment (ITM put), or being called away (ITM call).
    Do not add early-exit logic here.

STATE MACHINE::

    FLAT ---- sell put, expires OTM ------------------> FLAT   (keep premium)
    FLAT ---- sell put, expires ITM  ------------------> LONG_FUTURES
                                                          (assigned @ strike)
    LONG_FUTURES - sell covered call (>= basis),
                   expires OTM ----------------------> LONG_FUTURES
                                                          (keep premium; the
                                                          futures leg accrues
                                                          daily M2M regardless;
                                                          rolled to the next
                                                          contract month if its
                                                          own expiry arrives
                                                          first -- see below)
    LONG_FUTURES - sell covered call, expires ITM -----> FLAT
                                                          (called away at the
                                                          call strike)

**Regime gating** (`EngineConfig.target_delta_for`): the day's
`RegimeLabel.for_option_type(...)` (from `regime.py`) decides whether a new
short is opened at all, and if so at what delta:
  - `Regime.CONSOLIDATING`   -> `consolidating_target_delta` (default 0.30)
  - `Regime.TREND_FAVORABLE` -> `trend_favorable_target_delta` (default 0.50)
  - `Regime.TREND_UNFAVORABLE`, or no label for the day at all -> skip entry
    entirely for that day/side. A missing day is "no opinion", never treated
    as permissive.

**Basis, defined precisely** (mirrors `wheel_engine.py`'s own convention: see
its `basis = k` line): the recorded `basis` on assignment is the RAW STRIKE
the put was assigned at, not premium-adjusted. The premium received for
selling that put is booked as its own cash P&L at entry, not netted into the
basis. This keeps "basis" meaning exactly what `call_at_or_above_basis`-style
floors need it to mean (a floor on future call strikes) without conflating
it with realised income, and keeps this module consistent with the only
other cycle engine in the repo.

**Futures daily mark-to-market**: the position enters at exactly the
assigned strike, so the day of assignment itself carries zero M2M (no gap
booked at entry). From the next trading day onward, every day held books
`(today's futures settle - previous mark) * qty` as P&L, using the FUTURES
price series in `futures_daily_bars` (assumed a continuous/back-adjusted
series spanning any roll -- see below). On the day the covered call goes
ITM, the exit price used for that final day's M2M is the CALL STRIKE (the
price the position actually closes at), not that day's raw futures settle.

**Futures contract rollover -- a flat cost/spread placeholder, not a real
roll-spread lookup.** If the assigned futures CONTRACT's own expiry (given
via `futures_contract_expiries`, a plain sorted list of contract-expiry
dates, independent of the option chain's expiries) arrives before the
covered-call cycle resolves, the position is rolled: marked to the roll
day's futures settle, closed and reopened at that same price (so the roll
itself has no PRICE effect on the continuous series assumed here), and
charged `futures_cost_model` round-trip leg costs on the roll notional plus
a flat `EngineConfig.futures_roll_cost_per_lot` -- a documented
simplification standing in for a real bid/ask roll spread, which is not
modelled. `futures_contract_expiries` is optional; omit it (or pass an empty
list) to assume the position never needs a rollover.

**Margin/capital -- a documented flat multiple, not SPAN.** Real MCX span
margin methodology is not modelled. `EngineConfig.margin_multiple_of_premium`
is a flat multiple of the premium just collected, recorded on each opening
leg purely for informational/reporting purposes (`LegRecord.margin_used`) --
it does NOT gate whether a trade is taken. This is a placeholder pending a
real SPAN-based margin model, stated plainly rather than pretended away.

**Costs**: `EngineConfig.option_cost_model` prices the two option legs
(selling the put, selling the call) on PREMIUM turnover, exactly the way
`wheel_engine.py` prices its option legs against `NSE_OPTION_COST_MODEL`.
`EngineConfig.futures_cost_model` prices the futures-side trades (entering
via assignment, exiting via being called away, and each roll) on
STRIKE/SETTLE * qty notional, the way `wheel_engine.py` prices its equity
delivery legs. Defaults: `futures_cost_model` defaults to
`growmore_bot.costs.DEFAULT_COST_MODEL`, which IS the real, reviewed MCX
commodity-FUTURES rate card -- the correct instrument for this leg.
`option_cost_model` defaults to `growmore_bot.costs.FREE_COST_MODEL` (charges
exactly zero) rather than to `MCX_COMMODITY_OPTION_COST_MODEL` (which raises
on use, being an unreviewed placeholder -- see `growmore_bot/costs.py`) or to
`NSE_OPTION_COST_MODEL` (the wrong instrument, equity options not commodity
options). `FREE_COST_MODEL` is unmistakably a zero-cost placeholder rather
than a plausible-looking guessed rate; callers who want real cost behaviour
must pass their own reviewed `CostModel` for the option leg.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

import pandas as pd

from growmore_bot.costs import DEFAULT_COST_MODEL, FREE_COST_MODEL, CostModel, leg_cost
from research.mcx_options.bhavcopy import MCXOptionRow
from research.mcx_options.regime import Regime, RegimeLabel
from research.mcx_options.strike_selection import select_strike_by_target_delta

#: Fallback realised-vol used when the caller supplies no per-day estimate
#: and a candidate strike's own implied vol cannot be solved either. Mirrors
#: `wheel_engine.PREMIUM_SLIPPAGE_PCT`'s role: a declared constant, not
#: swept.
DEFAULT_SIGMA = 0.20

#: Applied to premium on entry, same rationale as
#: `wheel_engine.PREMIUM_SLIPPAGE_PCT`: options slip on premium, not ticks.
PREMIUM_SLIPPAGE_PCT = 0.02


class State:
    FLAT = "flat"
    LONG_FUTURES = "long_futures"


@dataclass(frozen=True)
class EngineConfig:
    lot_size: int
    tick_size: float
    lots: int = 1

    #: Regime -> target delta mapping. A swept/comparable parameter per the
    #: approved plan, not hardcoded in the classification logic itself.
    consolidating_target_delta: float = 0.30
    trend_favorable_target_delta: float = 0.50

    min_open_interest: int = 0
    r: float = 0.0

    option_cost_model: CostModel = FREE_COST_MODEL
    futures_cost_model: CostModel = DEFAULT_COST_MODEL
    premium_slippage_pct: float = PREMIUM_SLIPPAGE_PCT

    #: Flat margin-multiple placeholder -- see module docstring. Reporting
    #: only; never gates a trade.
    margin_multiple_of_premium: float = 3.0

    #: Flat placeholder for a futures roll's bid/ask spread, rupees per lot.
    #: See module docstring -- NOT a real roll-spread lookup.
    futures_roll_cost_per_lot: float = 0.0

    def target_delta_for(self, regime: Regime) -> Optional[float]:
        """`None` means "skip entry entirely" -- TREND_UNFAVORABLE, or (via
        the caller never looking this up) no regime label for the day."""
        if regime == Regime.CONSOLIDATING:
            return self.consolidating_target_delta
        if regime == Regime.TREND_FAVORABLE:
            return self.trend_favorable_target_delta
        return None


@dataclass
class LegRecord:
    date: date
    action: str  # sell_put | assigned | sell_call | call_expired_otm |
                 # called_away | roll | put_expired_otm
    strike: Optional[float]
    pnl: float          # net cash effect of this leg (credit/debit, cost included)
    cost: float
    margin_used: float = 0.0
    note: str = ""


@dataclass
class CycleRecord:
    start_date: date
    end_date: Optional[date] = None
    #: "put_expired_otm" | "called_away" | "still_open" (run() ended mid-cycle)
    outcome: Optional[str] = None
    basis: Optional[float] = None
    legs: list[LegRecord] = field(default_factory=list)
    total_pnl: float = 0.0


@dataclass
class EngineResult:
    cycles: list[CycleRecord]
    days: list[date]
    daily_pnl: list[float]
    total_pnl: float
    #: Count of day/side entries skipped because the regime said
    #: TREND_UNFAVORABLE or carried no label at all.
    skipped_entries: int = 0


def _to_date(value) -> date:
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _next_contract_expiry(today: date, contract_expiries: Sequence[date]) -> Optional[date]:
    upcoming = [d for d in contract_expiries if d >= today]
    return min(upcoming) if upcoming else None


def _next_contract_after(today: date, contract_expiries: Sequence[date]) -> Optional[date]:
    later = [d for d in contract_expiries if d > today]
    return min(later) if later else None


def _option_leg_cost(premium_turnover: float, side: str, model: CostModel) -> float:
    return leg_cost(max(premium_turnover, 0.0), side, model)


def _futures_leg_cost(notional: float, side: str, model: CostModel) -> float:
    return leg_cost(max(notional, 0.0), side, model)


def run(
    config: EngineConfig,
    option_chain: Sequence[MCXOptionRow],
    futures_daily_bars: pd.DataFrame,
    regime_by_day: dict,
    futures_contract_expiries: Optional[Sequence[date]] = None,
    realised_vol_by_day: Optional[dict] = None,
) -> EngineResult:
    """Walk one underlying's futures/option history through the state
    machine described in the module docstring.

    `futures_daily_bars` must have a date-like index (one row per trading
    day, sorted or not -- it is sorted here) and a `close` column giving
    that day's futures settlement price, used both as the spot for strike
    selection and as the mark-to-market price. `option_chain` rows are
    matched to a day/expiry/side by (`trade_date`, `expiry`, `opt_type`).
    `regime_by_day` is expected to come straight from `regime.regime_by_day`
    (or an equivalent dict of `date -> RegimeLabel`); a day missing from it
    is treated as "no opinion" -- entry is skipped, never permitted.
    """
    contract_expiries = sorted(futures_contract_expiries or [])
    rv_by_day = realised_vol_by_day or {}

    bars = futures_daily_bars.copy()
    bars.index = [_to_date(i) for i in bars.index]
    bars = bars.sort_index()
    trading_days = list(bars.index)

    chain_index: dict[tuple, list[MCXOptionRow]] = {}
    expiries: set = set()
    for row in option_chain:
        key = (row.trade_date, row.expiry, row.opt_type)
        chain_index.setdefault(key, []).append(row)
        expiries.add(row.expiry)
    sorted_expiries = sorted(expiries)

    state = State.FLAT
    short: Optional[dict] = None
    futures_pos: Optional[dict] = None
    current_cycle: Optional[CycleRecord] = None
    handled_expiry: Optional[date] = None

    cycles: list[CycleRecord] = []
    daily_pnl: list[float] = []
    total_pnl = 0.0
    skipped_entries = 0

    def add_leg(leg: LegRecord, amount: float) -> float:
        nonlocal total_pnl
        assert current_cycle is not None
        current_cycle.legs.append(leg)
        current_cycle.total_pnl += amount
        total_pnl += amount
        return amount

    for day in trading_days:
        F = float(bars.loc[day, "close"])
        day_pnl = 0.0
        rolled_today = False

        # ---- 1. mid-cycle futures rollover -------------------------------
        if (
            futures_pos is not None
            and futures_pos["contract_expiry"] is not None
            and day >= futures_pos["contract_expiry"]
            and not (short is not None and short["expiry"] == day)
        ):
            mtm = (F - futures_pos["last_mark"]) * futures_pos["qty"]
            notional = F * futures_pos["qty"]
            roll_cost = (
                _futures_leg_cost(notional, "sell", config.futures_cost_model)
                + _futures_leg_cost(notional, "buy", config.futures_cost_model)
                + config.futures_roll_cost_per_lot * config.lots
            )
            leg_amount = mtm - roll_cost
            day_pnl += add_leg(
                LegRecord(day, "roll", None, leg_amount, roll_cost,
                          note=f"rolled from {futures_pos['contract_expiry']}"),
                leg_amount,
            )
            futures_pos["last_mark"] = F
            futures_pos["contract_expiry"] = _next_contract_after(day, contract_expiries)
            rolled_today = True

        # ---- 2. settle a short expiring today -----------------------------
        settled_futures_today = False
        assigned_today = False
        if short is not None and short["expiry"] == day:
            strike = short["strike"]
            qty = config.lots * config.lot_size
            if short["opt_type"] == "PE":
                if F >= strike:  # OTM: worthless, cycle closes FLAT
                    day_pnl += add_leg(
                        LegRecord(day, "put_expired_otm", strike, 0.0, 0.0), 0.0
                    )
                    current_cycle.end_date = day
                    current_cycle.outcome = "put_expired_otm"
                    cycles.append(current_cycle)
                    current_cycle = None
                    state = State.FLAT
                else:  # ITM: assigned into a futures position at the strike
                    cost = _futures_leg_cost(strike * qty, "buy", config.futures_cost_model)
                    day_pnl += add_leg(
                        LegRecord(day, "assigned", strike, -cost, cost,
                                  note="basis = strike, not premium-adjusted"),
                        -cost,
                    )
                    current_cycle.basis = strike
                    futures_pos = {
                        "basis": strike, "qty": qty, "last_mark": strike,
                        "contract_expiry": _next_contract_expiry(day, contract_expiries),
                    }
                    state = State.LONG_FUTURES
                    # No M2M today: entry is exactly at the strike, so the
                    # first mark happens from the NEXT trading day onward.
                    assigned_today = True
            else:  # a covered call we wrote
                if F <= strike:  # OTM: keep the futures position
                    day_pnl += add_leg(
                        LegRecord(day, "call_expired_otm", strike, 0.0, 0.0), 0.0
                    )
                else:  # ITM: called away, futures position closes at strike
                    qty = futures_pos["qty"]
                    exit_mtm = (strike - futures_pos["last_mark"]) * qty
                    cost = _futures_leg_cost(strike * qty, "sell", config.futures_cost_model)
                    leg_amount = exit_mtm - cost
                    day_pnl += add_leg(
                        LegRecord(day, "called_away", strike, leg_amount, cost), leg_amount
                    )
                    current_cycle.end_date = day
                    current_cycle.outcome = "called_away"
                    cycles.append(current_cycle)
                    current_cycle = None
                    futures_pos = None
                    state = State.FLAT
                    settled_futures_today = True
            short = None

        # ---- 3. ordinary daily mark-to-market ------------------------------
        if (
            futures_pos is not None
            and not rolled_today
            and not settled_futures_today
            and not assigned_today
        ):
            mtm = (F - futures_pos["last_mark"]) * futures_pos["qty"]
            day_pnl += add_leg(LegRecord(day, "mtm", None, mtm, 0.0), mtm)
            futures_pos["last_mark"] = F

        # ---- 4. open a new short if we have none ---------------------------
        if short is None:
            future_expiries = [e for e in sorted_expiries if e > day]
            if future_expiries and future_expiries[0] != handled_expiry:
                nxt = future_expiries[0]
                opt_type = "CE" if state == State.LONG_FUTURES else "PE"
                label: Optional[RegimeLabel] = regime_by_day.get(day)
                target_delta = (
                    config.target_delta_for(label.for_option_type(opt_type))
                    if label is not None else None
                )
                if target_delta is None:
                    skipped_entries += 1
                else:
                    candidates = chain_index.get((day, nxt, opt_type), [])
                    if state == State.LONG_FUTURES and futures_pos is not None:
                        candidates = [c for c in candidates if c.strike >= futures_pos["basis"]]
                    T_years = max((nxt - day).days, 0) / 365.25
                    sigma = rv_by_day.get(day, DEFAULT_SIGMA)
                    picked = select_strike_by_target_delta(
                        candidates, futures_price=F, T_years=T_years, sigma=sigma,
                        r=config.r, target_delta=target_delta,
                        min_open_interest=config.min_open_interest,
                    ) if candidates else None
                    if picked is not None:
                        handled_expiry = nxt
                        qty = config.lots * config.lot_size
                        premium = picked.close * (1 - config.premium_slippage_pct)
                        credit = premium * qty
                        cost = _option_leg_cost(credit, "sell", config.option_cost_model)
                        margin = config.margin_multiple_of_premium * credit
                        leg_amount = credit - cost
                        action = "sell_call" if opt_type == "CE" else "sell_put"
                        if opt_type == "PE":
                            current_cycle = CycleRecord(start_date=day)
                        day_pnl += add_leg(
                            LegRecord(day, action, picked.strike, leg_amount, cost,
                                      margin_used=margin),
                            leg_amount,
                        )
                        short = {
                            "opt_type": opt_type, "strike": picked.strike, "expiry": nxt,
                        }

        daily_pnl.append(day_pnl)

    if current_cycle is not None:
        current_cycle.outcome = "still_open"
        cycles.append(current_cycle)

    return EngineResult(
        cycles=cycles,
        days=trading_days,
        daily_pnl=daily_pnl,
        total_pnl=total_pnl,
        skipped_entries=skipped_entries,
    )


__all__ = [
    "State",
    "EngineConfig",
    "LegRecord",
    "CycleRecord",
    "EngineResult",
    "DEFAULT_SIGMA",
    "PREMIUM_SLIPPAGE_PCT",
    "run",
]
