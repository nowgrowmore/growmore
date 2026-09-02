"""Tests for growmore_bot.scheduler.market_hours.is_market_open.

MCX hours: 09:00-23:30 IST, weekdays only. No holiday calendar yet (tracked
as a TODO in market_hours.py) -- only weekends + the hour window are checked.
"""
from __future__ import annotations

from datetime import datetime

import pytz

from growmore_bot.scheduler.market_hours import is_market_open

IST = pytz.timezone("Asia/Kolkata")


def _ist(y, m, d, h, minute):
    return IST.localize(datetime(y, m, d, h, minute))


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
