"""Fetch and locally cache the 5y MCX DAILY bars every research phase needs.

Four separate pieces of work -- the stop-lookahead re-measurement, the
walk-forward harness, the USDINR decomposition and the gold-filters-silver
study -- all want the same eight daily series, repeatedly. Pulling them from
Dhan each time is slow, rate-limited, non-reproducible (the series changes as
contracts roll) and needs a live token; pulling them from Neon means writing
to a database another agent is using. So they get cached to parquet once.

This is the DAILY sibling of research/intraday/bar_cache.py and deliberately
mirrors its layout rather than generalising it -- the intraday module's whole
reason for existing is 90-day windowing and session semantics, neither of
which applies here.

Crucially the cache stores bars AFTER DhanClient._validated_bars has run, so
the duplicate-timestamp repair (resolve to the higher-volume front-month bar)
and the corrupt-bar drop are already applied. That is the same series the
committed sweep numbers were produced on.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class CachedBar:
    """Duck-types growmore_bot.broker.dhan_client.Bar for the backtest engine.

    The engine only ever reads .timestamp/.open/.high/.low/.close, so a plain
    frozen dataclass is enough and keeps the research layer free of a broker
    import.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def _path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}_1d.parquet"


def bars_to_frame(bars: Sequence[Any]) -> pd.DataFrame:
    """Normalise a sequence of Dhan Bars into the canonical cache frame."""
    rows = [
        {
            "timestamp": b.timestamp,
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(getattr(b, "volume", 0.0) or 0.0),
        }
        for b in bars
    ]
    frame = pd.DataFrame(rows, columns=COLUMNS)
    return frame.sort_values("timestamp").reset_index(drop=True)


def frame_to_bars(frame: pd.DataFrame) -> list[CachedBar]:
    return [
        CachedBar(
            timestamp=row.timestamp.to_pydatetime()
            if hasattr(row.timestamp, "to_pydatetime")
            else row.timestamp,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.itertuples(index=False)
    ]


def save(symbol: str, frame: pd.DataFrame) -> Path:
    path = _path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def is_cached(symbol: str) -> bool:
    return _path(symbol).exists()


def load(
    symbol: str, from_date: Optional[date] = None, to_date: Optional[date] = None
) -> list[CachedBar]:
    """Load cached daily bars, optionally clipped to a date window."""
    path = _path(symbol)
    if not path.exists():
        raise FileNotFoundError(
            f"No daily cache for {symbol} at {path}. "
            f"Run `python -m research.dailydata.fetch` first."
        )
    frame = pd.read_parquet(path)
    ts = pd.to_datetime(frame["timestamp"])
    if from_date is not None:
        frame = frame[ts.dt.date >= from_date]
        ts = pd.to_datetime(frame["timestamp"])
    if to_date is not None:
        frame = frame[ts.dt.date <= to_date]
    return frame_to_bars(frame.reset_index(drop=True))


def cached_symbols() -> list[str]:
    if not CACHE_DIR.exists():
        return []
    return sorted(p.stem.removesuffix("_1d") for p in CACHE_DIR.glob("*_1d.parquet"))
