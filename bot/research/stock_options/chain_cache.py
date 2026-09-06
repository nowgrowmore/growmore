"""Local store for NSE stock-option chains.

Two layouts, because the fetch and the backtest want opposite things:

    research/.cache/stock_options/days/{YYYY-MM-DD}.parquet   <- fetch writes
    research/.cache/stock_options/symbols/{SYMBOL}.parquet    <- engine reads

The fetch is inherently per-day (one bhavcopy per trading day) and needs to
be resumable at day granularity, so a crash 900 days in costs nothing. The
backtest is inherently per-stock -- it walks one underlying's whole option
history -- so reading 1,750 day files per stock would be absurd. Consolidating
once, after the fetch, is far cheaper than doing either badly.

Mirrors the layout convention of `research/fno/bar_cache.py` rather than
generalising it, which is what every other cache module in this repo does and
says.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Sequence

import pandas as pd

from research.stock_options.bhavcopy import OptionRow

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "stock_options"
DAY_DIR = CACHE_DIR / "days"
SYMBOL_DIR = CACHE_DIR / "symbols"

COLUMNS = [
    "trade_date", "symbol", "expiry", "strike", "opt_type",
    "open", "high", "low", "close", "settle",
    "open_interest", "volume", "lot_size", "underlying",
]


def rows_to_frame(rows: Sequence[OptionRow]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "trade_date": r.trade_date, "symbol": r.symbol, "expiry": r.expiry,
                "strike": r.strike, "opt_type": r.opt_type,
                "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "settle": r.settle, "open_interest": r.open_interest,
                "volume": r.volume,
                "lot_size": r.lot_size if r.lot_size is not None else 0,
                "underlying": r.underlying if r.underlying is not None else 0.0,
            }
            for r in rows
        ],
        columns=COLUMNS,
    )
    if not frame.empty:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        frame["expiry"] = pd.to_datetime(frame["expiry"])
    return frame


def _day_path(day: date) -> Path:
    return DAY_DIR / f"{day.isoformat()}.parquet"


def is_day_cached(day: date) -> bool:
    return _day_path(day).exists()


def save_day(day: date, frame: pd.DataFrame) -> Path:
    path = _day_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def cached_days() -> list[date]:
    if not DAY_DIR.exists():
        return []
    return sorted(date.fromisoformat(p.stem) for p in DAY_DIR.glob("*.parquet"))


def consolidate_by_symbol(symbols: Optional[Iterable[str]] = None) -> dict[str, int]:
    """Rewrite the per-day files as one parquet per underlying.

    Done in a single streaming pass over the day files, accumulating per
    symbol, because loading 46M rows at once is unnecessary and the whole
    point of the per-symbol layout is that the engine never has to.
    """
    days = cached_days()
    if not days:
        return {}
    wanted = set(symbols) if symbols is not None else None
    SYMBOL_DIR.mkdir(parents=True, exist_ok=True)

    buckets: dict[str, list[pd.DataFrame]] = {}
    for day in days:
        frame = pd.read_parquet(_day_path(day))
        if frame.empty:
            continue
        for symbol, group in frame.groupby("symbol", sort=False):
            if wanted is not None and symbol not in wanted:
                continue
            buckets.setdefault(str(symbol), []).append(group)

    counts: dict[str, int] = {}
    for symbol, parts in buckets.items():
        merged = pd.concat(parts, ignore_index=True)
        merged = merged.sort_values(["trade_date", "expiry", "strike", "opt_type"])
        merged.reset_index(drop=True).to_parquet(SYMBOL_DIR / f"{symbol}.parquet", index=False)
        counts[symbol] = len(merged)
    return counts


def load_symbol(symbol: str) -> pd.DataFrame:
    path = SYMBOL_DIR / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"No option chain for {symbol} at {path}. Run "
            f"`python -m research.stock_options.fetch` then "
            f"`python -m research.stock_options.fetch --consolidate`."
        )
    return pd.read_parquet(path)


def cached_symbols() -> list[str]:
    if not SYMBOL_DIR.exists():
        return []
    return sorted(p.stem for p in SYMBOL_DIR.glob("*.parquet"))


__all__ = [
    "CACHE_DIR", "DAY_DIR", "SYMBOL_DIR", "COLUMNS", "rows_to_frame",
    "is_day_cached", "save_day", "cached_days", "consolidate_by_symbol",
    "load_symbol", "cached_symbols",
]
