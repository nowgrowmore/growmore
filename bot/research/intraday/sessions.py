"""Session segmentation, VWAP reconstruction and CPR, from intraday bars.

MCX runs ONE continuous session per day (09:00-23:30 IST, 23:55 outside US
DST), verified empirically -- there is no bar gap at 17:00. So session VWAP
resets once a day and there is exactly one CPR per day, derived from the
previous session.

Session VWAP here is the running sum(typical price x volume) / sum(volume)
from the session's first bar, which is the same quantity Dhan reports live as
`average_price` and which `VwapSessionBounceStrategy` trades against. It is a
RECONSTRUCTION from 5-minute bars rather than the exchange's own trade-by-
trade figure -- since the strategy triggers on a CROSSING, a small error near
the crossing point can flip a signal, so any result has to be read with the
reconstruction error in mind.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import pandas as pd
import pytz

IST = pytz.timezone("Asia/Kolkata")


def to_ist(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["ist"] = out["ts"].dt.tz_convert(IST)
    out["session"] = out["ist"].dt.date
    return out


def sessions(frame: pd.DataFrame) -> Iterator[tuple[date, pd.DataFrame]]:
    """Yield (session date, that session's bars in order)."""
    localised = to_ist(frame)
    for session_date, bars in localised.groupby("session", sort=True):
        yield session_date, bars.sort_values("ist").reset_index(drop=True)


def running_session_vwap(bars: pd.DataFrame) -> pd.Series:
    """Running VWAP from the session open, one value per bar."""
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    pv = (typical * bars["volume"]).cumsum()
    vol = bars["volume"].cumsum()
    return (pv / vol).where(vol > 0)


def daily_bar(bars: pd.DataFrame) -> dict:
    """Aggregate one session's intraday bars into a daily OHLC, for feeding
    a strategy's warm-up branch."""
    return {
        "open": float(bars["open"].iloc[0]),
        "high": float(bars["high"].max()),
        "low": float(bars["low"].min()),
        "close": float(bars["close"].iloc[-1]),
        "volume": float(bars["volume"].sum()),
    }


def flag_roll_gaps(daily: pd.DataFrame, threshold: float = 3.0) -> pd.Series:
    """True where a session's open gaps from the prior close by more than
    `threshold` times the trailing median absolute gap.

    Contract rolls rewrite `instruments.security_id` in place, so a "5-year"
    series is really several contracts spliced together with a price
    discontinuity at each join. On daily bars that is a rounding error in a
    90-trade sample; at intraday decision thresholds each one is a fake
    breakout, ~12 a year.
    """
    gap = (daily["open"] - daily["close"].shift(1)).abs()
    typical = gap.rolling(20, min_periods=5).median()
    return gap > threshold * typical


__all__ = [
    "IST", "to_ist", "sessions", "running_session_vwap", "daily_bar", "flag_roll_gaps",
]
