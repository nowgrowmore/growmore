"""NSE equity trading-day check for the wheel-basket strategy's once-daily
cycle.

Deliberately just a trading-DAY check (weekday, not a holiday) -- unlike
market_hours.is_market_open, which also gates on intraday open/close time
for the MCX tick loop, the wheel-basket job runs once daily after NSE's
close, not on an intraday polling loop.

Reuses MCX's 2026 holiday list as an approximation -- NSE equity and MCX
commodity holiday calendars differ on some dates, a known gap with the same
honesty as market_hours.py's own documented one (no real holiday calendar),
not fixed here (see docs/pending-actions.md).
"""
from __future__ import annotations

from datetime import datetime

from growmore_bot.scheduler.market_hours import MCX_HOLIDAYS_2026, MCX_TIMEZONE

#: Both IST -- kept as a separate name so a future real NSE calendar swap
#: doesn't quietly also change MCX's.
NSE_TIMEZONE = MCX_TIMEZONE


def is_nse_trading_day(now: datetime) -> bool:
    """True if `now` (IST if naive) falls on an NSE equity trading day."""
    if now.tzinfo is not None:
        now_ist = now.astimezone(NSE_TIMEZONE)
    else:
        now_ist = NSE_TIMEZONE.localize(now)
    if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return now_ist.date() not in MCX_HOLIDAYS_2026


__all__ = ["is_nse_trading_day", "NSE_TIMEZONE"]
