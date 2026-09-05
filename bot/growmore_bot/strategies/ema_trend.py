"""Slow trend: long while price is above a single long EMA, flat below.

Every trend variant this repo has tested lives at the FAST end of the
spectrum. The incumbent is MACD(5,13,5) -- 5- and 13-day EMAs. The slowest
standalone thing in the grid is Donchian-55, and the ensemble's slowest member
is MACD(26,52,18). The three-weeks-to-three-months region where the
trend-following literature puts the Sharpe optimum has never been sampled here
at all.

"Breaking the Trend: How to Avoid Cherry-Picked Signals" (arXiv 2504.10914)
goes further and is worth stating because it argues against this codebase's own
ensemble: a SINGLE EMA at a ~112-business-day lookback posted Sharpe 1.24 on a
diversified futures book, against 1.18 for a three-timescale MACD, and the
authors show EMA(80) and EMA(150) correlate at 0.96 while a multi-timescale
MACD correlates 0.99+ with a plain EMA(120). On that evidence extra speeds buy
correlation, not diversification.

Deliberately as simple as the argument requires: no signal line, no second
EMA, one parameter. The EMA seeds as an SMA of its first `period` closes and
then runs the standard recurrence, matching MacdTrendStrategy exactly so the
two are comparable.

Read the honest caveat before quoting any result: at `period=112` on five
years of daily bars this fires roughly ten times. Ten trades cannot separate a
Sharpe of 1.2 from a Sharpe of 0.4, so on THIS dataset the strategy is close
to untestable, and the trade count belongs next to every figure it produces.
"""
from __future__ import annotations

from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class EmaTrendStrategy(Strategy):
    def __init__(self, period: int) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        self.period = period
        self._seed: list[float] = []
        self._ema: Optional[float] = None
        self._prev_above: Optional[bool] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        close = float(bar.close)
        k = 2.0 / (self.period + 1)

        if self._ema is None:
            self._seed.append(close)
            if len(self._seed) < self.period:
                return Signal(action=SignalAction.HOLD)
            self._ema = sum(self._seed) / self.period
        else:
            self._ema = close * k + self._ema * (1 - k)

        above = close > self._ema
        if self._prev_above is None:
            # The first computable bar establishes the reference. Acting on it
            # would turn "we just started watching" into a trade.
            self._prev_above = above
            return Signal(action=SignalAction.HOLD)

        prev, self._prev_above = self._prev_above, above
        if above and not prev:
            return Signal(action=SignalAction.BUY)
        if prev and not above:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)

    def debug_state(self) -> dict[str, Optional[float]]:
        return {
            "ema": self._ema,
            # `stance` is what research/crosstrend/companion.py reads; exposing
            # it here means this strategy needs no adapter branch of its own.
            "stance": None if self._prev_above is None else float(self._prev_above),
        }

    def get_state_snapshot(self) -> dict[str, Any]:
        if self._ema is None and self._prev_above is None:
            return {}
        return {"ema": self._ema, "prev_above": self._prev_above, "seed": list(self._seed)}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not snapshot:
            return
        self._ema = snapshot.get("ema")
        self._prev_above = snapshot.get("prev_above")
        self._seed = list(snapshot.get("seed") or [])
