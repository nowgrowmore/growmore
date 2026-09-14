"""Which market regime an MCX commodity FUTURES series is in TODAY, and what
that regime means for a put-seller versus a call-seller.

Live analog of `research/mcx_options/regime.py` -- same 3-way `Regime`
concept, same per-side mapping (`RegimeLabel.for_option_type`), same
"insufficient data or missing = no opinion, never permissive" discipline --
but reimplemented hand-rolled/streaming rather than vectorised over a
`pandas`/`pandas_ta_classic` DataFrame, and classifying only the LAST bar of
a trailing window ("what is today's regime") rather than labelling an entire
historical series.

**Why hand-rolled instead of importing pandas_ta_classic**: `pandas_ta_classic`
is declared a `research`-only optional dependency in bot/pyproject.toml,
whose own docstring says it is "only for research, never imported by the
live trading bot" -- `growmore_bot` stays dependency-light (no pandas/numpy
anywhere), the same reasoning `growmore_bot/indicators.py`'s own docstring
gives: one streaming indicator object usable both live and in a backtest.
ADX/+DI/-DI and Bollinger Bandwidth are well-defined, standard formulas, and
this repo already has a live streaming ADX (`growmore_bot/strategies/
regime_switch.py`'s `_AdxCalculator`, built on `growmore_bot.indicators`'
`WilderSmoother`/`AtrCalculator`) -- reused here rather than reimplemented a
third time, so ADX can never drift between the regime-switch strategy and
this engine.

**Indicators and thresholds** (mirrors research/mcx_options/regime.py's
declared, conventional values -- not tuned to flatter any result):
  - ADX(14) / +DI(14) / -DI(14), Wilder-style smoothing via
    `growmore_bot.indicators.WilderSmoother`/`AtrCalculator` (the
    `ewm(alpha=1/period, adjust=False)` convention documented at length in
    `regime_switch.py`'s own docstring).
  - `ADX_TREND_THRESHOLD = 25`.
  - Bollinger Bandwidth(20, 2.0) = (upper - lower) / mean, hand-rolled
    exactly like `growmore_bot/strategies/bollinger_reversion.py`'s bands
    (population stdev, matching every other stdev calc in this project).
  - `BANDWIDTH_PERCENTILE_WINDOW = 60`: a trailing ~3-month window: the
    newest bandwidth reading's percentile rank within it. Required for
    warm-up (same as the research module) but -- per that module's own
    reasoning -- does not gate a *different* classification outcome once
    ADX has ruled out both trend directions; it is still computed and
    required to be real so a "consolidating" read is genuinely earned by
    having reached full warm-up, not produced from a partially-warmed
    ADX-only calculation.

**Non-lookahead**: every value used here is a strict function of bars up to
and including the one being classified (Wilder smoothing recurses forward
from the first bar fed; the Bollinger window and percentile window are both
trailing) -- feeding a longer window and reading the same final bar gives an
identical label to feeding a window truncated at that bar. Proven by
`tests/growmore_bot/mcx_options/test_regime.py::test_labels_never_change_when_future_bars_arrive`.

Callers pass a plain trailing list of bar-like objects (anything with
`.high`/`.low`/`.close`, e.g. `growmore_bot.broker.dhan_client.Bar`) sorted
oldest-to-newest, via `classify_today`.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Sequence

from growmore_bot.indicators import AtrCalculator
from growmore_bot.indicators import WilderSmoother as _WilderSmoother

#: Declared, conventional values -- see module docstring. Do not tune these
#: to flatter any particular result.
ADX_LENGTH = 14
BBANDS_LENGTH = 20
BBANDS_NUM_STD = 2.0
ADX_TREND_THRESHOLD = 25
BANDWIDTH_PERCENTILE_WINDOW = 60


class Regime(str, Enum):
    """One side's read of a day's regime. A `str` subclass so
    `label.for_option_type("PE") == "trend_favorable"` reads naturally in
    tests and logs without an extra `.value`.
    """

    CONSOLIDATING = "consolidating"
    TREND_FAVORABLE = "trend_favorable"
    TREND_UNFAVORABLE = "trend_unfavorable"


@dataclass(frozen=True)
class RegimeLabel:
    """Both sides' regime read for today, plus the raw trend direction they
    were derived from (`"uptrend"`, `"downtrend"`, or `"consolidating"`) for
    callers that want the underlying signal rather than a per-side spin.
    """

    raw: str
    put: Regime
    call: Regime

    def for_option_type(self, option_type: str) -> Regime:
        if option_type == "PE":
            return self.put
        if option_type == "CE":
            return self.call
        raise ValueError(f"option_type must be 'PE' or 'CE', got {option_type!r}")


def _label_for(raw: str) -> RegimeLabel:
    if raw == "uptrend":
        return RegimeLabel(raw=raw, put=Regime.TREND_FAVORABLE, call=Regime.TREND_UNFAVORABLE)
    if raw == "downtrend":
        return RegimeLabel(raw=raw, put=Regime.TREND_UNFAVORABLE, call=Regime.TREND_FAVORABLE)
    return RegimeLabel(raw=raw, put=Regime.CONSOLIDATING, call=Regime.CONSOLIDATING)


def _classify_raw(adx: float, di_plus: float, di_minus: float) -> str:
    """Pure classification given already-warmed-up ADX/+DI/-DI for a single
    day -- same residual-bucket shape as research/mcx_options/regime.py's
    `_classify_raw` (a bandwidth-percentile tie is not needed to reach
    "consolidating": once ADX rules out both trend directions, it's the
    only state left in this three-way label).
    """
    if adx >= ADX_TREND_THRESHOLD:
        if di_plus > di_minus:
            return "uptrend"
        if di_minus > di_plus:
            return "downtrend"
    return "consolidating"


def classify_today(bars: Sequence[Any]) -> Optional[RegimeLabel]:
    """Today's (the last bar's) regime label, or None if there isn't enough
    trailing history to warm up ADX(14) AND Bollinger Bandwidth(20) AND its
    60-day percentile rank -- "no opinion", never a silently permissive
    default. `bars` must be sorted oldest-to-newest and expose
    `.high`/`.low`/`.close` (e.g. `growmore_bot.broker.dhan_client.Bar`).
    """
    if not bars:
        return None

    prev_bar: Any = None
    atr = AtrCalculator(ADX_LENGTH)
    plus_dm_smoother = _WilderSmoother(ADX_LENGTH)
    minus_dm_smoother = _WilderSmoother(ADX_LENGTH)
    dx_smoother = _WilderSmoother(ADX_LENGTH)

    closes: deque[float] = deque(maxlen=BBANDS_LENGTH)
    bandwidths: deque[float] = deque(maxlen=BANDWIDTH_PERCENTILE_WINDOW)

    adx: Optional[float] = None
    di_plus: Optional[float] = None
    di_minus: Optional[float] = None
    bbb_pct: Optional[float] = None

    for bar in bars:
        # ---- ADX / +DI / -DI (mirrors regime_switch._AdxCalculator.update) --
        if prev_bar is None:
            plus_dm = 0.0
            minus_dm = 0.0
        else:
            up_move = bar.high - prev_bar.high
            down_move = prev_bar.low - bar.low
            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        prev_bar = bar

        tr_smooth = atr.update(bar)
        plus_dm_smooth = plus_dm_smoother.update(plus_dm)
        minus_dm_smooth = minus_dm_smoother.update(minus_dm)

        if (
            tr_smooth is None
            or plus_dm_smooth is None
            or minus_dm_smooth is None
            or tr_smooth == 0
        ):
            adx, di_plus, di_minus = None, None, None
        else:
            di_plus = 100 * plus_dm_smooth / tr_smooth
            di_minus = 100 * minus_dm_smooth / tr_smooth
            di_sum = di_plus + di_minus
            dx = 0.0 if di_sum == 0 else 100 * abs(di_plus - di_minus) / di_sum
            adx = dx_smoother.update(dx)

        # ---- Bollinger Bandwidth(20, 2.0) and its 60-day percentile rank ---
        closes.append(float(bar.close))
        bbb_pct = None
        if len(closes) == BBANDS_LENGTH:
            mean = sum(closes) / BBANDS_LENGTH
            variance = sum((c - mean) ** 2 for c in closes) / BBANDS_LENGTH
            std = math.sqrt(variance)
            if mean != 0:
                upper = mean + BBANDS_NUM_STD * std
                lower = mean - BBANDS_NUM_STD * std
                bandwidths.append((upper - lower) / mean)
        if len(bandwidths) == BANDWIDTH_PERCENTILE_WINDOW:
            latest = bandwidths[-1]
            bbb_pct = sum(1 for v in bandwidths if v < latest) / BANDWIDTH_PERCENTILE_WINDOW

    if adx is None or di_plus is None or di_minus is None or bbb_pct is None:
        return None

    raw = _classify_raw(adx, di_plus, di_minus)
    return _label_for(raw)


__all__ = [
    "ADX_LENGTH",
    "BBANDS_LENGTH",
    "ADX_TREND_THRESHOLD",
    "BANDWIDTH_PERCENTILE_WINDOW",
    "Regime",
    "RegimeLabel",
    "classify_today",
]
