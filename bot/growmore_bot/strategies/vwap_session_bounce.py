"""Live VWAP + CPR session-bounce strategy.

CPR (Central Pivot Range) sets the day's directional *bias* -- a gate;
today's live session VWAP crossing is the entry *trigger*. A trade only
fires when price is outside the CPR band on the correct side AND crosses
back over VWAP in that same direction; price sitting inside CPR, or a VWAP
cross against the day's CPR bias, is ignored.

**This strategy cannot be meaningfully backtested.** CPR only needs the
prior day's H/L/C -- fully derivable from historical daily bars, same as
every other strategy here. The live VWAP, though, is Dhan's own real,
continuously-updating session VWAP (`Quote.vwap`, mapped from the
`average_price` field in a live quote response) -- it has no equivalent in
historical daily bars at all, so it can only ever be evaluated going
forward. See docs/goldmini-regime-switch-results.md and
docs/smallcap-momentum-research.md's sibling doc for why this is validated
by paper trading instead of a historical backtest.

`on_bar` tells a historical `Bar` (warm-up) apart from a live `Quote` by
checking whether `ltp` is present (`getattr(bar, "ltp", None)`) -- a field
only the live quote shape has -- rather than needing a new
`Strategy.on_bar` parameter. It deliberately does NOT discriminate on
`vwap`: `Quote.vwap` is legitimately None early in a session (Dhan reports
`average_price: 0` until real trades print), and a real `Quote` also
carries high/low/close, so such a tick used to fall into the warm-up branch
and overwrite today's CPR with one computed from today's own partial
session range -- found via independent code review 2026-09-04.
  - **Historical bar**: recompute CPR from *this* bar's H/L/C and store it
    -- return HOLD (no `vwap` to trade against yet). Since the scheduler's
    `_warm_up_strategy` already replays daily bars ending *yesterday* fresh
    on every tick, `_current_cpr` is correctly "yesterday's CPR, for today"
    by the time warm-up finishes, with zero extra scheduler plumbing.
  - **Live quote**: BUY when `ltp` is above CPR's top band AND crosses from
    below `vwap` to above it; SELL when `ltp` is below CPR's bottom band AND
    crosses from above `vwap` to below it; HOLD otherwise.

`requires_intraday_flatten = True`: both CPR and VWAP are single-day
concepts (VWAP resets every session; CPR is yesterday's range) -- a
position opened on today's context shouldn't carry into a new day where
both have already reset to something else. The scheduler force-closes any
open position near the daily MCX session close for a strategy that sets
this, the same way it already does for a contract nearing expiry.
"""
from __future__ import annotations

from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class VwapSessionBounceStrategy(Strategy):
    requires_intraday_flatten = True

    def __init__(self) -> None:
        self._current_cpr: Optional[tuple[float, float, float]] = None  # (bottom, pivot, top)
        self._prev_above_vwap: Optional[bool] = None
        self._last_vwap: Optional[float] = None
        self._last_ltp: Optional[float] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        raw_ltp = getattr(bar, "ltp", None)

        if raw_ltp is None:
            # A historical daily bar during warm-up -- CPR for "today" comes
            # from THIS bar's H/L/C (yesterday's range, from today's vantage
            # point). No live quote to trade against yet.
            pivot = (bar.high + bar.low + bar.close) / 3
            bc = (bar.high + bar.low) / 2
            tc = 2 * pivot - bc
            self._current_cpr = (min(bc, tc), pivot, max(bc, tc))
            return Signal(action=SignalAction.HOLD)

        vwap = getattr(bar, "vwap", None)
        if vwap is None:
            # A live quote with no real session VWAP yet (no trades printed).
            # Nothing to trade against -- and critically, this must leave BOTH
            # today's CPR and the crossing reference untouched, so the first
            # tick carrying a real VWAP can't register a crossing against a
            # reference that never existed.
            return Signal(action=SignalAction.HOLD)

        ltp = float(raw_ltp)
        self._last_vwap = vwap
        self._last_ltp = ltp

        if self._current_cpr is None:
            return Signal(action=SignalAction.HOLD)

        cpr_bottom, _, cpr_top = self._current_cpr
        above_vwap = ltp > vwap
        prev = self._prev_above_vwap
        self._prev_above_vwap = above_vwap

        if prev is None:
            return Signal(action=SignalAction.HOLD)

        crossed_up = above_vwap and not prev
        crossed_down = not above_vwap and prev

        if ltp > cpr_top and crossed_up:
            return Signal(action=SignalAction.BUY)
        if ltp < cpr_bottom and crossed_down:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)

    def debug_state(self) -> dict[str, Optional[float]]:
        cpr_bottom, cpr_pivot, cpr_top = self._current_cpr or (None, None, None)
        return {
            "cpr_bottom": cpr_bottom,
            "cpr_pivot": cpr_pivot,
            "cpr_top": cpr_top,
            "vwap": self._last_vwap,
        }

    def get_state_snapshot(self) -> dict[str, Any]:
        if self._prev_above_vwap is None:
            return {}
        return {"prev_above_vwap": self._prev_above_vwap}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "prev_above_vwap" in snapshot:
            self._prev_above_vwap = snapshot["prev_above_vwap"]


__all__ = ["VwapSessionBounceStrategy"]
