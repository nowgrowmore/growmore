"""Local full-OHLCV store for the NSE F&O universe.

    research/.cache/fno_bars/{SYMBOL}_1d.parquet
    columns: timestamp, open, high, low, close, volume

This is the equity sibling of `research/dailydata/cache.py` and reuses its
frame helpers and `CachedBar` outright -- the schema is identical and
`CachedBar` already duck-types `growmore_bot.broker.dhan_client.Bar` for the
backtest engine. Only the path layout, the IST trading-date conversion and
the window clipping live here. Mirroring rather than generalising is the
convention both existing caches state explicitly (`dailydata/cache.py:10-13`,
`intraday/bar_cache.py:23-27`).

**Why not reuse the small-cap equity cache instead.** That one
(`research/smallcap_momentum/price_data.py`) stores `date, close, volume`
and throws OHLC away. Every config this store exists to serve is an ATR
system, and ATR needs high and low, so its 400 cached symbols -- 113 of them
F&O names -- cannot serve this work at all.

**The trading date is the IST calendar date, and that is a bug fix.** Dhan
returns daily bars stamped `18:30:00+00:00`, i.e. midnight IST of the NEXT
day. `price_data._save_bars` takes `.date()` off the raw UTC timestamp, so
every date it stores is one day early. Day-of-week counts over five years
confirm it on both existing caches: 249 "Sundays" and one "Friday". Harmless
for a single-symbol backtest, where only bar ORDER matters, and wrong the
moment a date is cross-referenced against another series or printed in a
published table. The parquet keeps the raw tz-aware timestamp -- lossless --
and `trading_date` does the conversion at the point of use.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from research.dailydata.cache import (
    COLUMNS,
    CachedBar,
    bars_to_frame,
    frame_to_bars,
)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "fno_bars"

#: India has no DST, so a fixed offset is exact -- no tz database needed,
#: and this stays stdlib-only like the rest of the research layer.
_IST = timezone(timedelta(hours=5, minutes=30))


def trading_date(timestamp: datetime) -> date:
    """The IST calendar date a bar belongs to.

    A naive timestamp is assumed to already be UTC, matching what
    `DhanClient` produces.
    """
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(_IST).date()


def _path(symbol: str, cache_dir: Optional[Path] = None) -> Path:
    return (cache_dir or CACHE_DIR) / f"{symbol}_1d.parquet"


def save(symbol: str, frame: pd.DataFrame, cache_dir: Optional[Path] = None) -> Path:
    path = _path(symbol, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def is_cached(symbol: str, cache_dir: Optional[Path] = None) -> bool:
    return _path(symbol, cache_dir).exists()


def load(
    symbol: str,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    cache_dir: Optional[Path] = None,
) -> list[CachedBar]:
    """Load cached daily bars, optionally clipped to an **IST** date window."""
    path = _path(symbol, cache_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No F&O daily cache for {symbol} at {path}. "
            f"Run `python -m research.fno.fetch_bars` first."
        )
    frame = pd.read_parquet(path)
    if from_date is not None or to_date is not None:
        dates = pd.to_datetime(frame["timestamp"], utc=True).map(trading_date)
        if from_date is not None:
            frame = frame[dates >= from_date]
            dates = dates[dates >= from_date]
        if to_date is not None:
            frame = frame[dates <= to_date]
    return frame_to_bars(frame.reset_index(drop=True))


def cached_symbols(cache_dir: Optional[Path] = None) -> list[str]:
    directory = cache_dir or CACHE_DIR
    if not directory.exists():
        return []
    return sorted(p.stem.removesuffix("_1d") for p in directory.glob("*_1d.parquet"))


__all__ = [
    "CACHE_DIR",
    "COLUMNS",
    "CachedBar",
    "bars_to_frame",
    "frame_to_bars",
    "trading_date",
    "save",
    "is_cached",
    "load",
    "cached_symbols",
]
