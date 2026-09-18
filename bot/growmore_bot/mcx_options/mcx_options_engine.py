"""The MCX Goldmini/Silvermini options-selling live paper-trading decision
engine.

Architectural template: `growmore_bot/wheel_basket/wheel_basket_engine.py`
(a pure, DB-session-taking, already-fetched-market-data-taking `run_cycle`
function -- see that module's own docstring for why fetching is kept out of
the decision logic). Studied closely against the offline backtest state
machine this mirrors, `research/mcx_options/engine.py` (see that module's
docstring for the full state machine and every documented simplification:
flat cost/margin placeholders, no stop-loss, futures roll cost model), but
NOT imported from -- this repo's one-directional research -> growmore_bot
convention (growmore_bot/wheel_basket/universe.py's own comment) means the
live version is freshly written, adapted for a single day's decision rather
than a full historical walk.

STATE MACHINE (same shape as research/mcx_options/engine.py's module
docstring; `MCXOptionsPosition.state` is `flat|long_futures|closed` --
unlike wheel_basket, it tracks futures exposure, not "what leg is currently
outstanding": a short put sold against no futures position is still `flat`)::

    (no position) -- sell put ------------------------> flat (short put out)
    flat -- put expires OTM ---------------------------> position CLOSED,
                                                          state stays "flat"
    flat -- put expires ITM ---------------------------> long_futures
                                                          (assigned @ strike,
                                                          basis = strike),
                                                          covered call
                                                          written same day
    long_futures -- call expires OTM ------------------> long_futures
                                                          (keep futures, new
                                                          covered call
                                                          written same day)
    long_futures -- call expires ITM ------------------> position CLOSED,
                                                          state "closed"
                                                          (called away)

No stop-loss anywhere, by deliberate design (matches the offline backtest):
a leg only ever resolves via expiry/assignment/being-called-away. (A
flag-gated, default-OFF stop-loss is proposed in docs/pending-actions.md --
until the account owner enables it, this remains literally true.)

**Settlement is due on OR AFTER the leg's expiry, never only ON it.** The
check is `open_leg.cycle_expiry <= today`. Exact equality (what this used to
do) meant a single missed cycle -- the VPS down or restarting at 23:59, an
MCX partial-session holiday absent from `market_hours.MCX_HOLIDAYS_2026`, or
a `DhanApiError` that exhausted `scheduler_job`'s retries -- left the leg
unsettled FOREVER: that date never came round again, so `entry_needed`
stayed False on every later cycle and the commodity silently stopped trading
with nothing but a log line to show for it. A late settlement is logged as
such, because the ITM/OTM call is then made against today's futures price
rather than the expiry day's (found by independent code review, 2026-09-15).

**Moneyness guards on both sides.** `_floor_chain_for_covered_call` keeps a
covered call at or above the position's basis; `_cap_chain_for_short_put`
keeps a short put strictly below the futures price. The target-delta picker
ranks purely on |delta| and has no notion of moneyness, so at
`trend_favorable_target_delta = 0.50` (an ATM delta) it would otherwise
happily sell a put at or through the futures price -- a near-certain
assignment booked as premium income.

**One cycle_date is one decision.** Every code path records its outcome via
`_record_selection`, which REPLACES that day's `MCXOptionsSelection` row
rather than appending another. Three production cycles were hand-run on
2026-09-14 (19:13, 20:42, 23:59 IST) and each left its own row for the same
date, so the dashboard showed that day three times with three different
"why" strings. Migration 0026 adds the matching `UNIQUE (config_id,
cycle_date)`.

**Regime gating**: `growmore_bot.mcx_options.regime.classify_today` is
called directly on `cycle_data.futures_bars` (a pure function of already-
fetched data -- no Dhan mock plumbing needed here, same as wheel_basket_engine
calling `iv_percentile_ranks` directly). `Regime.TREND_UNFAVORABLE`, or no
regime label at all, skips entry entirely for that side/day -- never
permissive. `Regime.CONSOLIDATING`/`TREND_FAVORABLE` map to
`config.consolidating_target_delta`/`trend_favorable_target_delta`.

**Daily futures mark-to-market -- a documented simplification, not a true
day-by-day incremental ledger.** `MCXOptionsPosition` (unlike the offline
backtest's in-memory state) has no persisted "last mark" column, so this
engine cannot book `(today's close - yesterday's mark) * qty` the way
`research/mcx_options/engine.py` does. Instead, every cycle a `long_futures`
position is held, `unrealized_pnl` is recomputed fresh as
`(futures_price - basis) * futures_qty` -- mathematically the same total
unrealized P&L at any point in time, just without a per-day M2M event
ledger. This is called out explicitly rather than silently doing nothing
(PaperPosition.realized_pnl/unrealized_pnl similarly aren't updated by
`wheel_basket_engine.py` at all -- this at least keeps `unrealized_pnl`
live).

`_mark_to_market` is called ONCE per cycle, unconditionally, for whatever
position exists -- it is a no-op for any non-`long_futures` state, so every
code path below can share the one call. It used to live only on the "a leg
is live and not due yet" branch, which meant `unrealized_pnl` (and the
`position_unrealized_pnl` snapshot written to `MCXOptionsSelection`) went
stale on exactly the cycles that matter most: the cycle a put was ASSIGNED
(the futures position opens already underwater by `(F - strike) * qty`, and
that read as 0), and any cycle where a new covered call was skipped by
regime or by the strike filters. Found by independent code review,
2026-09-15.

**Futures contract rollover -- implemented by reusing the existing
Instrument-level rollover mechanism, not by re-deriving next-contract logic
here.** `growmore_bot.scheduler.contract_rollover.roll_to_next_contract`
already keeps `Instrument.contract_expiry` current (called earlier in the
same tick, gated by `is_past_close_out_cutoff` -- see `run.py`). On
assignment, `_settle_leg`'s PE-ITM branch now records the CURRENT front-month
contract on `MCXOptionsPosition.futures_contract_expiry` (from
`cycle_data.instrument_contract_expiry`, populated by `live_data.
fetch_cycle_data` straight off `Instrument.contract_expiry`). Every cycle a
`long_futures` position is held, `run_cycle` compares that stored value
against `cycle_data.instrument_contract_expiry` (the FRESH read this cycle):
a mismatch means the Instrument's own contract has already rolled out from
under this position, and `_roll_futures_position` executes the position's
own roll --

  1. Mark-to-market the OLD exposure: `mtm = (F - old_basis) * futures_qty`
     (the same formula `_mark_to_market` uses), where `F` is today's futures
     price (already the NEW contract's price, since the Instrument's
     `security_id` was already rolled this tick -- there is no old-contract
     quote left to mark against, which is also why this matches
     `research/mcx_options/engine.py`'s own "close and reopen at the same
     price" framing: F stands in for both the old contract's exit print and
     the new contract's entry print).
  2. Charge a round-trip futures leg cost on that notional
     (`growmore_bot.costs.leg_cost`, `DEFAULT_COST_MODEL` -- the real,
     reviewed MCX-futures rate card, same model
     `research/mcx_options/engine.py` defaults `futures_cost_model` to) plus
     the flat `config.futures_roll_cost_per_lot` placeholder.
  3. **Basis semantics (this is the crux of correctness here).**
     `position.basis` is not a per-day mark -- it is the reference price
     `unrealized_pnl` is computed fresh against every cycle
     (`(F - basis) * qty`, see `_mark_to_market`). If a roll simply reset
     `basis = F` without first crystallizing `mtm` above, the entire
     price move accrued since assignment/last roll would silently vanish
     from every future P&L reading -- neither realized nor unrealized would
     ever reflect it again. So `mtm` is added to `position.realized_pnl`
     (crystallizing it -- the roll is the event that locks it in, exactly
     as `research/mcx_options/engine.py`'s own roll adds `mtm - roll_cost`
     into its running `total_pnl`, since that offline model has no separate
     realized/unrealized split at all), the roll cost is subtracted from
     `realized_pnl` too, and ONLY THEN is `basis` reset to `F`. This keeps
     total P&L (realized + unrealized) conserved across the roll, net of
     the actual transaction cost paid -- no gain is created or destroyed by
     the roll itself, only cost. `position.futures_contract_expiry` is then
     advanced to `cycle_data.instrument_contract_expiry`.
  4. A `MCXOptionsLeg` row records the event: `opt_type="ROLL"`,
     `strike`/`premium` both `None` (a roll is not an option leg -- see
     migration 0023, which is what made these columns nullable),
     `action="roll"`, `cycle_expiry` = the NEW contract's expiry (a
     documented repurposing of that column -- see the model's own
     docstring), and `pnl` = the roll cost alone (negated) -- the leg
     row's `pnl` is the TRANSACTION COST booked, not the mtm gain/loss
     (which lands in `position.realized_pnl` as described above, not on
     this leg row).

A roll happening on the same cycle as a leg's own due-today settlement does
not prevent that settlement (or a fresh entry) from also running: the roll
is checked and executed FIRST (see `run_cycle`), then the ordinary
settle/decide logic proceeds using the now-current contract/basis.

**Option premium booking -- credited at entry, never at settlement.** Selling
an option is a real cash credit the moment it happens, exactly as
`research/mcx_options/engine.py`'s `add_leg` books it: `credit = premium *
qty`, `cost = leg_cost(credit, "sell", FREE_COST_MODEL)` (`FREE_COST_MODEL`,
not `MCX_COMMODITY_OPTION_COST_MODEL` -- see `growmore_bot/costs.py`'s own
docstring for why the latter raises rather than pretending a plausible rate
is known), `leg_amount = credit - cost`. `run_cycle`'s "entered" code path
adds `leg_amount` into `active_position.realized_pnl` immediately, for BOTH
a fresh `sell_put` and a covered `sell_call` -- before the leg even has a
chance to settle. This mirrors real trading: the premium is yours whether
the option later expires worthless, gets assigned, or is called away.
`MCXOptionsLeg.pnl` is deliberately left `None` at entry -- it represents
the leg's fully-RESOLVED outcome once settled, not the entry credit; the
raw `premium` column already shows what an open leg collected (see the
dashboard's trade-history table, which shows "P&L: --" for an open leg and
falls back to `premium` elsewhere).

`_settle_leg` recomputes that same `leg_amount` (via `_entry_leg_amount`,
from `leg.premium`/`leg.lots`, mathematically identical to the entry-time
value since neither changes) purely to populate `leg.pnl` once the leg
settles -- informational, NOT a second booking into `realized_pnl`, since
that already happened at entry. On top of that, two branches book a
genuinely NEW realized event at settlement, separate from the option
premium entirely:
  - `assigned` (PE ITM): the futures BUY-IN at the strike is a real new
    debit -- `leg_cost(strike * qty, "buy", DEFAULT_COST_MODEL)` (the real,
    reviewed MCX-futures rate card, same one `_roll_futures_position` uses
    for the futures leg) -- subtracted from `realized_pnl`.
  - `called_away` (CE ITM): the futures position's move from `basis` to the
    call `strike` is crystallized -- `exit_mtm = (strike - basis) * qty`
    minus `leg_cost(strike * qty, "sell", DEFAULT_COST_MODEL)` -- added to
    `realized_pnl`. `unrealized_pnl` is still zeroed (the position is
    closing, so it carries no more unrealized exposure), but the gain is no
    longer simply discarded the way it was before this accounting was
    added: it is captured as realized P&L instead.

**No PaperOrder/DhanOrderClient/PaperTradingEngine anywhere.** Like
wheel_basket, this is a self-contained ledger writing
`MCXOptionsPosition`/`MCXOptionsLeg`/`MCXOptionsSelection` rows directly.
MCX options order placement does not exist in this codebase and must not be
attempted -- `growmore_bot.mcx_options.live_data` only ever calls
`DhanClient`'s read-only Data API methods.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from growmore_bot.broker.dhan_client import OptionChainSnapshot
from growmore_bot.costs import DEFAULT_COST_MODEL, FREE_COST_MODEL, leg_cost
from growmore_bot.mcx_options import regime as regime_module
from growmore_bot.mcx_options.ladder import select_put_ladder
from growmore_bot.mcx_options.live_data import MCXCycleData
from growmore_bot.mcx_options.pricing import realised_vol_from_bars
from growmore_bot.mcx_options.regime import Regime
from growmore_bot.mcx_options.strike_selection import (
    evaluate_candidates,
    select_strike_by_target_delta,
)
from growmore_bot.persistence.models import MCXOptionsLeg, MCXOptionsPosition, MCXOptionsSelection
from growmore_bot.scheduler.market_hours import is_first_mcx_trading_day_of_week

logger = logging.getLogger(__name__)


class MCXOptionsStateError(RuntimeError):
    """A persisted invariant this engine relies on has been violated -- e.g.
    two `status="open"` positions for one config, or two unsettled legs on one
    position.

    Raised rather than silently working around, because every "work around
    it" option is worse: `.first()` on a duplicate open position orphans the
    other one forever (never settled, never marked to market), and a bare
    `MultipleResultsFound` from `.one_or_none()` reaches
    `scheduler_job.run_mcx_options_configs`'s blanket `except Exception` as an
    anonymous traceback that silently skips that commodity EVERY day
    thereafter. A named error at least says what is wrong in the log line the
    operator eventually reads.

    Migration 0026 adds the partial unique indexes that make these states
    unreachable going forward; this stays as the belt to that braces, and as
    the thing that catches a state which predates the migration.
    """


#: Fallback vol used when a candidate strike's own quoted IV is missing --
#: see strike_selection.py's per-strike fallback discipline. Matches
#: research/mcx_options/engine.py's DEFAULT_SIGMA (a declared constant, not
#: swept).
DEFAULT_SIGMA = 0.20

#: Flat placeholder risk-free rate, matching research/mcx_options/engine.py's
#: EngineConfig.r default -- MCX options are cheap enough (weeks to expiry)
#: that this has negligible pricing effect either way.
RISK_FREE_RATE = 0.0


def _entry_leg_amount(premium: float, lots: float, lot_size: int) -> float:
    """The net premium credit an option leg earned at the moment it was
    written -- `credit = premium * qty`, `cost = leg_cost(credit, "sell",
    FREE_COST_MODEL)`, `leg_amount = credit - cost`, exactly matching
    `research/mcx_options/engine.py`'s `add_leg` formula for `sell_put`/
    `sell_call` (see that module's docstring). Recomputed here from
    `leg.premium`/`leg.lots` rather than stored on the leg row at entry time
    -- mathematically identical, since neither input changes between entry
    and settlement, and avoids a fourth schema migration for this feature.
    Used both to book `realized_pnl` at entry and to populate the settled
    leg's own informational `pnl` column later (see module docstring's
    "Option premium booking" section).
    """
    qty = float(lots) * lot_size
    credit = premium * qty
    cost = leg_cost(credit, "sell", FREE_COST_MODEL)
    return credit - cost


def _settle_leg(
    position: MCXOptionsPosition, leg: MCXOptionsLeg, cycle_data: MCXCycleData, now: datetime,
) -> None:
    """Mutates `leg`/`position` in place per the state machine described in
    the module docstring -- mirrors wheel_basket_engine._settle_leg's
    mutate-the-existing-row convention (never creates a second leg row for
    the same cycle's outcome).
    """
    F = cycle_data.futures_price
    qty = float(leg.lots) * cycle_data.lot_size
    leg.settled_at = now

    # `strike`/`premium` are nullable at the schema level only to accommodate
    # a "roll" leg (migration 0023) -- this function only ever settles a
    # genuine PE/CE option leg (run_cycle never calls it on a roll leg),
    # which always has both, so this narrows the types back down for the
    # comparisons below. Raised, not `assert`ed: `python -O` strips asserts,
    # and without them a None strike would reach `F < leg.strike` and fail
    # somewhere far less legible.
    if leg.strike is None:
        raise MCXOptionsStateError(
            f"MCXOptionsLeg {leg.id} (opt_type={leg.opt_type!r}) is being settled but has no "
            "strike -- only a ROLL leg may have a null strike, and ROLL legs are never settled"
        )
    if leg.premium is None:
        raise MCXOptionsStateError(
            f"MCXOptionsLeg {leg.id} (opt_type={leg.opt_type!r}) is being settled but has no "
            "premium -- only a ROLL leg may have a null premium, and ROLL legs are never settled"
        )

    # The premium this leg earned when it was WRITTEN was already credited
    # into `position.realized_pnl` at entry time (see run_cycle's "entered"
    # code path) -- never re-book it here. `leg.pnl` is set to that same
    # figure purely so the settled leg's own row shows a resolved outcome
    # instead of staying blank (the dashboard trade-history table renders
    # `leg.pnl`, not `leg.premium`, once a leg is settled).
    entry_leg_amount = _entry_leg_amount(float(leg.premium), leg.lots, cycle_data.lot_size)
    leg.pnl = entry_leg_amount

    if leg.opt_type == "PE":
        if F < leg.strike:  # ITM: assigned into a futures position at the strike
            leg.action = "assigned"
            leg.assigned = True
            position.state = "long_futures"
            position.basis = leg.strike
            position.futures_qty = qty
            # The CURRENT front-month contract as of assignment day -- kept
            # fresh going forward by the Instrument-level rollover mechanism
            # (see module docstring); run_cycle compares this against a
            # fresh read of it every cycle to detect when a roll is due.
            position.futures_contract_expiry = cycle_data.instrument_contract_expiry
            # A SEPARATE, genuinely new realized cost: buying into the
            # futures position at the strike -- not a duplicate of the
            # premium credit above (which is the option leg's own income,
            # already booked at entry and unaffected by assignment).
            assignment_cost = leg_cost(float(leg.strike) * qty, "buy", DEFAULT_COST_MODEL)
            position.realized_pnl = float(position.realized_pnl) - assignment_cost
        else:  # OTM: worthless, position closes
            leg.action = "put_expired_otm"
            position.status = "closed"
            position.state = "flat"
            position.closed_at = now
    else:  # CE -- a covered call written against an existing futures position
        if F > leg.strike:  # ITM: called away, futures position closes at strike
            leg.action = "called_away"
            leg.called_away = True
            position.status = "closed"
            position.state = "closed"
            position.closed_at = now
            position.unrealized_pnl = 0
            # Crystallize the futures gain from basis to the call strike --
            # this is what the position's daily M2M would have shown had it
            # been marked at F=strike, now locked in for good since the
            # position is closing. A SEPARATE realized event from the call's
            # own premium credit (already booked at entry, above).
            if position.basis is not None:
                exit_mtm = (float(leg.strike) - float(position.basis)) * qty
                exit_cost = leg_cost(float(leg.strike) * qty, "sell", DEFAULT_COST_MODEL)
                position.realized_pnl = float(position.realized_pnl) + exit_mtm - exit_cost
            # The futures are gone -- they were sold at the call strike. Left
            # non-zero, this closed position kept reporting phantom exposure
            # that the dashboard's current-position table reads straight off
            # the row (found by independent code review, 2026-09-15).
            position.futures_qty = 0
        else:  # OTM: keep the futures position, mark it to market
            leg.action = "call_expired_otm"
            if position.basis is not None:
                position.unrealized_pnl = (F - float(position.basis)) * float(position.futures_qty)


def _mark_to_market(position: MCXOptionsPosition, cycle_data: MCXCycleData) -> None:
    """Simplified daily M2M -- see module docstring's documented
    simplification (no persisted "last mark" column exists)."""
    if position.state == "long_futures" and position.basis is not None:
        position.unrealized_pnl = (
            (cycle_data.futures_price - float(position.basis)) * float(position.futures_qty)
        )


def _roll_futures_position(
    session: Any, config: Any, position: MCXOptionsPosition, cycle_data: MCXCycleData, now: datetime,
) -> None:
    """Executes a mid-cycle futures contract roll for `position` -- called
    when `run_cycle` detects `position.futures_contract_expiry` has fallen
    behind `cycle_data.instrument_contract_expiry` (the Instrument-level
    rollover mechanism has already advanced the underlying contract). See
    the module docstring's "Futures contract rollover" section for the full
    reasoning behind the basis/cost semantics used here.
    """
    F = cycle_data.futures_price
    qty = float(position.futures_qty)
    old_basis = float(position.basis) if position.basis is not None else F

    # 1. Crystallize the exposure accrued against the OLD basis -- this is
    # exactly `_mark_to_market`'s formula, but booked into `realized_pnl`
    # (not `unrealized_pnl`) because resetting `basis` below would otherwise
    # silently erase it from every future P&L reading.
    mtm = (F - old_basis) * qty

    # 2. Round-trip futures leg cost on the roll notional, plus the flat
    # per-lot placeholder -- a REALIZED transaction cost, unlike the mtm
    # above which is just relabeled from unrealized to realized.
    notional = abs(F * qty)
    roll_cost = (
        leg_cost(notional, "sell", DEFAULT_COST_MODEL)
        + leg_cost(notional, "buy", DEFAULT_COST_MODEL)
        + float(config.futures_roll_cost_per_lot) * float(config.lots)
    )

    position.realized_pnl = float(position.realized_pnl) + mtm - roll_cost

    # 3. Reopen at today's price -- the roll itself has no price effect
    # (see module docstring). unrealized_pnl is recomputed fresh next mark;
    # setting it to 0 here keeps it consistent with the new basis in the
    # meantime (F - new_basis == 0).
    new_expiry = cycle_data.instrument_contract_expiry
    assert new_expiry is not None  # guarded by the caller before invoking this
    position.basis = F
    position.futures_contract_expiry = new_expiry
    position.unrealized_pnl = 0.0

    lots = qty / cycle_data.lot_size if cycle_data.lot_size else 0.0
    session.add(
        MCXOptionsLeg(
            id=uuid.uuid4(), position_id=position.id, cycle_expiry=new_expiry,
            opt_type="ROLL", strike=None, premium=None, lots=lots, action="roll",
            opened_at=now, settled_at=now, pnl=-roll_cost,
        )
    )


def _floor_chain_for_covered_call(
    chain: OptionChainSnapshot, floor_strike: float
) -> OptionChainSnapshot:
    """A covered call must never be written below the position's basis
    (mirrors wheel_basket_engine's `floor_strike` on `_pick_strike` for its
    own covered call) -- drops CE rows below the floor; leaves every other
    row untouched.
    """
    return replace(
        chain,
        rows=[r for r in chain.rows if not (r.opt_type == "CE" and r.strike < floor_strike)],
    )


def _effective_sigma(config: Any) -> float:
    """The vol to fall back on for a strike whose own quoted IV is missing or
    implausible -- `config.fallback_sigma` when set, else `DEFAULT_SIGMA`.

    `DEFAULT_SIGMA` is one hard-coded 0.20 for both GOLDM and SILVERM, and
    silver's realised vol is materially higher than gold's. Default-OFF
    (migration 0027): unset means the module constant, exactly as before.
    """
    raw = getattr(config, "fallback_sigma", None)
    return DEFAULT_SIGMA if raw is None else float(raw)


def _premium_collected(session: Any, position: MCXOptionsPosition, lot_size: int) -> float:
    """Total option premium written against `position` so far, gross and IN
    RUPEES -- the denominator `stop_loss_premium_multiple` is expressed in.

    `lot_size` matters and is not optional: `leg.premium` is quoted PER UNIT,
    while `position.unrealized_pnl` (what this is compared against) is rupee
    cash. Dropping it would make the stop threshold `lot_size` times too
    tight -- 100x for GOLDM -- which in practice means stopping out of
    essentially every position on its first adverse tick.

    Summed from the position's own legs rather than read off `realized_pnl`,
    which also carries assignment/roll/exit COSTS and would make the
    threshold drift with them.
    """
    legs = session.query(MCXOptionsLeg).filter_by(position_id=position.id).all()
    return sum(
        float(leg.premium) * float(leg.lots) * lot_size
        for leg in legs
        if leg.premium is not None and leg.opt_type in ("PE", "CE")
    )


def _stop_out_position(
    session: Any,
    config: Any,
    position: MCXOptionsPosition,
    cycle_data: MCXCycleData,
    now: datetime,
) -> float:
    """Flatten a long-futures position at today's price, crystallizing the
    loss net of a real futures exit cost, and record a `STOP` leg.

    Only ever called when `config.stop_loss_premium_multiple` is set -- the
    strategy is stop-less by default and by design (see the module
    docstring). Mirrors `_settle_leg`'s called-away branch: the move from
    `basis` to the exit price is booked into `realized_pnl`, `unrealized_pnl`
    is zeroed because the position carries no more exposure, and
    `futures_qty` is cleared so nothing reads phantom exposure off a closed
    row.
    """
    F = cycle_data.futures_price
    qty = float(position.futures_qty)
    basis = float(position.basis) if position.basis is not None else F

    exit_mtm = (F - basis) * qty
    exit_cost = leg_cost(abs(F * qty), "sell", DEFAULT_COST_MODEL)
    position.realized_pnl = float(position.realized_pnl) + exit_mtm - exit_cost
    position.unrealized_pnl = 0.0
    position.futures_qty = 0
    position.status = "closed"
    position.state = "closed"
    position.closed_at = now

    lots = qty / cycle_data.lot_size if cycle_data.lot_size else 0.0
    session.add(
        MCXOptionsLeg(
            id=uuid.uuid4(), position_id=position.id, cycle_expiry=cycle_data.option_expiry,
            opt_type="STOP", strike=None, premium=None, lots=lots, action="stop_loss",
            opened_at=now, settled_at=now, pnl=exit_mtm - exit_cost,
        )
    )
    return exit_mtm - exit_cost


def _cap_chain_for_short_put(
    chain: OptionChainSnapshot, futures_price: float
) -> OptionChainSnapshot:
    """A short put must be genuinely OUT of the money -- drops PE rows at or
    above `futures_price`; leaves every other row untouched.

    The mirror image of `_floor_chain_for_covered_call`, and it exists for
    the same reason: the target-delta picker ranks purely on |delta| and has
    no notion of moneyness, so at `trend_favorable_target_delta = 0.50` (an
    ATM delta) it could and would pick a strike at or through the futures
    price. Selling an ITM put is not premium income -- it is buying the
    underlying at a worse price and calling the difference a credit. The
    offline backtest (`research/mcx_options/strike_selection.py`) has no such
    guard either; it never bit there because bhavcopy chains are dense and
    symmetric around spot, which a live Dhan chain need not be.
    """
    return replace(
        chain,
        rows=[r for r in chain.rows if not (r.opt_type == "PE" and r.strike >= futures_price)],
    )


def _position_snapshot(position: Optional[MCXOptionsPosition]) -> dict:
    """The (state, basis, unrealized_pnl) fields `MCXOptionsSelection`
    records every cycle -- a snapshot of `position`'s CURRENT values (post
    whatever this cycle's `_settle_leg`/`_mark_to_market`/
    `_roll_futures_position` already did to it), so a hold-day row is
    self-contained without a join back through position history (which only
    tracks CURRENT state, not day-by-day history). `None` for every field
    when no position exists at all yet.
    """
    if position is None:
        return {"position_state": None, "position_basis": None, "position_unrealized_pnl": None}
    return {
        "position_state": position.state,
        "position_basis": position.basis,
        "position_unrealized_pnl": position.unrealized_pnl,
    }


#: Selection rows written by the EVENING settlement phase use negative
#: `attempt_seq` values, the morning entry phase positive ones. Both phases
#: run on the same `cycle_date`, so they need disjoint, DETERMINISTIC
#: sequences: deterministic so re-running a phase corrects its own rows in
#: place rather than stacking duplicates (what migration 0026's uniqueness was
#: really protecting), and disjoint so the two phases never collide on the
#: `(config_id, cycle_date, attempt_seq)` key. 0 is left unused.
SETTLE_SEQ_SIGN = -1
ENTRY_SEQ_SIGN = 1


def _record_selection(
    session: Any,
    config: Any,
    today: date,
    cycle_data: MCXCycleData,
    *,
    attempt_seq: int,
    reason: str,
    position: Optional[MCXOptionsPosition],
    regime: Optional[str] = None,
    target_delta: Optional[float] = None,
    selected_strike: Optional[float] = None,
    candidates_considered: Optional[list] = None,
    option_expiry: Optional[date] = None,
) -> None:
    """Write EXACTLY ONE `MCXOptionsSelection` row per (config, cycle_date),
    replacing that day's row in place if the cycle is re-run.

    One cycle_date is one decision. Three production cycles were run by hand
    on 2026-09-14 (19:13, 20:42 and 23:59 IST) and each appended its own row
    for the same date, so the dashboard's selection log showed the same day
    three times with three different "why" strings and no way to tell which
    one the engine actually acted on. A re-run is a CORRECTION of that day's
    decision, not an additional one.

    Migration 0026 adds `UNIQUE (config_id, cycle_date)` to enforce this at
    the database level; this function is what keeps the engine from tripping
    that constraint, and it also collapses any duplicate rows that predate
    the migration.

    Every column is written on every call (falling back to the defaults in
    this signature) so a re-run can never leave a stale value from the
    earlier run's code path sitting in an unrelated column.
    """
    fields = dict(
        regime=regime,
        target_delta=target_delta,
        selected_strike=selected_strike,
        reason=reason,
        futures_price=cycle_data.futures_price,
        option_expiry=option_expiry if option_expiry is not None else cycle_data.option_expiry,
        candidates_considered=candidates_considered,
        position_id=position.id if position is not None else None,
        **_position_snapshot(position),
    )

    existing = (
        session.query(MCXOptionsSelection)
        .filter_by(config_id=config.id, cycle_date=today, attempt_seq=attempt_seq)
        .all()
    )
    if existing:
        row = existing[0]
        for key, value in fields.items():
            setattr(row, key, value)
        for duplicate in existing[1:]:
            logger.warning(
                "mcx_options: collapsing a duplicate MCXOptionsSelection row for "
                "config_id=%s cycle_date=%s attempt_seq=%s",
                config.id,
                today,
                attempt_seq,
            )
            session.delete(duplicate)
        return

    session.add(
        MCXOptionsSelection(
            id=uuid.uuid4(),
            config_id=config.id,
            cycle_date=today,
            attempt_seq=attempt_seq,
            **fields,
        )
    )


def _open_positions_for(session: Any, config: Any) -> list[MCXOptionsPosition]:
    """Every open position for `config`, oldest first.

    Migration 0028 removed 0026's "one open position per config" partial
    unique index: the weekly ladder deliberately holds several concurrent
    short puts, each running its own put -> assignment -> covered-call chain.
    This fans out the way `wheel_basket_engine.run_cycle` always has, rather
    than raising on the second one.

    The PER-POSITION invariant is unchanged and still enforced, in the
    database and in `_open_leg_for`: one unsettled leg per position.
    """
    return (
        session.query(MCXOptionsPosition)
        .filter_by(config_id=config.id, status="open")
        .order_by(MCXOptionsPosition.opened_at.asc())
        .all()
    )


def _ist_week_start(today: date) -> date:
    """The Monday of `today`'s week -- the key the weekly new-put target is
    counted against (`MCXOptionsPosition.entry_week_start`).

    `today` is already an IST calendar date everywhere this engine is called
    (`scheduler.run` passes `datetime.now(MCX_TIMEZONE).date()`), which is
    exactly why the week start is stored rather than derived from the UTC
    `opened_at` timestamp at query time.
    """
    return today - timedelta(days=today.weekday())


def _open_leg_for(session: Any, position: MCXOptionsPosition) -> Optional[MCXOptionsLeg]:
    """The position's single unsettled leg, or None. Raises rather than
    letting `.one_or_none()`'s anonymous `MultipleResultsFound` reach the
    scheduler's blanket handler -- see `MCXOptionsStateError`.
    """
    open_legs = (
        session.query(MCXOptionsLeg).filter_by(position_id=position.id, settled_at=None).all()
    )
    if len(open_legs) > 1:
        raise MCXOptionsStateError(
            f"position_id={position.id} has {len(open_legs)} unsettled legs: "
            f"{[str(leg.id) for leg in open_legs]}. Exactly one is expected -- this engine "
            "writes at most one leg per position at a time."
        )
    return open_legs[0] if open_legs else None


def _maybe_roll(
    session: Any, config: Any, position: MCXOptionsPosition, cycle_data: MCXCycleData,
    now: datetime,
) -> bool:
    """Roll a held futures position if the Instrument's own contract has moved
    on underneath it. Idempotent -- a no-op once the two expiries agree, which
    is why both the morning and evening phases can safely call it.
    """
    if (
        position.state == "long_futures"
        and position.futures_contract_expiry is not None
        and cycle_data.instrument_contract_expiry is not None
        and position.futures_contract_expiry != cycle_data.instrument_contract_expiry
    ):
        _roll_futures_position(session, config, position, cycle_data, now)
        return True
    return False


def _maybe_stop_out(
    session: Any, config: Any, position: MCXOptionsPosition, cycle_data: MCXCycleData,
    now: datetime,
) -> bool:
    """Flatten `position` if `stop_loss_premium_multiple` is set and its
    unrealized loss has exceeded that multiple of the premium collected on it.
    Default-OFF; see `_stop_out_position`.
    """
    stop_multiple = getattr(config, "stop_loss_premium_multiple", None)
    if stop_multiple is None or position.state != "long_futures" or position.status != "open":
        return False
    collected = _premium_collected(session, position, cycle_data.lot_size)
    if collected <= 0:
        return False
    if float(position.unrealized_pnl) > -(float(stop_multiple) * collected):
        return False
    booked = _stop_out_position(session, config, position, cycle_data, now)
    logger.warning(
        "mcx_options: STOPPED OUT position=%s config_id=%s at futures price %g -- unrealized "
        "loss exceeded %g x the %g premium collected; booked %.2f",
        position.id, config.id, cycle_data.futures_price, float(stop_multiple), collected, booked,
    )
    return True


def settle_cycle(session: Any, config: Any, cycle_data: MCXCycleData, today: date) -> None:
    """**Evening phase** (23:59 IST cron): resolve what the day's close
    decided, and nothing else.

    Settles every position whose leg is due, marks the book to market, and
    applies the stop-loss. **Opens nothing** -- writing a new leg is the
    morning phase's job (`entry_cycle`).

    The split exists because the two halves need different prices. Assignment
    is an ITM/OTM call the exchange makes against the day's SETTLEMENT price,
    so it has to run after the close -- which is why the 23:59 trigger and its
    seasonal-close reasoning in `scheduler/run.py` are unchanged. Opening a
    leg, by contrast, is an order: at 23:59 it could only ever be placed at
    the next morning's open, leaving an overnight gap between the decision and
    the fill (a blocker `docs/technical-debt.md` recorded against any live
    phase). Moving entry to the morning closes that gap.

    A selection row is written only for positions where something actually
    HAPPENED -- a settlement, a roll, a stop-out. A quiet position produces no
    row at all: the old "position already has an open leg, not due for
    settlement today" heartbeat is gone.
    """
    now = datetime.now(timezone.utc)
    event_seq = 0

    for position in _open_positions_for(session, config):
        events: list[str] = []

        if _maybe_roll(session, config, position, cycle_data, now):
            events.append(
                f"rolled the futures leg to the {cycle_data.instrument_contract_expiry} contract"
            )

        open_leg = _open_leg_for(session, position)
        if open_leg is not None and open_leg.cycle_expiry <= today:
            if open_leg.cycle_expiry < today:
                logger.warning(
                    "mcx_options: settling leg %s LATE -- its cycle_expiry was %s but this is "
                    "the first cycle since, running on %s. The ITM/OTM call below is made "
                    "against TODAY's futures price, not the expiry day's, so the outcome may "
                    "differ from what the exchange actually settled.",
                    open_leg.id, open_leg.cycle_expiry, today,
                )
            _settle_leg(position, open_leg, cycle_data, now)
            events.append(f"{open_leg.opt_type} {open_leg.strike:g} -> {open_leg.action}")

        _mark_to_market(position, cycle_data)

        if _maybe_stop_out(session, config, position, cycle_data, now):
            events.append(
                f"stop-loss hit -- flattened the futures position at "
                f"{cycle_data.futures_price:g}"
            )

        if events:
            event_seq += 1
            _record_selection(
                session, config, today, cycle_data,
                attempt_seq=SETTLE_SEQ_SIGN * event_seq,
                reason="; ".join(events),
                position=position,
            )


def entry_cycle(session: Any, config: Any, cycle_data: MCXCycleData, today: date) -> None:
    """**Morning phase** (09:15 IST cron): everything that is an ORDER.

    In order: roll any futures contract that moved on overnight, mark the book
    to market, write a covered call against any assigned position that lacks
    one, then top up this week's new-put target.

    **The weekly target.** The strategy sells `weekly_new_puts_target` (2) NEW
    puts per week per instrument, ON TOP of whatever is already open, counted
    by `MCXOptionsPosition.entry_week_start`. Counting by the week a position
    was OPENED -- rather than by what is currently open -- is what makes
    "irrespective of assigned or not" true: a put sold Monday and assigned on
    Wednesday still counts as that week's. It also makes the retry fall out
    for free: any morning on which the week's count is short, the shortfall is
    attempted again, with no separate "is today the first trading day"
    branch.

    **No row when nothing was due.** If the week's target is already met and
    no covered call is owed, this writes no selection row at all. That, plus
    the removal of the evening heartbeat row, is the whole of the log-noise
    fix: the selection log now contains only decisions.
    """
    now = datetime.now(timezone.utc)
    attempt_seq = 0

    positions = _open_positions_for(session, config)
    for position in positions:
        _maybe_roll(session, config, position, cycle_data, now)
        _mark_to_market(position, cycle_data)

    regime_label = regime_module.classify_today(cycle_data.futures_bars)

    # ---- 1. covered calls against assigned positions ---------------------
    for position in positions:
        if position.status != "open" or position.state != "long_futures":
            continue
        if _open_leg_for(session, position) is not None:
            continue
        attempt_seq += 1
        _write_covered_call(
            session, config, position, cycle_data, regime_label, today, now, attempt_seq
        )

    # ---- 2. this week's new short puts -----------------------------------
    week_start = _ist_week_start(today)
    opened_this_week = (
        session.query(MCXOptionsPosition)
        .filter_by(config_id=config.id, entry_week_start=week_start)
        .count()
    )
    room = int(getattr(config, "weekly_new_puts_target", 0) or 0) - opened_this_week
    cap = getattr(config, "max_concurrent_positions", None)
    if cap is not None:
        still_open = [p for p in _open_positions_for(session, config)]
        room = min(room, int(cap) - len(still_open))
    if room <= 0:
        return  # nothing due -- deliberately no selection row

    _open_new_puts(
        session, config, cycle_data, regime_label, today, now, week_start, room, attempt_seq
    )


def _write_covered_call(
    session: Any, config: Any, position: MCXOptionsPosition, cycle_data: MCXCycleData,
    regime_label: Any, today: date, now: datetime, attempt_seq: int,
) -> None:
    """Write a covered call against an assigned futures position, or record
    why one wasn't written. Called the morning AFTER assignment, which is the
    first moment such an order could actually be placed.
    """
    side_regime = regime_label.for_option_type("CE") if regime_label is not None else None
    if side_regime is None:
        _record_selection(
            session, config, today, cycle_data, attempt_seq=attempt_seq,
            reason="No regime label for today (insufficient warm-up data) -- no covered call",
            position=position,
        )
        return
    if side_regime == Regime.TREND_UNFAVORABLE:
        _record_selection(
            session, config, today, cycle_data, attempt_seq=attempt_seq,
            regime=side_regime.value,
            reason=f"{side_regime.value} regime for CE -- no covered call written",
            position=position,
        )
        return

    target_delta = float(
        config.consolidating_target_delta
        if side_regime == Regime.CONSOLIDATING
        else config.trend_favorable_target_delta
    )
    chain = cycle_data.option_chain
    if position.basis is not None:
        chain = _floor_chain_for_covered_call(chain, float(position.basis))

    sigma = _effective_sigma(config)
    max_spread = getattr(config, "max_relative_spread", None)
    candidates = evaluate_candidates(
        chain, opt_type="CE", futures_price=cycle_data.futures_price,
        T_years=cycle_data.T_years, sigma=sigma, r=RISK_FREE_RATE,
        min_open_interest=int(config.min_open_interest),
        max_relative_spread=None if max_spread is None else float(max_spread),
    )
    candidates_considered = [
        {"strike": c.strike, "delta": c.delta, "oi": c.oi, "ltp": c.ltp} for c in candidates
    ]
    picked = select_strike_by_target_delta(
        chain, opt_type="CE", futures_price=cycle_data.futures_price,
        T_years=cycle_data.T_years, sigma=sigma, r=RISK_FREE_RATE,
        target_delta=target_delta, min_open_interest=int(config.min_open_interest),
        max_relative_spread=None if max_spread is None else float(max_spread),
    )
    if picked is None:
        _record_selection(
            session, config, today, cycle_data, attempt_seq=attempt_seq,
            regime=side_regime.value, target_delta=target_delta,
            reason="No call strike cleared the basis/OI/executability filters",
            candidates_considered=candidates_considered, position=position,
        )
        return

    # A COVERED call is covered by the futures actually held, never by
    # whatever `config.lots` happens to say today.
    held_qty = float(position.futures_qty)
    lots = held_qty / cycle_data.lot_size if cycle_data.lot_size else float(config.lots)
    premium = picked.ltp
    if getattr(config, "use_bid_for_entry_premium", False) and picked.top_bid_price:
        premium = float(picked.top_bid_price)

    position.realized_pnl = float(position.realized_pnl) + _entry_leg_amount(
        premium, lots, cycle_data.lot_size
    )
    session.add(
        MCXOptionsLeg(
            id=uuid.uuid4(), position_id=position.id, cycle_expiry=cycle_data.option_expiry,
            opt_type="CE", strike=picked.strike, premium=premium, lots=lots,
            action="sell_call", opened_at=now,
        )
    )
    _record_selection(
        session, config, today, cycle_data, attempt_seq=attempt_seq,
        regime=side_regime.value, target_delta=target_delta, selected_strike=picked.strike,
        reason=(
            f"{side_regime.value} regime, target delta {target_delta:.2f} -- "
            f"wrote a covered call at strike {picked.strike:g}"
        ),
        candidates_considered=candidates_considered, position=position,
    )


def _open_new_puts(
    session: Any, config: Any, cycle_data: MCXCycleData, regime_label: Any, today: date,
    now: datetime, week_start: date, room: int, attempt_seq: int,
) -> None:
    """Attempt `room` new short puts for this week's target, recording one
    selection row per attempt -- filled or not.
    """
    # Purely a label for the selection log, so a reader can tell this week's
    # scheduled round from a later morning picking up a shortfall the regime
    # blocked earlier in the week. It drives no decision: the weekly COUNT
    # above is what actually gates entry, which is why a Monday holiday needs
    # no special handling in the logic -- only in this wording.
    round_label = "weekly round" if is_first_mcx_trading_day_of_week(today) else "retry"

    side_regime = regime_label.for_option_type("PE") if regime_label is not None else None
    if side_regime is None:
        _record_selection(
            session, config, today, cycle_data, attempt_seq=attempt_seq + 1,
            reason=(
                f"{round_label}: no regime label for today (insufficient warm-up data) "
                "-- skipped entry"
            ),
            position=None,
        )
        return
    if side_regime == Regime.TREND_UNFAVORABLE:
        _record_selection(
            session, config, today, cycle_data, attempt_seq=attempt_seq + 1,
            regime=side_regime.value,
            reason=f"{round_label}: {side_regime.value} regime for PE -- skipped entry",
            position=None,
        )
        return

    target_delta = float(
        config.consolidating_target_delta
        if side_regime == Regime.CONSOLIDATING
        else config.trend_favorable_target_delta
    )

    entry_chains = {
        e: c for e, c in cycle_data.chains_by_expiry.items() if e in cycle_data.entry_expiries
    }
    if not entry_chains:
        _record_selection(
            session, config, today, cycle_data, attempt_seq=attempt_seq + 1,
            regime=side_regime.value, target_delta=target_delta,
            reason=(
                f"{round_label}: no expiry is far enough out to open a new put "
                f"(entry_min_dte_days={getattr(config, 'entry_min_dte_days', None)})"
            ),
            position=None,
        )
        return

    open_positions = _open_positions_for(session, config)
    existing_puts: list[float] = []
    expiry_counts: dict[date, int] = {}
    for position in open_positions:
        leg = _open_leg_for(session, position)
        if leg is not None and leg.opt_type == "PE" and leg.strike is not None:
            existing_puts.append(float(leg.strike))
        if leg is not None and leg.cycle_expiry is not None:
            expiry_counts[leg.cycle_expiry] = expiry_counts.get(leg.cycle_expiry, 0) + 1

    picks, reasons, evaluated = select_put_ladder(
        chains_by_expiry=entry_chains,
        futures_price=cycle_data.futures_price,
        existing_puts=existing_puts,
        config=config,
        target_delta=target_delta,
        lot_size=cycle_data.lot_size,
        today=today,
        realised_vol=realised_vol_from_bars(cycle_data.futures_bars),
        count=room,
        existing_expiry_counts=expiry_counts,
    )

    for pick in picks:
        attempt_seq += 1
        position = MCXOptionsPosition(
            id=uuid.uuid4(), config_id=config.id, status="open", state="flat",
            futures_qty=0, opened_at=now, entry_week_start=week_start,
            realized_pnl=0, unrealized_pnl=0,
        )
        session.add(position)
        session.flush()

        lots = float(config.lots)
        position.realized_pnl = _entry_leg_amount(pick.premium, lots, cycle_data.lot_size)
        session.add(
            MCXOptionsLeg(
                id=uuid.uuid4(), position_id=position.id, cycle_expiry=pick.expiry,
                opt_type="PE", strike=pick.strike, premium=pick.premium, lots=lots,
                action="sell_put", opened_at=now,
            )
        )
        _record_selection(
            session, config, today, cycle_data, attempt_seq=attempt_seq,
            regime=side_regime.value, target_delta=target_delta, selected_strike=pick.strike,
            option_expiry=pick.expiry,
            candidates_considered=evaluated,
            reason=(
                f"{round_label}: {side_regime.value} regime, target delta "
                f"{target_delta:.2f} -- sold PE {pick.strike:g} exp {pick.expiry} "
                f"(annualised yield {pick.annualized_yield:.1%}, breakeven cushion "
                f"{pick.breakeven_cushion:.1%})"
            ),
            position=position,
        )

    for reason in reasons:
        attempt_seq += 1
        _record_selection(
            session, config, today, cycle_data, attempt_seq=attempt_seq,
            regime=side_regime.value, target_delta=target_delta,
            reason=f"{round_label}: {reason}",
            candidates_considered=evaluated,
            position=None,
        )


__all__ = [
    "settle_cycle",
    "entry_cycle",
    "MCXOptionsStateError",
    "DEFAULT_SIGMA",
    "RISK_FREE_RATE",
]
