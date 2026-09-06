"""Tests for growmore_bot.scheduler.nse_equity_hours.is_nse_trading_day."""
from __future__ import annotations

from datetime import datetime

from growmore_bot.scheduler.nse_equity_hours import NSE_TIMEZONE, is_nse_trading_day


def test_a_weekday_that_is_not_a_holiday_is_a_trading_day():
    monday = NSE_TIMEZONE.localize(datetime(2026, 1, 5, 10, 0))  # a Monday
    assert is_nse_trading_day(monday) is True


def test_a_weekend_is_never_a_trading_day():
    saturday = NSE_TIMEZONE.localize(datetime(2026, 1, 3, 10, 0))
    sunday = NSE_TIMEZONE.localize(datetime(2026, 1, 4, 10, 0))
    assert is_nse_trading_day(saturday) is False
    assert is_nse_trading_day(sunday) is False


def test_a_full_closure_holiday_is_not_a_trading_day():
    republic_day = NSE_TIMEZONE.localize(datetime(2026, 1, 26, 10, 0))
    assert is_nse_trading_day(republic_day) is False


def test_a_naive_datetime_is_assumed_already_ist():
    naive_monday = datetime(2026, 1, 5, 10, 0)
    assert is_nse_trading_day(naive_monday) is True


def test_date_is_normalized_from_a_timezone_aware_datetime():
    import pytz

    utc_late_night = pytz.utc.localize(datetime(2026, 1, 4, 19, 0))  # 2026-01-05 00:30 IST, a Monday
    assert is_nse_trading_day(utc_late_night) is True
