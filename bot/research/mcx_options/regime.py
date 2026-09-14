"""Which market regime an MCX commodity FUTURES series is in, judged only on
what was knowable that day -- and what that regime means for a put-seller
versus a call-seller.

Mirrors `research/wheel_basket/regime.py`'s discipline (see that module's
docstring): every threshold here is a DECLARED, conventional value, not swept
against this study's own results, and a day with insufficient warm-up data is
left with NO LABEL rather than silently defaulted to something permissive.
`regime_by_day`'s output dict simply has no entry for such a day; callers
must treat a missing day as "no regime opinion", never as "consolidating".

**Non-lookahead.** Unlike `wheel_basket/regime.py`, this module does not
stream bars one at a time through an incremental indicator object -- there is
no such object for `pandas_ta`'s ADX/Bollinger-Bandwidth, which are vectorised
over a whole `DataFrame`. Instead it relies on (and the test suite proves,
mirroring `wheel_basket`'s own truncate-and-compare test) the fact that every
indicator used here is CAUSAL: ADX/+DI/-DI is Wilder's recursive smoothing
seeded at the start of the series, and Bollinger Bandwidth/its percentile
rank are trailing rolling windows -- neither is centred and neither consults
a bar after the one being labelled. Computing the full frame once and reading
row N therefore gives exactly the same value `regime_by_day` would give if
the frame were truncated to end at row N. That equivalence, not blind trust
in the library, is what `test_labels_never_change_when_future_bars_arrive`
checks.

**Indicators and thresholds** (all via `pandas_ta`'s `df.ta.*` accessor,
provided in this environment by the `pandas-ta-classic` package -- see
`pyproject.toml`'s `research` extra for why):
  - ADX(14) / +DI(14) / -DI(14) via `df.ta.adx(length=14)`, columns
    `ADX_14`, `DMP_14` (+DI), `DMN_14` (-DI). 14 is Wilder's own original
    parameter and the universal default.
  - `ADX_TREND_THRESHOLD = 25`: the conventional Wilder cut for "a real
    trend is present" (below ~20 is textbook "no trend"; 25 is the more
    conservative, commonly-cited line and is used here as a single
    threshold rather than sweeping a 20-25 band).
  - Bollinger Bandwidth(20, 2.0) via `df.ta.bbands(length=20)`, column
    `BBB_20_2.0` (located by prefix, not hardcoded, since the exact suffix
    depends on the std-dev multiplier and library version).
  - `BANDWIDTH_PERCENTILE_WINDOW = 60` / `LOW_BANDWIDTH_PERCENTILE = 0.25`:
    a trailing ~3-month (60 trading day) window, tight bands defined as the
    newest reading sitting in the bottom quartile of that window. Shorter
    than `wheel_basket/regime.py`'s 504-day VIX window on purpose -- this is
    a per-underlying-future compression read, not an index-wide vol regime,
    and MCX option chains commonly don't have multiple years of history to
    draw on.

**Classification** (see `_classify_raw`):
  - ADX >= `ADX_TREND_THRESHOLD` and +DI > -DI  -> `"uptrend"`.
  - ADX >= `ADX_TREND_THRESHOLD` and -DI > +DI  -> `"downtrend"`.
  - Anything else (ADX below threshold, or a +DI/-DI tie at high ADX) ->
    `"consolidating"`. The bandwidth-percentile check is computed and
    required for warm-up either way (so a tight-band day genuinely earns
    the `"consolidating"` read rather than getting it only by elimination),
    but does not need to gate a *different* outcome: once ADX has ruled out
    both trend directions, `"consolidating"` is the only state left in this
    three-way label. This is the same "residual bucket" shape
    `wheel_basket/regime.py._classify` uses for its own `"consolidating"`.

**Per-side mapping** (`RegimeLabel.for_option_type`): a put SELLER wants the
underlying not to fall, so an uptrend is favorable and a downtrend is
unfavorable for "PE"; a call seller wants the mirror image for "CE".
Consolidating is neutral-ish for both (a premium seller's classic
regime), so it maps to `Regime.CONSOLIDATING` on both sides.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd
import pandas_ta_classic  # noqa: F401  -- registers the `.ta` DataFrame accessor

#: Declared, conventional values -- see module docstring. Do not tune these
#: to flatter any particular backtest.
ADX_LENGTH = 14
BBANDS_LENGTH = 20
ADX_TREND_THRESHOLD = 25
BANDWIDTH_PERCENTILE_WINDOW = 60
LOW_BANDWIDTH_PERCENTILE = 0.25

_OPTION_TYPES = ("PE", "CE")


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
    """Both sides' regime read for one day, plus the raw trend direction
    they were derived from (`"uptrend"`, `"downtrend"`, or `"consolidating"`)
    for callers that want the underlying signal rather than a per-side spin.
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


