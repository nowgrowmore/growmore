"""RSI mean-reversion strategy.

The mean-reversion counterpart to SmaCrossoverStrategy/DonchianBreakoutStrategy
(both trend/breakout-style): BUY when RSI crosses back ABOVE the oversold
threshold (was <=, now >), betting the extreme has been faded; SELL when RSI
crosses back BELOW the overbought threshold. Crossing INTO an extreme zone is
never itself a signal -- only crossing back OUT of one is, since that's the
"reversion" this strategy is betting on, not the extreme itself.

Uses simple (unweighted) average gain/loss over `period` diffs -- a "Cutler's
RSI" variant -- rather than Wilder's exponential smoothing. This is a
deliberate choice: it keeps the calculation stateless per window (no seeding
period distinct from `period`, no smoothing-constant ambiguity), which makes
it straightforward to hand-verify in tests.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class RsiMeanReversionStrategy(Strategy):
    def __init__(self, period: int, oversold: float = 30, overbought: float = 70) -> None:
        if period < 1:
            raise ValueError("period must be positive")
        if not (0 < oversold < overbought < 100):
            raise ValueError("must have 0 < oversold < overbought < 100")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self._closes: deque[float] = deque(maxlen=period + 1)
        self._prev_rsi: Optional[float] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        self._closes.append(float(bar.close))

        if len(self._closes) < self.period + 1:
            return Signal(action=SignalAction.HOLD)

        closes = list(self._closes)
        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        avg_gain = sum(d for d in diffs if d > 0) / self.period
        avg_loss = sum(-d for d in diffs if d < 0) / self.period

        if avg_gain == 0:
            rsi = 0.0
        elif avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - 100.0 / (1.0 + rs)

        prev = self._prev_rsi
        self._prev_rsi = rsi

        if prev is None:
            # First computable RSI value -- nothing to cross from yet.
            return Signal(action=SignalAction.HOLD)
        if prev <= self.oversold and rsi > self.oversold:
            return Signal(action=SignalAction.BUY)
        if prev >= self.overbought and rsi < self.overbought:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)

    def debug_state(self) -> dict[str, Optional[float]]:
        return {"rsi": self._prev_rsi}


__all__ = ["RsiMeanReversionStrategy"]
