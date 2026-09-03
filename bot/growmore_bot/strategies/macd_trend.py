"""MACD trend strategy.

A momentum-based trend strategy, distinct from SmaCrossoverStrategy's plain
level-cross: BUY when the MACD line (EMA(fast) - EMA(slow)) crosses above its
own signal line (an EMA of the MACD line), SELL on the mirror cross below.

Both the fast/slow EMAs and the signal-line EMA seed as a plain SMA of their
first `period` values, then update via the standard recurrence
value*k + prev*(1-k), k = 2/(period+1) -- the conventional MACD definition.
"""
from __future__ import annotations

from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class MacdTrendStrategy(Strategy):
    def __init__(self, fast_period: int, slow_period: int, signal_period: int) -> None:
        if fast_period < 1 or slow_period < 1:
            raise ValueError("periods must be positive")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be less than slow_period")
        if signal_period < 1:
            raise ValueError("signal_period must be positive")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

        self._closes_seed: list[float] = []
        self._fast_ema: Optional[float] = None
        self._slow_ema: Optional[float] = None
        self._macd_seed: list[float] = []
        self._signal: Optional[float] = None
        self._prev_macd_above_signal: Optional[bool] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        close = float(bar.close)
        self._closes_seed.append(close)

        k_fast = 2.0 / (self.fast_period + 1)
        k_slow = 2.0 / (self.slow_period + 1)
        k_signal = 2.0 / (self.signal_period + 1)

        if self._fast_ema is None:
            if len(self._closes_seed) >= self.fast_period:
                self._fast_ema = sum(self._closes_seed[-self.fast_period :]) / self.fast_period
        else:
            self._fast_ema = close * k_fast + self._fast_ema * (1 - k_fast)

        if self._slow_ema is None:
            if len(self._closes_seed) >= self.slow_period:
                self._slow_ema = sum(self._closes_seed[-self.slow_period :]) / self.slow_period
        else:
            self._slow_ema = close * k_slow + self._slow_ema * (1 - k_slow)

        if self._fast_ema is None or self._slow_ema is None:
            return Signal(action=SignalAction.HOLD)

        macd = self._fast_ema - self._slow_ema

        if self._signal is None:
            self._macd_seed.append(macd)
            if len(self._macd_seed) >= self.signal_period:
                self._signal = sum(self._macd_seed) / self.signal_period
            else:
                return Signal(action=SignalAction.HOLD)
        else:
            self._signal = macd * k_signal + self._signal * (1 - k_signal)

        macd_above_signal = macd > self._signal
        prev = self._prev_macd_above_signal
        self._prev_macd_above_signal = macd_above_signal

        if prev is None:
            # First point where macd/signal are both computable -- nothing to
            # cross from yet.
            return Signal(action=SignalAction.HOLD)
        if macd_above_signal and not prev:
            return Signal(action=SignalAction.BUY)
        if not macd_above_signal and prev:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)

    def debug_state(self) -> dict[str, float | None]:
        macd = (
            self._fast_ema - self._slow_ema
            if self._fast_ema is not None and self._slow_ema is not None
            else None
        )
        return {"macd": macd, "signal": self._signal}


__all__ = ["MacdTrendStrategy"]
