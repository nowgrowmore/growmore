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
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        self._closes.append(float(bar.close))

        if len(self._closes) < self.period + 1:
            return Signal(action=SignalAction.HOLD)

        closes = list(self._closes)
        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        avg_gain = sum(d for d in diffs if d > 0) / self.period
        avg_loss = sum(-d for d in diffs if d < 0) / self.period
        self._avg_gain = avg_gain
        self._avg_loss = avg_loss

        if avg_gain == 0 and avg_loss == 0:
            # A perfectly flat window (every diff exactly 0) makes RS = 0/0,
            # undefined -- not "maximally oversold". Convention is neutral
            # (50), matching an unmoving market having no directional bias.
            # Found via independent code review 2026-09-04: checking
            # avg_gain == 0 alone (before avg_loss) reported this case as
            # rsi = 0.0, fabricating a BUY on the very next up-tick.
            rsi = 50.0
        elif avg_gain == 0:
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
        # avg_gain/avg_loss/prev_close (not just the derived rsi value) let a
        # caller solve exactly how much price would need to move for RSI to
        # cross the oversold/overbought threshold.
        prev_close = self._closes[-2] if len(self._closes) >= 2 else None
        return {
            "rsi": self._prev_rsi,
            "avg_gain": self._avg_gain,
            "avg_loss": self._avg_loss,
            "prev_close": prev_close,
        }

    def get_state_snapshot(self) -> dict[str, Any]:
        if self._prev_rsi is None:
            return {}
        return {"prev_rsi": self._prev_rsi}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "prev_rsi" in snapshot:
            self._prev_rsi = snapshot["prev_rsi"]


__all__ = ["RsiMeanReversionStrategy"]
