"""Protects the MCX-options regime classifier against the one bug that would
invalidate every regime-conditioned result: reading the future.

Mirrors `tests/research/wheel_basket/test_regime.py`'s discipline -- a
truncate-and-compare check is the cheapest possible proof that a label never
depends on a bar that had not printed yet.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from research.mcx_options.regime import (
    ADX_TREND_THRESHOLD,
    BANDWIDTH_PERCENTILE_WINDOW,
    LOW_BANDWIDTH_PERCENTILE,
    Regime,
    regime_by_day,
)

START = date(2022, 1, 3)


def _days(n: int) -> list[date]:
    return [START + timedelta(days=i) for i in range(n)]


def _bars(closes: list[float], *, high_pad: float = 1.0, low_pad: float = 1.0) -> pd.DataFrame:
    """A daily-bars frame shaped like `regime_by_day` expects: DatetimeIndex,
    `high`/`low`/`close` columns. `high`/`low` are a small fixed pad around
    `close` -- ADX needs true range, not just a close series.
    """
    idx = pd.DatetimeIndex(_days(len(closes)))
    return pd.DataFrame(
        {
            "high": [c + high_pad for c in closes],
            "low": [c - low_pad for c in closes],
            "close": closes,
        },
        index=idx,
    )


def _uptrend(n: int = 300) -> pd.DataFrame:
    """Compounding uptrend, deliberately not a straight ramp (a linear ramp
    can starve ADX/DI of the volatility they need) -- same reasoning as
    `wheel_basket/regime.py`'s test fixtures.
    """
    return _bars([100.0 * (1.0015**i) for i in range(n)])


def _downtrend(n: int = 300) -> pd.DataFrame:
    return _bars([100.0 * (0.9985**i) for i in range(n)])


def _flat_oscillating(n: int = 300) -> pd.DataFrame:
    """Tight chop: alternates by a small fraction of price, band-limited so
    both ADX stays low and Bollinger Bandwidth stays low.
    """
    return _bars([100.0 + (0.3 if i % 2 else -0.3) for i in range(n)], high_pad=0.4, low_pad=0.4)


def test_a_clean_uptrend_is_favorable_for_puts_and_unfavorable_for_calls():
    labels = regime_by_day(_uptrend())
    last = labels[_days(300)[-1]]
    assert last.raw == "uptrend"
    assert last.for_option_type("PE") == Regime.TREND_FAVORABLE
    assert last.for_option_type("CE") == Regime.TREND_UNFAVORABLE


def test_a_clean_downtrend_flips_favorability():
    labels = regime_by_day(_downtrend())
    last = labels[_days(300)[-1]]
    assert last.raw == "downtrend"
    assert last.for_option_type("CE") == Regime.TREND_FAVORABLE
    assert last.for_option_type("PE") == Regime.TREND_UNFAVORABLE


def test_a_flat_choppy_market_is_consolidating_for_both_sides():
    labels = regime_by_day(_flat_oscillating())
    last = labels[_days(300)[-1]]
    assert last.raw == "consolidating"
    assert last.for_option_type("PE") == Regime.CONSOLIDATING
    assert last.for_option_type("CE") == Regime.CONSOLIDATING


def test_labels_never_change_when_future_bars_arrive():
    """THE lookahead test -- mirrors
    `wheel_basket/test_regime.py::test_labels_never_change_when_future_bars_arrive`.
    """
    n = 300
    bars = _uptrend(n)
    full = regime_by_day(bars)

    cut_idx = 250
    truncated = bars.iloc[: cut_idx + 1]
    partial = regime_by_day(truncated)

    assert partial, "expected some labels before the cut"
    for day, label in partial.items():
        assert full[day] == label, f"label for {day} changed once the future arrived"


def test_insufficient_warmup_is_no_opinion_not_a_default():
    """5 bars is nowhere near ADX(14)/BBands(20) warm-up -- every day must
    be unlabelled, never silently defaulted to `consolidating`.
    """
    bars = _uptrend(5)
    labels = regime_by_day(bars)
    assert labels == {}


def test_declared_thresholds_are_not_silently_retuned():
    """Guards the declared constants against drift -- see module docstring
    for why these particular values were picked.
    """
    assert ADX_TREND_THRESHOLD == 25
    assert LOW_BANDWIDTH_PERCENTILE == 0.25
    assert BANDWIDTH_PERCENTILE_WINDOW == 60


def test_for_option_type_rejects_an_unknown_side():
    labels = regime_by_day(_uptrend())
    last = labels[_days(300)[-1]]
    with pytest.raises(ValueError):
        last.for_option_type("XX")
