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
a leg only ever resolves via expiry/assignment/being-called-away.

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

import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Optional

from growmore_bot.broker.dhan_client import OptionChainSnapshot
from growmore_bot.costs import DEFAULT_COST_MODEL, FREE_COST_MODEL, leg_cost
from growmore_bot.mcx_options import regime as regime_module
from growmore_bot.mcx_options.live_data import MCXCycleData
from growmore_bot.mcx_options.regime import Regime
from growmore_bot.mcx_options.strike_selection import (
    evaluate_candidates,
    select_strike_by_target_delta,
)
from growmore_bot.persistence.models import MCXOptionsLeg, MCXOptionsPosition, MCXOptionsSelection

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

    # `strike` is nullable at the schema level only to accommodate a "roll"
    # leg (migration 0023) -- this function only ever settles a genuine PE/CE
    # option leg (run_cycle never calls it on a roll leg), which always has
    # one, so this narrows the type back down for the comparisons below.
    assert leg.strike is not None, "an option leg being settled must have a strike"
    # `premium` is nullable for the same reason (a "roll" leg) -- narrows
    # back down for `_entry_leg_amount` below, same reasoning as `strike`.
    assert leg.premium is not None, "an option leg being settled must have a premium"

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


def run_cycle(
    session: Any, config: Any, cycle_data: MCXCycleData, today: date,
) -> None:
    """Run one commodity's daily cycle: settle any leg expiring `today`,
    then decide whether to open a new leg today, writing
    `MCXOptionsPosition`/`MCXOptionsLeg` rows and exactly one
    `MCXOptionsSelection` row via `session.add(...)` regardless of whether
    an entry happened (dashboard "why didn't it enter" transparency, mirrors
    wheel_basket_engine._record_selections).
    """
    now = datetime.now(timezone.utc)

    position: Optional[MCXOptionsPosition] = (
        session.query(MCXOptionsPosition).filter_by(config_id=config.id, status="open").first()
    )

    # Roll a held futures position FIRST, before any settle/entry decision --
    # see module docstring. A mismatch means the Instrument-level rollover
    # mechanism (contract_rollover.roll_to_next_contract, run earlier in the
    # same tick) has already advanced the underlying contract out from under
    # this position.
    if (
        position is not None
        and position.state == "long_futures"
        and position.futures_contract_expiry is not None
        and cycle_data.instrument_contract_expiry is not None
        and position.futures_contract_expiry != cycle_data.instrument_contract_expiry
    ):
        _roll_futures_position(session, config, position, cycle_data, now)

    entry_needed = True
    if position is not None:
        open_leg = (
            session.query(MCXOptionsLeg)
            .filter_by(position_id=position.id, settled_at=None)
            .one_or_none()
        )
        if open_leg is not None and open_leg.cycle_expiry == today:
            _settle_leg(position, open_leg, cycle_data, now)
            entry_needed = True  # settling always leaves room for a fresh decision
        elif open_leg is not None:
            entry_needed = False  # a leg is live and not due yet
            _mark_to_market(position, cycle_data)
        # else: an open position with no open leg at all -- fall through and
        # let entry_needed=True try to write one (should not normally arise).

    active_position = position if (position is not None and position.status == "open") else None

    if not entry_needed:
        opt_type = (
            "CE" if (active_position is not None and active_position.state == "long_futures") else "PE"
        )
        # No entry decision is made on a hold day, but today's regime read is
        # still cheap and side-effect-free to compute -- recorded purely so
        # the dashboard can show "today's regime read was X" for every day,
        # not just entry days.
        hold_regime_label = regime_module.classify_today(cycle_data.futures_bars)
        hold_side_regime = hold_regime_label.for_option_type(opt_type) if hold_regime_label else None
        basis = active_position.basis if active_position is not None else None
        unrealized = active_position.unrealized_pnl if active_position is not None else None
        reason = "position already has an open leg, not due for settlement today"
        if basis is not None and unrealized is not None:
            reason += f" (basis {float(basis):g}, unrealized P&L {float(unrealized):.2f})"
        session.add(
            MCXOptionsSelection(
                id=uuid.uuid4(), config_id=config.id, cycle_date=today,
                regime=hold_side_regime.value if hold_side_regime is not None else None,
                target_delta=None, selected_strike=None, reason=reason,
                futures_price=cycle_data.futures_price,
                **_position_snapshot(active_position),
            )
        )
        return

    opt_type = "CE" if (active_position is not None and active_position.state == "long_futures") else "PE"

    regime_label = regime_module.classify_today(cycle_data.futures_bars)
    if regime_label is None:
        session.add(
            MCXOptionsSelection(
                id=uuid.uuid4(), config_id=config.id, cycle_date=today, regime=None,
                target_delta=None, selected_strike=None,
                reason="No regime label for today (insufficient warm-up data) -- skipped entry",
                futures_price=cycle_data.futures_price,
                **_position_snapshot(active_position),
            )
        )
        return

    side_regime = regime_label.for_option_type(opt_type)
    if side_regime == Regime.TREND_UNFAVORABLE:
        session.add(
            MCXOptionsSelection(
                id=uuid.uuid4(), config_id=config.id, cycle_date=today, regime=side_regime.value,
                target_delta=None, selected_strike=None,
                reason=f"{side_regime.value} regime for {opt_type} -- skipped entry",
                futures_price=cycle_data.futures_price,
                **_position_snapshot(active_position),
            )
        )
        return

    target_delta = float(
        config.consolidating_target_delta
        if side_regime == Regime.CONSOLIDATING
        else config.trend_favorable_target_delta
    )

    chain = cycle_data.option_chain
    if opt_type == "CE" and active_position is not None and active_position.basis is not None:
        chain = _floor_chain_for_covered_call(chain, float(active_position.basis))

    candidates = evaluate_candidates(
        chain, opt_type=opt_type, futures_price=cycle_data.futures_price,
        T_years=cycle_data.T_years, sigma=DEFAULT_SIGMA, r=RISK_FREE_RATE,
        min_open_interest=int(config.min_open_interest),
    )
    candidates_considered = [
        {"strike": c.strike, "delta": c.delta, "oi": c.oi, "ltp": c.ltp} for c in candidates
    ]
    picked = select_strike_by_target_delta(
        chain, opt_type=opt_type, futures_price=cycle_data.futures_price,
        T_years=cycle_data.T_years, sigma=DEFAULT_SIGMA, r=RISK_FREE_RATE,
        target_delta=target_delta, min_open_interest=int(config.min_open_interest),
    )

    if picked is None:
        session.add(
            MCXOptionsSelection(
                id=uuid.uuid4(), config_id=config.id, cycle_date=today, regime=side_regime.value,
                target_delta=target_delta, selected_strike=None,
                reason="No strike cleared the OI/basis filter -- skipped entry",
                futures_price=cycle_data.futures_price,
                candidates_considered=candidates_considered,
                **_position_snapshot(active_position),
            )
        )
        return

    if active_position is None:
        active_position = MCXOptionsPosition(
            id=uuid.uuid4(), config_id=config.id, status="open", state="flat",
            futures_qty=0, opened_at=now, realized_pnl=0, unrealized_pnl=0,
        )
        session.add(active_position)
        session.flush()

    # The premium collected for selling this option is real cash credited
    # the moment it is sold -- book it into realized_pnl right here, at
    # entry, not deferred to settlement. See module docstring's "Option
    # premium booking" section and `_entry_leg_amount`'s docstring for the
    # formula (matches research/mcx_options/engine.py's `add_leg` exactly).
    # `leg.pnl` is deliberately left unset here -- it represents the leg's
    # fully-resolved outcome once SETTLED (see `_settle_leg`), not the
    # entry credit; the raw `premium` column already shows what an open
    # leg collected.
    entry_leg_amount = _entry_leg_amount(picked.ltp, config.lots, cycle_data.lot_size)
    active_position.realized_pnl = float(active_position.realized_pnl) + entry_leg_amount

    action = "sell_call" if opt_type == "CE" else "sell_put"
    session.add(
        MCXOptionsLeg(
            id=uuid.uuid4(), position_id=active_position.id, cycle_expiry=cycle_data.option_expiry,
            opt_type=opt_type, strike=picked.strike, premium=picked.ltp, lots=config.lots,
            action=action, opened_at=now,
        )
    )

    session.add(
        MCXOptionsSelection(
            id=uuid.uuid4(), config_id=config.id, cycle_date=today, regime=side_regime.value,
            target_delta=target_delta, selected_strike=picked.strike,
            reason=(
                f"{side_regime.value} regime, target delta {target_delta:.2f} -- "
                f"entered {opt_type} at strike {picked.strike}"
            ),
            futures_price=cycle_data.futures_price,
            candidates_considered=candidates_considered,
            **_position_snapshot(active_position),
        )
    )


__all__ = ["run_cycle", "DEFAULT_SIGMA", "RISK_FREE_RATE"]