def _percentile_of_last(window) -> float:
    """Where the newest reading sits within its own trailing window.

    Fraction of the window strictly below it -- the same tie convention as
    `wheel_basket/regime.py._percentile_of_last`, so a flat bandwidth reads
    0.0 (not remarkably tight) rather than 1.0.
    """
    latest = window[-1]
    return sum(1 for v in window if v < latest) / len(window)


def _bandwidth_column(columns) -> Optional[str]:
    prefix = "BBB_"
    for col in columns:
        if col.startswith(prefix):
            return col
    return None


def _classify_raw(adx: float, di_plus: float, di_minus: float, bbb_pct: float) -> str:
    """Pure classification given already-warmed-up indicator readings for a
    single day. `bbb_pct` is required to be a real (non-NaN) number by the
    caller -- see module docstring on why it still doesn't change the
    ADX-only outcome in the residual branch.
    """
    if adx >= ADX_TREND_THRESHOLD:
        if di_plus > di_minus:
            return "uptrend"
        if di_minus > di_plus:
            return "downtrend"
    return "consolidating"


def _label_for(raw: str) -> RegimeLabel:
    if raw == "uptrend":
        return RegimeLabel(raw=raw, put=Regime.TREND_FAVORABLE, call=Regime.TREND_UNFAVORABLE)
    if raw == "downtrend":
        return RegimeLabel(raw=raw, put=Regime.TREND_UNFAVORABLE, call=Regime.TREND_FAVORABLE)
    return RegimeLabel(raw=raw, put=Regime.CONSOLIDATING, call=Regime.CONSOLIDATING)


def regime_by_day(futures_daily_bars: pd.DataFrame) -> dict:
    """Label every day for which ADX(14), Bollinger Bandwidth(20) AND its
    60-day percentile rank are all real, warmed-up numbers.

    `futures_daily_bars` must have `high`, `low`, `close` columns and a
    `DatetimeIndex` sorted ascending (one row per trading day). A day
    missing any required indicator (insufficient warm-up, or a NaN in the
    inputs) is left OUT of the returned dict entirely -- "no opinion", never
    a silent default. See module docstring for the non-lookahead argument.
    """
    if futures_daily_bars.empty:
        return {}

    bars = futures_daily_bars.sort_index()

    adx_df = bars.ta.adx(length=ADX_LENGTH)
    bbands_df = bars.ta.bbands(length=BBANDS_LENGTH)
    adx_col, dmp_col, dmn_col = f"ADX_{ADX_LENGTH}", f"DMP_{ADX_LENGTH}", f"DMN_{ADX_LENGTH}"
    bbb_col = _bandwidth_column(bbands_df.columns) if bbands_df is not None else None

    if (
        adx_df is None
        or bbb_col is None
        or adx_col not in adx_df.columns
        or dmp_col not in adx_df.columns
        or dmn_col not in adx_df.columns
    ):
        # pandas_ta_classic returns either `None` or (when the input is
        # shorter than the indicator's minimum length, e.g. fewer than 14
        # rows for ADX(14)) the input frame echoed back UNCHANGED, with none
        # of the indicator's own columns added. Either way that means no
        # warm-up at all, so no day gets a label -- "missing data => no
        # opinion", never a silent default.
        return {}

    bbb = bbands_df[bbb_col]
    bbb_pct = bbb.rolling(window=BANDWIDTH_PERCENTILE_WINDOW, min_periods=BANDWIDTH_PERCENTILE_WINDOW).apply(
        lambda w: _percentile_of_last(list(w)), raw=False
    )

    out: dict = {}
    for day in bars.index:
        adx = adx_df.loc[day, adx_col]
        di_plus = adx_df.loc[day, dmp_col]
        di_minus = adx_df.loc[day, dmn_col]
        pct = bbb_pct.loc[day]

        if pd.isna(adx) or pd.isna(di_plus) or pd.isna(di_minus) or pd.isna(pct):
            continue

        raw = _classify_raw(float(adx), float(di_plus), float(di_minus), float(pct))
        out[day.date() if hasattr(day, "date") else day] = _label_for(raw)
    return out


__all__ = [
    "ADX_LENGTH",
    "BBANDS_LENGTH",
    "ADX_TREND_THRESHOLD",
    "BANDWIDTH_PERCENTILE_WINDOW",
    "LOW_BANDWIDTH_PERCENTILE",
    "Regime",
    "RegimeLabel",
    "regime_by_day",
]
