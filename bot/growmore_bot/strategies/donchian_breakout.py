"""N-period Donchian channel breakout strategy.

BUY the moment the close FIRST breaks above the highest high of the N bars
strictly preceding it; SELL the moment it FIRST breaks below the lowest low
of the same window; HOLD otherwise (including while there isn't yet an N-bar
history, and on every subsequent bar the close remains outside the channel --
sustained, not repeated, matching every other signalling strategy here). The
current bar is never included in its own channel -- this mirrors the backtest
engine's "trade on next bar" no-lookahead discipline: the signal at bar i is
only ever a function of bars < i, plus bar i's own close.

Regression: found via independent code review 2026-09-04 -- this strategy
used to have no crossing state at all, re-signalling BUY/SELL on EVERY tick
the close stayed outside the channel. The scheduler rebuilds a fresh strategy
instance every 5-minute tick and warms it up from history ending yesterday,
so a live position sitting above the channel all day would either (a) get
its repeat BUY silently rejected by the max_position_size guard, which skips
mark-to-market on that code path -- freezing unrealized_pnl for the rest of
the day (the exact bug `requires_intraday_flatten`'s sibling fixes documented
in base.py were about), or (b) with a looser max_position_size, silently
pyramid a new lot every tick. Fixed the same way every other strategy here
already was: track the previous bar's above/below/inside classification and
only fire on the TRANSITION into a breakout.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class DonchianBreakoutStrategy(Strategy):
    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be positive")
        self.period = period
        self._highs: deque[float] = deque(maxlen=period)
        self._lows: deque[float] = deque(maxlen=period)
        self._last_channel_high: Optional[float] = None
        self._last_channel_low: Optional[float] = None
        self._prev_breakout_state: Optional[str] = None  # "above" | "below" | "inside"

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        signal = Signal(action=SignalAction.HOLD)

        if len(self._highs) == self.period:
            channel_high = max(self._highs)
            channel_low = min(self._lows)
            self._last_channel_high = channel_high
            self._last_channel_low = channel_low
            close = float(bar.close)
            if close > channel_high:
                current_state = "above"
            elif close < channel_low:
                current_state = "below"
            else:
                current_state = "inside"

            prev_state = self._prev_breakout_state
            self._prev_breakout_state = current_state

            if current_state == "above" and prev_state != "above":
                signal = Signal(action=SignalAction.BUY)
            elif current_state == "below" and prev_state != "below":
                signal = Signal(action=SignalAction.SELL)

        self._highs.append(float(bar.high))
        self._lows.append(float(bar.low))
        return signal

    def debug_state(self) -> dict[str, Optional[float]]:
        return {"channel_high": self._last_channel_high, "channel_low": self._last_channel_low}

    def get_state_snapshot(self) -> dict[str, Any]:
        if self._prev_breakout_state is None:
            return {}
        return {"prev_breakout_state": self._prev_breakout_state}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "prev_breakout_state" in snapshot:
            self._prev_breakout_state = snapshot["prev_breakout_state"]


__all__ = ["DonchianBreakoutStrategy"]
