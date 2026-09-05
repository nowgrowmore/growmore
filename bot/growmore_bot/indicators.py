"""Shared streaming indicators.

Stdlib only, and deliberately so: `growmore_bot` imports neither pandas nor
numpy anywhere, because the same indicator objects have to run inside the
backtest (replaying thousands of bars) AND inside the live/paper engines
(fed one bar per tick). Anything that needs a whole array in memory can't do
both. Everything here is O(1) per bar over `collections.deque` and running
sums.

`WilderSmoother` was previously private to `strategies/regime_switch.py`;
it lives here now so ATR and ADX provably share one smoothing convention
rather than drifting apart. That convention is `ewm(alpha=1/period,
adjust=False)` -- recursion from the very first value, masked to None until
`period` values have been fed (mirroring pandas' `min_periods`) -- NOT
Wilder's classical SMA-seeded initialization. That choice is documented at
length in regime_switch.py's own docstring; it is the convention most
charting libraries ship under the "Wilder smoothing" name, and it is exactly
reproducible via pandas, which is how the fixtures here were independently
verified.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Optional


class WilderSmoother:
    """`ewm(alpha=1/period, adjust=False)`: seeded with the first value, then
    `value = alpha*x + (1-alpha)*value`. Returns None until `period` values
    have been fed, mirroring pandas' `min_periods=period` masking.
    """

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be positive")
        self._alpha = 1.0 / period
        self._period = period
        self._value: Optional[float] = None
        self._count = 0

    def update(self, x: float) -> Optional[float]:
        self._count += 1
        self._value = x if self._value is None else self._alpha * x + (1 - self._alpha) * self._value
        return self._value if self._count >= self._period else None

    @property
    def value(self) -> Optional[float]:
        return self._value if self._count >= self._period else None


def true_range(bar: Any, prev_bar: Any) -> float:
    """Wilder's True Range: the widest of (high-low), |high-prev_close| and
    |low-prev_close|.

    With no prior bar only `high - low` survives -- matching the reference
    calculation's NaN-skipping max(), and matching what
    `regime_switch._AdxCalculator` has always done on its first bar.
    """
    if prev_bar is None:
        return float(bar.high) - float(bar.low)
    prev_close = float(prev_bar.close)
    return max(
        float(bar.high) - float(bar.low),
        abs(float(bar.high) - prev_close),
        abs(float(bar.low) - prev_close),
    )


class AtrCalculator:
    """Wilder ATR -- the smoothed True Range, in the instrument's own price
    units (NOT a percentage, and NOT rupees per lot; multiply by lot_size for
    that -- see growmore_bot.risk.sizing).

    Shares `WilderSmoother` with `_AdxCalculator`, which already computed
    exactly this value internally and threw it away.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("period must be positive")
        self._period = period
        self._smoother = WilderSmoother(period)
        self._prev_bar: Any = None

    def update(self, bar: Any) -> Optional[float]:
        tr = true_range(bar, self._prev_bar)
        self._prev_bar = bar
        return self._smoother.update(tr)

    @property
    def value(self) -> Optional[float]:
        return self._smoother.value


class RealizedVolCalculator:
    """Annualized realised volatility: the population standard deviation of
    log close-to-close returns over a rolling window, times sqrt(periods per
    year).

    Population (not sample) stdev, matching every other volatility/stdev
    calculation in this project (`backtest.metrics.sharpe_ratio`,
    `BollingerReversionStrategy`, `research...portfolio_engine._annualized_vol`).
    Returns None until `window` RETURNS are available, i.e. after window+1
    closes.

    A non-positive close (which has no log) is skipped rather than raising --
    a bad print shouldn't take down a live tick.
    """

    def __init__(self, window: int = 20, periods_per_year: int = 252) -> None:
        if window < 2:
            raise ValueError("window must be at least 2")
        if periods_per_year < 1:
            raise ValueError("periods_per_year must be positive")
        self._window = window
        self._annualization = math.sqrt(periods_per_year)
        self._returns: deque[float] = deque(maxlen=window)
        self._prev_close: Optional[float] = None

    def update(self, close: float) -> Optional[float]:
        close = float(close)
        if close <= 0:
            # No log return is definable; leave the window untouched.
            return self.value
        if self._prev_close is not None:
            self._returns.append(math.log(close / self._prev_close))
        self._prev_close = close
        return self.value

    @property
    def value(self) -> Optional[float]:
        if len(self._returns) < self._window:
            return None
        n = len(self._returns)
        mean = sum(self._returns) / n
        variance = sum((r - mean) ** 2 for r in self._returns) / n
        return math.sqrt(variance) * self._annualization


__all__ = ["WilderSmoother", "true_range", "AtrCalculator", "RealizedVolCalculator"]
