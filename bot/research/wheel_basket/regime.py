"""What state is the market in, judged only on what was knowable that day.

Every threshold here is DECLARED in `docs/wheel-basket-research.md` Sec 5 and
none is swept. A swept threshold is a hidden trial, and this study's whole
claim to being believable is that its trial count is 12 rather than 432.

THE ONE BUG THAT WOULD INVALIDATE EVERYTHING is reading a bar at or after the
day being labelled. The defence is structural rather than careful: the
indicators are driven by feeding bars in order through the shared strategy
registry and reading `debug_state()` after each one -- the same streaming
pattern `research/stock_options/run_strategies.trend_bullish_by_day` uses --
so a future bar has not been constructed yet when a label is emitted. The
test suite asserts the property directly anyway.

Indicator parameters are the conventional ones (SMA 50/200, MACD 12/26/9)
chosen BECAUSE they are conventional. Picking index-trend parameters that
flattered this particular seven years would be the same best-of-N error
docs/crosstrend-results.md was written to record.
"""
from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Mapping, Optional

from growmore_bot.strategies.registry import build_strategy

#: Declared in the research doc, Sec 5. Do not tune these.
SMA_FAST = 50
SMA_SLOW = 200
MACD_PARAMS = {"fast_period": 12, "slow_period": 26, "signal_period": 9}
VIX_PERCENTILE_WINDOW = 504          # ~2 years of trading days
VIX_HIGH_PERCENTILE = 0.80

REGIMES = ("high_vol", "bull", "bearish", "consolidating")


def _percentile_of_last(window: deque) -> float:
    """Where the newest reading sits within its own trailing window.

    Fraction of the window strictly below it -- the same tie convention
    `growmore_bot.wheel_basket.scoring.iv_percentile_ranks` uses, so a flat
    VIX reads as 0.0 (unremarkable) rather than 1.0 (panic).
    """
    latest = window[-1]
    return sum(1 for v in window if v < latest) / len(window)


def regime_by_day(
    index_closes: Mapping,
    vix_closes: Mapping,
) -> dict:
    """Label every day for which a full warm-up exists.

    A day with no VIX reading is left UNLABELLED rather than assumed calm --
    a data gap must not become a "safe to sell" signal. Callers treat a
    missing label as "no regime opinion", which every variant defines as
    behaving exactly like the baseline.
    """
    days = sorted(index_closes)
    macd = build_strategy("macd_trend", dict(MACD_PARAMS))
    closes: deque = deque(maxlen=SMA_SLOW)
    vix_window: deque = deque(maxlen=VIX_PERCENTILE_WINDOW)
    out: dict = {}

    for day in days:
        close = float(index_closes[day])
        closes.append(close)
        macd.on_bar(
            SimpleNamespace(
                timestamp=day, open=close, high=close, low=close,
                close=close, volume=0.0,
            ),
            None,
        )

        vix = vix_closes.get(day)
        if vix is not None:
            vix_window.append(float(vix))

        label = _classify(closes, vix_window, macd, has_vix_today=vix is not None)
        if label is not None:
            out[day] = label
    return out


def _classify(closes: deque, vix_window: deque, macd, has_vix_today: bool) -> Optional[str]:
    if len(closes) < SMA_SLOW or not has_vix_today:
        return None
    if len(vix_window) < VIX_PERCENTILE_WINDOW:
        return None

    if _percentile_of_last(vix_window) >= VIX_HIGH_PERCENTILE:
        return "high_vol"

    close = closes[-1]
    sma_slow = sum(closes) / len(closes)
    recent = list(closes)[-SMA_FAST:]
    sma_fast = sum(recent) / len(recent)

    state = macd.debug_state()
    macd_line, signal = state.get("macd"), state.get("signal")
    macd_bullish = (
        macd_line is not None and signal is not None and macd_line > signal
    )

    if close > sma_slow and sma_fast > sma_slow and macd_bullish:
        return "bull"
    if close < sma_slow and sma_fast < sma_slow:
        return "bearish"
    return "consolidating"


__all__ = [
    "REGIMES", "SMA_FAST", "SMA_SLOW", "MACD_PARAMS",
    "VIX_PERCENTILE_WINDOW", "VIX_HIGH_PERCENTILE", "regime_by_day",
]
