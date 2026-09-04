"""Tests for growmore_bot.scheduler.market_hours.is_market_open.

MCX hours: 09:00-23:30/23:55 IST (seasonal, see below), weekdays only, minus
a hardcoded 2026 full-closure holiday list. Partial-session holidays (e.g.
Holi, Ganesh Chaturthi -- morning closed, evening open) are NOT handled yet,
same as MCX special/shortened sessions -- see docs/technical-debt.md item #4.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytz

from growmore_bot.scheduler.market_hours import (
    _nth_sunday_of_month,
    is_market_open,
    is_near_session_close,
)

IST = pytz.timezone("Asia/Kolkata")


def _ist(y, m, d, h, minute):
    return IST.localize(datetime(y, m, d, h, minute))


def _next_weekday_on_or_after(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def test_weekday_inside_hours_is_open():
    # Wednesday, 2026-09-02 14:30 IST
    assert is_market_open(_ist(2026, 9, 2, 14, 30)) is True


def test_weekday_just_after_open_is_open():
    assert is_market_open(_ist(2026, 9, 2, 9, 0)) is True


def test_weekday_just_before_close_is_open():
    assert is_market_open(_ist(2026, 9, 2, 23, 29)) is True


def test_weekday_before_market_open_is_closed():
    assert is_market_open(_ist(2026, 9, 2, 8, 59)) is False


def test_weekday_after_market_close_is_closed():
    assert is_market_open(_ist(2026, 9, 2, 23, 31)) is False


def test_weekday_late_night_is_closed():
    assert is_market_open(_ist(2026, 9, 2, 2, 0)) is False


def test_saturday_is_closed():
    # 2026-09-05 is a Saturday
    assert is_market_open(_ist(2026, 9, 5, 14, 0)) is False


def test_sunday_is_closed():
    # 2026-09-06 is a Sunday
    assert is_market_open(_ist(2026, 9, 6, 14, 0)) is False


def test_naive_datetime_is_assumed_ist():
    naive = datetime(2026, 9, 2, 14, 30)
    assert is_market_open(naive) is True


def test_utc_datetime_is_converted_to_ist():
    utc = pytz.utc.localize(datetime(2026, 9, 2, 4, 0))  # 09:30 IST
    assert is_market_open(utc) is True


# -- 2026 full-closure holidays (sourced from Groww's MCX 2026 holiday list,
# checked 2026-09-03 -- see market_hours.py docstring). Only unambiguous
# full-day closures; partial-session holidays are a known remaining gap.
def test_new_years_day_2026_is_closed():
    assert is_market_open(_ist(2026, 1, 1, 14, 0)) is False


def test_republic_day_2026_is_closed():
    assert is_market_open(_ist(2026, 1, 26, 14, 0)) is False


def test_good_friday_2026_is_closed():
    assert is_market_open(_ist(2026, 4, 3, 14, 0)) is False


def test_gandhi_jayanti_2026_is_closed():
    assert is_market_open(_ist(2026, 10, 2, 14, 0)) is False


def test_christmas_2026_is_closed():
    assert is_market_open(_ist(2026, 12, 25, 14, 0)) is False


def test_day_after_a_holiday_is_open_as_normal():
    # 2026-01-02 is a Friday, not a holiday.
    assert is_market_open(_ist(2026, 1, 2, 14, 0)) is True


# -- Seasonal close-time shift: MCX closes non-agri commodities at 23:30 IST
# while the US observes daylight saving time (2nd Sunday of March through
# the day before the 1st Sunday of November), and at 23:55 IST the rest of
# the year, to keep the same overlap window with US markets. Verified against
# ICICI Direct's coverage of the 2026-03-09 change, checked 2026-09-03.
def test_summer_close_time_is_2330():
    summer_day = _next_weekday_on_or_after(date(2024, 6, 17))
    assert is_market_open(_ist(summer_day.year, summer_day.month, summer_day.day, 23, 29)) is True
    assert is_market_open(_ist(summer_day.year, summer_day.month, summer_day.day, 23, 31)) is False


def test_winter_close_time_is_2355():
    winter_day = _next_weekday_on_or_after(date(2024, 1, 15))
    assert is_market_open(_ist(winter_day.year, winter_day.month, winter_day.day, 23, 50)) is True
    assert is_market_open(_ist(winter_day.year, winter_day.month, winter_day.day, 23, 56)) is False


def test_open_time_unaffected_by_season():
    winter_day = _next_weekday_on_or_after(date(2024, 1, 15))
    assert is_market_open(_ist(winter_day.year, winter_day.month, winter_day.day, 8, 59)) is False
    assert is_market_open(_ist(winter_day.year, winter_day.month, winter_day.day, 9, 0)) is True


def test_nth_sunday_of_month_second_sunday_of_march_2024():
    assert _nth_sunday_of_month(2024, 3, 2) == date(2024, 3, 10)


def test_nth_sunday_of_month_first_sunday_of_november_2024():
    assert _nth_sunday_of_month(2024, 11, 1) == date(2024, 11, 3)


class TestIsNearSessionClose:
    def test_true_within_default_buffer_of_summer_close(self):
        # 2026-09-02 is a summer-DST date -- close is 23:30.
        assert is_near_session_close(_ist(2026, 9, 2, 23, 20)) is True

    def test_false_well_before_summer_close(self):
        assert is_near_session_close(_ist(2026, 9, 2, 23, 0)) is False

    def test_true_within_default_buffer_of_winter_close(self):
        winter_day = _next_weekday_on_or_after(date(2026, 12, 15))
        assert is_near_session_close(_ist(winter_day.year, winter_day.month, winter_day.day, 23, 45)) is True

    def test_false_well_before_winter_close(self):
        winter_day = _next_weekday_on_or_after(date(2026, 12, 15))
        assert is_near_session_close(_ist(winter_day.year, winter_day.month, winter_day.day, 23, 0)) is False

    def test_custom_buffer_minutes(self):
        assert is_near_session_close(_ist(2026, 9, 2, 23, 10), buffer_minutes=30) is True
        assert is_near_session_close(_ist(2026, 9, 2, 22, 59), buffer_minutes=30) is False

    def test_true_exactly_at_close(self):
        assert is_near_session_close(_ist(2026, 9, 2, 23, 30)) is True
