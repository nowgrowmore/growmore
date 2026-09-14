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

**TODO -- futures contract rollover is NOT implemented.** If
`futures_contract_expiry` ever needs rolling (the assigned futures contract's
own expiry arriving before the covered-call cycle resolves), this engine
does nothing: it never sets `futures_contract_expiry` on assignment (left
`None`), and there is no logic here to detect or execute a roll. This is the
single deepest/most complex piece of the offline backtest's state machine
(see research/mcx_options/engine.py's module docstring on rollover cost
placeholders) and was deliberately cut last, per the phase-2 build's
explicit scope-cutting instruction. Before this strategy is trusted to hold
a `long_futures` position across a contract-month boundary, this MUST be
built and tested -- see docs/technical-debt.md.

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
from growmore_bot.mcx_options import regime as regime_module
from growmore_bot.mcx_options.live_data import MCXCycleData
from growmore_bot.mcx_options.regime import Regime
from growmore_bot.mcx_options.strike_selection import select_strike_by_target_delta
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

    if leg.opt_type == "PE":
        if F < leg.strike:  # ITM: assigned into a futures position at the strike
            leg.action = "assigned"
            leg.assigned = True
            position.state = "long_futures"
            position.basis = leg.strike
            position.futures_qty = qty
            # TODO(mcx-options-futures-rollover): futures_contract_expiry is
            # deliberately left unset here -- see module docstring. Real
            # contract-month bookkeeping is not implemented yet.
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
        session.add(
            MCXOptionsSelection(
                id=uuid.uuid4(), config_id=config.id, cycle_date=today, regime=None,
                target_delta=None, selected_strike=None,
                reason="position already has an open leg, not due for settlement today",
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
        )
    )


__all__ = ["run_cycle", "DEFAULT_SIGMA", "RISK_FREE_RATE"]
