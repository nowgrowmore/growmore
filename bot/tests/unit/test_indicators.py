"""Tests for growmore_bot.indicators.

Expected values are computed INDEPENDENTLY via pandas -- `ewm(alpha=1/period,
adjust=False)` for ATR, and `rolling(w).std(ddof=0) * sqrt(252)` on log
returns for realised volatility -- on the same synthetic OHLC series the
existing `_AdxCalculator` fixtures use (see test_regime_switch.py). Wilder
smoothing over 20+ bars isn't practical to verify by hand any other way, and
holding ATR to the same reference standard as ADX is the point: they share a
smoother, so they must agree.

`growmore_bot` itself imports neither pandas nor numpy (indicators are
hand-written streaming state so identical code runs in backtest and live) --
but tests may, and do, use pandas as the oracle.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.indicators import AtrCalculator, RealizedVolCalculator, WilderSmoother, true_range


def _bar(high, low, close):
    return SimpleNamespace(high=high, low=low, close=close)


def _trend_then_chop_bars():
    """Identical to test_regime_switch.py's series, so ATR and ADX are
    verified against the same price path."""
    bars = []
    price = 100.0
    for _ in range(20):
        price += 2.0
        bars.append(_bar(price + 1.0, price - 1.0, price))
    chop_base = price
    for i in range(15):
        wiggle = 3.0 * (1 if i % 2 == 0 else -1)
        p = chop_base + wiggle
        bars.append(_bar(p + 1.5, p - 1.5, p))
    return bars


class TestTrueRange:
    def test_first_bar_has_no_prior_close_so_only_high_minus_low_survives(self):
        # Matches _AdxCalculator's own first-bar convention (regime_switch.py):
        # the reference calc's NaN-skipping max() leaves just high-low.
        assert true_range(_bar(102, 100, 101), None) == pytest.approx(2.0)

    def test_uses_the_widest_of_the_three_wilder_legs(self):
        prev = _bar(100, 98, 99)
        # A gap up: high-prev_close (110-99=11) beats high-low (110-105=5).
        assert true_range(_bar(110, 105, 108), prev) == pytest.approx(11.0)
        # A gap down: prev_close-low (99-88=11) beats high-low (92-88=4).
        assert true_range(_bar(92, 88, 90), prev) == pytest.approx(11.0)
        # No gap: the bar's own range wins.
        assert true_range(_bar(101, 95, 100), prev) == pytest.approx(6.0)


class TestAtrCalculator:
    # Independently computed (pandas ewm reference, see module docstring).
    _EXPECTED = {13: 2.618408, 14: 2.645665, 20: 2.879996, 26: 4.538347, 34: 5.862970}

    def test_masked_until_period_bars_seen(self):
        calc = AtrCalculator(period=14)
        bars = _trend_then_chop_bars()
        for bar in bars[:13]:
            assert calc.update(bar) is None
        assert calc.update(bars[13]) is not None

    def test_matches_the_pandas_wilder_reference(self):
        calc = AtrCalculator(period=14)
        for i, bar in enumerate(_trend_then_chop_bars()):
            value = calc.update(bar)
            if i in self._EXPECTED:
                assert value == pytest.approx(self._EXPECTED[i], abs=1e-4)

    def test_value_property_matches_the_last_update(self):
        calc = AtrCalculator(period=14)
        last = None
        for bar in _trend_then_chop_bars():
            last = calc.update(bar)
        assert calc.value == pytest.approx(last)

    def test_rejects_a_non_positive_period(self):
        with pytest.raises(ValueError):
            AtrCalculator(period=0)


class TestRealizedVolCalculator:
    def test_masked_until_the_window_is_full(self):
        calc = RealizedVolCalculator(window=10)
        # Needs `window` RETURNS, i.e. window+1 closes.
        for close in [100.0 + i for i in range(10)]:
            assert calc.update(close) is None
        assert calc.update(110.0) is not None

    def test_matches_the_pandas_reference(self):
        # Independently computed: rolling(w).std(ddof=0) * sqrt(252) on log returns.
        closes = [b.close for b in _trend_then_chop_bars()]
        for window, expected_at_34 in [(10, 0.680440), (20, 0.581280)]:
            calc = RealizedVolCalculator(window=window)
            value = None
            for close in closes:
                value = calc.update(close)
            assert value == pytest.approx(expected_at_34, abs=1e-5)

    def test_a_perfectly_flat_series_has_zero_volatility_not_a_crash(self):
        calc = RealizedVolCalculator(window=5)
        value = None
        for _ in range(10):
            value = calc.update(100.0)
        assert value == pytest.approx(0.0)

    def test_ignores_a_non_positive_close_rather_than_taking_log_of_zero(self):
        calc = RealizedVolCalculator(window=3)
        for close in [100.0, 101.0, 0.0, 102.0, 103.0]:
            calc.update(close)  # must not raise
        assert calc.value is None or calc.value >= 0.0


class TestWilderSmoother:
    def test_seeds_with_the_first_value_then_recurses(self):
        s = WilderSmoother(period=2)
        assert s.update(10.0) is None  # masked: only 1 of 2 values seen
        # value = 0.5*20 + 0.5*10 = 15
        assert s.update(20.0) == pytest.approx(15.0)
