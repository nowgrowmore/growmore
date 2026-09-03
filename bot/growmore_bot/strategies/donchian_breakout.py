"""N-period Donchian channel breakout strategy.

BUY when the close breaks above the highest high of the N bars strictly
preceding it; SELL when it breaks below the lowest low of the same window;
HOLD otherwise (including while there isn't yet an N-bar history). The
current bar is never included in its own channel -- this mirrors the
backtest engine's "trade on next bar" no-lookahead discipline: the signal at
bar i is only ever a function of bars < i, plus bar i's own close.
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

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        signal = Signal(action=SignalAction.HOLD)

        if len(self._highs) == self.period:
            channel_high = max(self._highs)
            channel_low = min(self._lows)
            self._last_channel_high = channel_high
            self._last_channel_low = channel_low
            close = float(bar.close)
            if close > channel_high:
                signal = Signal(action=SignalAction.BUY)
            elif close < channel_low:
                signal = Signal(action=SignalAction.SELL)

        self._highs.append(float(bar.high))
        self._lows.append(float(bar.low))
        return signal

    def debug_state(self) -> dict[str, Optional[float]]:
        return {"channel_high": self._last_channel_high, "channel_low": self._last_channel_low}


__all__ = ["DonchianBreakoutStrategy"]
