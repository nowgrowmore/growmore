"""Fast/slow SMA crossover strategy.

BUY when the fast SMA crosses above the slow SMA; SELL when it crosses below;
HOLD otherwise (including while there isn't yet enough history, and on the
first bar where both SMAs first become computable -- there's no prior
relation yet to have "crossed" from).
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class SmaCrossoverStrategy(Strategy):
    def __init__(self, fast_period: int, slow_period: int) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period must be less than slow_period")
        if fast_period < 1 or slow_period < 1:
            raise ValueError("periods must be positive")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self._closes: deque[float] = deque(maxlen=slow_period)
        self._prev_fast_above_slow: Optional[bool] = None
        self._last_fast_sma: Optional[float] = None
        self._last_slow_sma: Optional[float] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        self._closes.append(float(bar.close))

        if len(self._closes) < self.slow_period:
            return Signal(action=SignalAction.HOLD)

        closes = list(self._closes)
        fast_sma = sum(closes[-self.fast_period :]) / self.fast_period
        slow_sma = sum(closes) / self.slow_period
        self._last_fast_sma = fast_sma
        self._last_slow_sma = slow_sma
        fast_above_slow = fast_sma > slow_sma

        prev = self._prev_fast_above_slow
        self._prev_fast_above_slow = fast_above_slow

        if prev is None:
            # First point where both SMAs are computable -- nothing to cross from yet.
            return Signal(action=SignalAction.HOLD)
        if fast_above_slow and not prev:
            return Signal(action=SignalAction.BUY)
        if not fast_above_slow and prev:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)

    def debug_state(self) -> dict[str, Optional[float]]:
        # oldest_fast/oldest_slow are the values about to roll out of each
        # window on the next appended BAR -- pure observability, retained
        # because they're already persisted in bot_signal_state.indicators.
        # They are deliberately NOT what the dashboard's "% move to signal"
        # solve needs: the scheduler rebuilds this strategy every tick and
        # warms it up from history ending yesterday, so the next tick
        # REPLACES the live price in the newest slot rather than appending a
        # bar -- nothing rolls out. Solving with these values answers a
        # question about a day that never happens (see
        # dashboard/lib/percent-to-signal.ts, fixed 2026-09-04).
        oldest_fast: Optional[float] = None
        oldest_slow: Optional[float] = None
        if self._last_fast_sma is not None:
            closes = list(self._closes)
            oldest_fast = closes[-self.fast_period]
            oldest_slow = closes[0]
        return {
            "fast_sma": self._last_fast_sma,
            "slow_sma": self._last_slow_sma,
            "oldest_fast": oldest_fast,
            "oldest_slow": oldest_slow,
        }

    def get_state_snapshot(self) -> dict[str, Any]:
        if self._prev_fast_above_slow is None:
            return {}
        return {"prev_fast_above_slow": self._prev_fast_above_slow}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "prev_fast_above_slow" in snapshot:
            self._prev_fast_above_slow = snapshot["prev_fast_above_slow"]


__all__ = ["SmaCrossoverStrategy"]
