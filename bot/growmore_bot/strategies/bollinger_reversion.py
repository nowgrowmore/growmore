"""Bollinger Band reversion strategy.

A second mean-reversion strategy (alongside RsiMeanReversionStrategy),
differently shaped: bands are `num_std` population standard deviations above
and below a rolling SMA(period). BUY when price closes back INSIDE the lower
band after having closed outside it (a faded extreme); SELL on the mirror
condition at the upper band. Entering an extreme is never itself a signal --
only recovering from one is.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class BollingerReversionStrategy(Strategy):
    def __init__(self, period: int, num_std: float = 2.0) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        if num_std <= 0:
            raise ValueError("num_std must be positive")
        self.period = period
        self.num_std = num_std
        self._closes: deque[float] = deque(maxlen=period)
        self._prev_below: Optional[bool] = None
        self._prev_above: Optional[bool] = None
        self._last_upper: Optional[float] = None
        self._last_lower: Optional[float] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        self._closes.append(float(bar.close))

        if len(self._closes) < self.period:
            return Signal(action=SignalAction.HOLD)

        closes = list(self._closes)
        mean = sum(closes) / self.period
        variance = sum((c - mean) ** 2 for c in closes) / self.period
        std = math.sqrt(variance)
        upper = mean + self.num_std * std
        lower = mean - self.num_std * std
        self._last_upper = upper
        self._last_lower = lower
        close = closes[-1]

        below = close < lower
        above = close > upper

        prev_below = self._prev_below
        prev_above = self._prev_above
        self._prev_below = below
        self._prev_above = above

        if prev_below is None:
            # First computable point -- nothing to recover from yet.
            return Signal(action=SignalAction.HOLD)
        if prev_below and not below:
            return Signal(action=SignalAction.BUY)
        if prev_above and not above:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)

    def debug_state(self) -> dict[str, Optional[float]]:
        return {"upper_band": self._last_upper, "lower_band": self._last_lower}

    def get_state_snapshot(self) -> dict[str, Any]:
        if self._prev_below is None and self._prev_above is None:
            return {}
        return {"prev_below": self._prev_below, "prev_above": self._prev_above}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "prev_below" in snapshot:
            self._prev_below = snapshot["prev_below"]
        if "prev_above" in snapshot:
            self._prev_above = snapshot["prev_above"]


__all__ = ["BollingerReversionStrategy"]
