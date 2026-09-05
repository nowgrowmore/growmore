"""Fetch and locally cache MCX intraday bars from Dhan.

Dhan v2 serves 1/5/15/25/60-minute candles for MCX going back 5 years, 90
days per request -- and `DhanClient.get_historical_ohlc` has always accepted
an int `interval` and routed it to `intraday_minute_data`. Nothing in the bot
had ever called it that way, so a whole timeframe was sitting there unused.

Verified live 2026-09-05 before building anything on top:
  * timestamps convert to 09:00-23:25 IST, so Dhan returns real UTC epochs
    and there is no hidden 5:30 offset (which would have silently shifted
    every time-of-day rule by half the session);
  * 174 bars per session at 5 minutes, matching a 09:00-23:30 day exactly;
  * NO gap at 17:00, confirming MCX runs one continuous session rather than
    the morning/evening split the market-structure literature describes --
    those are liquidity regimes, not exchange sessions. So session VWAP
    resets once a day, and there is one CPR per day.

5 minutes is the canonical store: 15/25/60 are derivable by resampling at no
API cost, and 5 is fine resolution for a session VWAP or any time-of-day
rule. Roughly 21 windows x 8 instruments = 168 requests, a few minutes at
Dhan's ~1 req/sec practical rate.

This deliberately does NOT reuse research/smallcap_momentum/price_data.py.
That module persists only date/close/volume (dropping OHLC entirely), has no
windowing, and hardcodes NSE_EQ -- adapting it would be a rewrite of its save
path, and CLAUDE.md's scope discipline says not to refactor working code for
an unrelated task. The genuine overlap is a short retry loop.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
REQUEST_DELAY_SECONDS = 1.2
MAX_RETRIES = 3
#: Dhan's hard limit per intraday request.
MAX_WINDOW_DAYS = 90


@dataclass(frozen=True)
class FetchResult:
    fetched: list[str]
    cached: list[str]
    failed: list[str]


def _path(symbol: str, interval: int, year: int) -> Path:
    return CACHE_DIR / f"{symbol}_{interval}m" / f"{year}.parquet"


def year_windows(from_date: date, to_date: date, year: int) -> list[tuple[date, date]]:
    """Calendar-aligned windows within one year, each at most 90 days.

    Aligned to the calendar rather than rolled forward from `from_date` so a
    re-run slices identically and the cache stays idempotent.
    """
    start = max(from_date, date(year, 1, 1))
    end = min(to_date, date(year, 12, 31))
    windows = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=MAX_WINDOW_DAYS - 1), end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows


def load_cached(symbol: str, interval: int, year: int) -> Optional[pd.DataFrame]:
    path = _path(symbol, interval, year)
    return pd.read_parquet(path) if path.exists() else None


def _fetch_window(dhan_client, instrument, start: date, stop: date, interval: int) -> list:
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return dhan_client.get_historical_ohlc(
                instrument,
                from_date=start.isoformat(),
                to_date=stop.isoformat(),
                interval=interval,
            )
        except Exception as exc:  # noqa: BLE001 - logged and retried, never aborts the batch
            last_error = exc
            logger.warning(
                "intraday fetch failed for %s %s..%s (attempt %d/%d): %s",
                getattr(instrument, "symbol", instrument), start, stop, attempt, MAX_RETRIES, exc,
            )
            time.sleep(REQUEST_DELAY_SECONDS * attempt)
    raise RuntimeError(f"gave up fetching {start}..{stop}") from last_error


def fetch_symbol_year(
    dhan_client, instrument, year: int, from_date: date, to_date: date, interval: int = 5
) -> Optional[pd.DataFrame]:
    """One year of bars for one instrument, cached to parquet. Resumable:
    an existing year file is returned untouched."""
    cached = load_cached(instrument.symbol, interval, year)
    if cached is not None:
        return cached

    rows = []
    for start, stop in year_windows(from_date, to_date, year):
        for bar in _fetch_window(dhan_client, instrument, start, stop, interval):
            rows.append(
                {
                    "ts": bar.timestamp,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
            )
        time.sleep(REQUEST_DELAY_SECONDS)

    if not rows:
        return None
    frame = pd.DataFrame(rows).drop_duplicates(subset="ts").sort_values("ts")
    path = _path(instrument.symbol, interval, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return frame


def load_range(symbol: str, interval: int, from_date: date, to_date: date) -> pd.DataFrame:
    frames = [
        f for f in (
            load_cached(symbol, interval, y) for y in range(from_date.year, to_date.year + 1)
        ) if f is not None
    ]
    if not frames:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    all_bars = pd.concat(frames).drop_duplicates(subset="ts").sort_values("ts")
    mask = (all_bars["ts"].dt.date >= from_date) & (all_bars["ts"].dt.date <= to_date)
    return all_bars.loc[mask].reset_index(drop=True)


__all__ = [
    "CACHE_DIR", "FetchResult", "year_windows", "load_cached", "fetch_symbol_year", "load_range",
]
