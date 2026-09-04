"""MCX market-hours check.

Pure function, no I/O: `is_market_open(now)` -> bool for MCX commodity
segment hours, Monday-Friday, minus a hardcoded holiday list.

Two real rules verified 2026-09-03 (see docs/technical-debt.md for sources
and the remaining gap):

1. **Seasonal close-time shift.** MCX closes non-agri commodities at 23:30
   IST while the US observes daylight saving time (2nd Sunday of March
   through the day before the 1st Sunday of November), and at 23:55 IST the
   rest of the year -- purely to keep the same overlap window with US
   markets, since India doesn't observe DST itself. Computed per-year from
   the DST rule directly (not hardcoded per year) via `_nth_sunday_of_month`.
2. **2026 full-closure holidays** (`MCX_HOLIDAYS_2026`): New Year's Day,
   Republic Day, Good Friday, Gandhi Jayanti, Christmas -- sourced from
   Groww's MCX 2026 holiday list, checked 2026-09-03. This needs a fresh
   lookup every year (same maintenance pattern as
   `config.DEFAULT_COMMODITY_UNIVERSE`'s contract expiries). Deliberately
   does NOT include partial-session holidays (e.g. Holi, Ganesh Chaturthi --
   morning closed, evening open per some sources) since their exact session
   boundaries aren't clearly documented and getting them wrong risks
   blocking real trading hours -- worse than the status quo of polling
   needlessly on those days, which is harmless (see docs/technical-debt.md
   item #4).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytz

MCX_TIMEZONE = pytz.timezone("Asia/Kolkata")
MCX_OPEN_TIME = time(9, 0)
MCX_CLOSE_TIME_SUMMER = time(23, 30)  # while the US observes DST
MCX_CLOSE_TIME_WINTER = time(23, 55)  # while the US does not

# Backward-compatible alias -- the pre-seasonal-aware default.
MCX_CLOSE_TIME = MCX_CLOSE_TIME_SUMMER

MCX_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 26),  # Republic Day
        date(2026, 4, 3),  # Good Friday
        date(2026, 10, 2),  # Mahatma Gandhi Jayanti
        date(2026, 12, 25),  # Christmas
    }
)


def _nth_sunday_of_month(year: int, month: int, n: int) -> date:
    """The date of the nth Sunday of `month`/`year` (n=1 -> first Sunday)."""
    first_of_month = date(year, month, 1)
    days_until_sunday = (6 - first_of_month.weekday()) % 7
    first_sunday = first_of_month + timedelta(days=days_until_sunday)
    return first_sunday + timedelta(weeks=n - 1)


def _mcx_close_time(d: date) -> time:
    """MCX's non-agri close time for the given IST calendar date."""
    us_dst_start = _nth_sunday_of_month(d.year, 3, 2)
    us_dst_end = _nth_sunday_of_month(d.year, 11, 1)
    if us_dst_start <= d < us_dst_end:
        return MCX_CLOSE_TIME_SUMMER
    return MCX_CLOSE_TIME_WINTER


def is_market_open(now: datetime) -> bool:
    """True if `now` falls within MCX trading hours on a trading day.

    Naive datetimes are assumed to already be in IST. Timezone-aware
    datetimes are converted to IST first.
    """
    if now.tzinfo is not None:
        now_ist = now.astimezone(MCX_TIMEZONE)
    else:
        now_ist = MCX_TIMEZONE.localize(now)

    if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    today = now_ist.date()
    if today in MCX_HOLIDAYS_2026:
        return False

    current_time = now_ist.time()
    return MCX_OPEN_TIME <= current_time <= _mcx_close_time(today)


def is_near_session_close(now: datetime, buffer_minutes: int = 15) -> bool:
    """True once `now` is within `buffer_minutes` of today's actual MCX
    close (season-aware, via `_mcx_close_time`). Used to force-flatten a
    position for a strategy whose logic is inherently single-day (see
    `Strategy.requires_intraday_flatten`) -- deliberately doesn't care
    whether the market is otherwise open (e.g. a weekend/holiday): the
    scheduler only calls this while already ticking a live/paper config
    during real market hours anyway.
    """
    if now.tzinfo is not None:
        now_ist = now.astimezone(MCX_TIMEZONE)
    else:
        now_ist = MCX_TIMEZONE.localize(now)

    close_time = _mcx_close_time(now_ist.date())
    close_dt = now_ist.replace(
        hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0
    )
    return close_dt - timedelta(minutes=buffer_minutes) <= now_ist <= close_dt


__all__ = [
    "is_market_open",
    "is_near_session_close",
    "MCX_TIMEZONE",
    "MCX_OPEN_TIME",
    "MCX_CLOSE_TIME",
    "MCX_CLOSE_TIME_SUMMER",
    "MCX_CLOSE_TIME_WINTER",
    "MCX_HOLIDAYS_2026",
]
