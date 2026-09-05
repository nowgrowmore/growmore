"""Tests for growmore_bot.risk.exits -- pure stop arithmetic.

These are deliberately dumb functions over floats. All the subtlety in the
risk layer lives in WHEN they're called and against WHICH bar (see
growmore_bot.risk.wrapper and the intrabar ordering in
growmore_bot.backtest.engine); keeping the arithmetic itself trivially
checkable is the point.
"""
from __future__ import annotations

import pytest

from growmore_bot.risk.exits import chandelier_stop, initial_atr_stop, time_stop_hit


class TestInitialAtrStop:
    def test_long_stop_sits_below_entry(self):
        assert initial_atr_stop(entry_price=100.0, atr=5.0, k=2.0, direction=1) == pytest.approx(90.0)

    def test_short_stop_sits_above_entry(self):
        assert initial_atr_stop(entry_price=100.0, atr=5.0, k=2.0, direction=-1) == pytest.approx(110.0)

    def test_no_atr_yet_means_no_stop_rather_than_a_stop_at_the_entry_price(self):
        """During warm-up ATR is None. Returning entry_price would place a
        stop exactly at the fill and exit instantly on any adverse tick."""
        assert initial_atr_stop(entry_price=100.0, atr=None, k=2.0, direction=1) is None

    def test_rejects_a_non_positive_multiple(self):
        with pytest.raises(ValueError):
            initial_atr_stop(entry_price=100.0, atr=5.0, k=0.0, direction=1)


class TestChandelierStop:
    def test_trails_below_the_high_water_mark_for_a_long(self):
        assert chandelier_stop(high_water=120.0, atr=4.0, k=3.0, direction=1) == pytest.approx(108.0)

    def test_trails_above_the_low_water_mark_for_a_short(self):
        assert chandelier_stop(high_water=80.0, atr=4.0, k=3.0, direction=-1) == pytest.approx(92.0)

    def test_returns_none_without_an_atr(self):
        assert chandelier_stop(high_water=120.0, atr=None, k=3.0, direction=1) is None


class TestTimeStopHit:
    def test_fires_once_the_holding_period_is_reached(self):
        assert time_stop_hit(bars_held=10, max_bars=10) is True
        assert time_stop_hit(bars_held=11, max_bars=10) is True

    def test_does_not_fire_before(self):
        assert time_stop_hit(bars_held=9, max_bars=10) is False

    def test_no_limit_configured_means_never(self):
        assert time_stop_hit(bars_held=10_000, max_bars=None) is False


class TestNonsensicalStopsAreRefused:
    """Defence in depth behind the feed validation in dhan_client. A run of
    zero-priced NICKEL bars once produced an ATR large enough to place a stop
    at -0.4, which then "filled" and booked a Rs 485,199 loss on one trade --
    a 199% max drawdown on a long-only 1x position."""

    def test_a_stop_below_zero_is_no_stop_at_all(self):
        assert initial_atr_stop(entry_price=1940.0, atr=970.0, k=2.0, direction=1) is None
        assert chandelier_stop(high_water=100.0, atr=50.0, k=3.0, direction=1) is None

    def test_a_merely_wide_but_positive_stop_is_still_allowed(self):
        # 2 x 400 = 800 below 1940 is wide, but 1140 is a real price.
        assert initial_atr_stop(
            entry_price=1940.0, atr=400.0, k=2.0, direction=1
        ) == pytest.approx(1140.0)

    def test_a_short_stop_above_the_price_is_never_affected(self):
        assert initial_atr_stop(
            entry_price=100.0, atr=970.0, k=2.0, direction=-1
        ) == pytest.approx(2040.0)
