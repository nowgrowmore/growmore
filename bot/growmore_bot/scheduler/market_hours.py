"""MCX market-hours check.

Pure function, no I/O: `is_market_open(now)` -> bool for MCX commodity
segment hours (09:00-23:30 IST), Monday-Friday.

TODO: this does NOT account for MCX's holiday calendar (Diwali, other
exchange holidays) -- only weekends + the hour window are handled. Add a
holiday calendar (e.g. loaded from MCX's published schedule) before relying
on this for anything beyond "don't trade outside normal hours".
"""
from __future__ import annotations

from datetime import datetime, time

import pytz

MCX_TIMEZONE = pytz.timezone("Asia/Kolkata")
MCX_OPEN_TIME = time(9, 0)
MCX_CLOSE_TIME = time(23, 30)


def is_market_open(now: datetime) -> bool:
    """True if `now` falls within MCX trading hours on a weekday.

    Naive datetimes are assumed to already be in IST. Timezone-aware
    datetimes are converted to IST first.
    """
    if now.tzinfo is not None:
        now_ist = now.astimezone(MCX_TIMEZONE)
    else:
        now_ist = MCX_TIMEZONE.localize(now)

    if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    current_time = now_ist.time()
    return MCX_OPEN_TIME <= current_time <= MCX_CLOSE_TIME


__all__ = ["is_market_open", "MCX_TIMEZONE", "MCX_OPEN_TIME", "MCX_CLOSE_TIME"]
