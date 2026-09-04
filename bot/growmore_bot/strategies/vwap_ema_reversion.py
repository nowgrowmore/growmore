"""Rolling-VWAP + EMA mean-reversion strategy.

A second, *daily-bar* option for the "ranging" side of RegimeSwitchStrategy,
alongside the already-built RsiMeanReversionStrategy: BUY when price closes
back ABOVE a rolling N-day volume-weighted average price (a support bounce)
while the fast EMA is at/above the slow EMA (mild directional confirmation);
SELL on the mirror crossing below.

This is deliberately a *rolling N-day* VWAP computed from historical daily
bars (`Bar.volume`), NOT the live intraday session VWAP Dhan's own quote
response provides (`Quote.vwap`, used by vwap_session_bounce.py) -- a
different indicator that happens to share a name. This one is backtestable
over the full 5-year history already fetched; the live session VWAP can't
be, since it doesn't exist in historical bars at all. See
docs/goldmini-regime-switch-results.md for why both exist.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class VwapEmaReversionStrategy(Strategy):
    def __init__(self, vwap_period: int, ema_fast: int, ema_slow: int) -> None:
        if vwap_period < 1:
            raise ValueError("vwap_period must be positive")
        if ema_fast < 1 or ema_slow < 1:
            raise ValueError("EMA periods must be positive")
        if ema_fast >= ema_slow:
            raise ValueError("ema_fast must be less than ema_slow")
        self.vwap_period = vwap_period
        self.ema_fast_period = ema_fast
        self.ema_slow_period = ema_slow

        self._tp_vol: deque[tuple[float, float]] = deque(maxlen=vwap_period)
        self._closes_seed: list[float] = []
        self._fast_ema: Optional[float] = None
        self._slow_ema: Optional[float] = None
        self._last_vwap: Optional[float] = None
        self._prev_above_vwap: Optional[bool] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        close = float(bar.close)
        typical_price = (float(bar.high) + float(bar.low) + close) / 3
        volume = float(getattr(bar, "volume", 0) or 0)
        self._tp_vol.append((typical_price * volume, volume))

        self._closes_seed.append(close)
        k_fast = 2.0 / (self.ema_fast_period + 1)
        k_slow = 2.0 / (self.ema_slow_period + 1)
        if self._fast_ema is None:
            if len(self._closes_seed) >= self.ema_fast_period:
                self._fast_ema = sum(self._closes_seed[-self.ema_fast_period :]) / self.ema_fast_period
        else:
            self._fast_ema = close * k_fast + self._fast_ema * (1 - k_fast)
        if self._slow_ema is None:
            if len(self._closes_seed) >= self.ema_slow_period:
                self._slow_ema = sum(self._closes_seed[-self.ema_slow_period :]) / self.ema_slow_period
        else:
            self._slow_ema = close * k_slow + self._slow_ema * (1 - k_slow)

        if len(self._tp_vol) < self.vwap_period or self._fast_ema is None or self._slow_ema is None:
            return Signal(action=SignalAction.HOLD)

        total_volume = sum(v for _, v in self._tp_vol)
        if total_volume == 0:
            return Signal(action=SignalAction.HOLD)
        vwap = sum(tv for tv, _ in self._tp_vol) / total_volume
        self._last_vwap = vwap

        above_vwap = close > vwap
        prev = self._prev_above_vwap
        self._prev_above_vwap = above_vwap

        if prev is None:
            return Signal(action=SignalAction.HOLD)
        if above_vwap and not prev and self._fast_ema >= self._slow_ema:
            return Signal(action=SignalAction.BUY)
        if not above_vwap and prev and self._fast_ema <= self._slow_ema:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)

    def debug_state(self) -> dict[str, Optional[float]]:
        return {"vwap": self._last_vwap, "ema_fast": self._fast_ema, "ema_slow": self._slow_ema}

    def get_state_snapshot(self) -> dict[str, Any]:
        if self._prev_above_vwap is None:
            return {}
        return {"prev_above_vwap": self._prev_above_vwap}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "prev_above_vwap" in snapshot:
            self._prev_above_vwap = snapshot["prev_above_vwap"]


__all__ = ["VwapEmaReversionStrategy"]
