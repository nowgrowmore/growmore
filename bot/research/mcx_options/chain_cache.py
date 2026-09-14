"""Local store for MCX option chains.

Adapts `research/stock_options/chain_cache.py`'s day-parquet -> per-symbol
parquet consolidation design directly -- same streaming/flush logic, same two
layouts for the same reason:

    research/.cache/mcx_options/days/{YYYY-MM-DD}.parquet   <- fetch writes
    research/.cache/mcx_options/symbols/{SYMBOL}.parquet    <- engine reads

The fetch is inherently per-day (one bhavcopy per trading day) and needs to
be resumable at day granularity, so a crash partway through a backfill costs
nothing. The backtest is inherently per-underlying -- it walks one
commodity's whole option history -- so reading every day file per commodity
would be wasteful. Consolidating once, after the fetch, is cheaper than doing
either badly.

Only the row type differs from the stock-options version: this works over
`research.mcx_options.bhavcopy.MCXOptionRow` instead of NSE's `OptionRow`,
which has no `lot_size`/`underlying` fields (MCX's bhavcopy carries no
underlying-futures price and lot size lives in `contract_specs.py`, not per
row -- see those modules' docstrings for why).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Sequence

import pandas as pd

from research.mcx_options.bhavcopy import MCXOptionRow

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "mcx_options"
DAY_DIR = CACHE_DIR / "days"
SYMBOL_DIR = CACHE_DIR / "symbols"

COLUMNS = [
    "trade_date", "symbol", "expiry", "strike", "opt_type",
    "open", "high", "low", "close", "previous_close",
    "open_interest", "volume", "value",
]


def rows_to_frame(rows: Sequence[MCXOptionRow]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "trade_date": r.trade_date, "symbol": r.symbol, "expiry": r.expiry,
                "strike": r.strike, "opt_type": r.opt_type,
                "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "previous_close": r.previous_close,
                "open_interest": r.open_interest, "volume": r.volume,
                "value": r.value,
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


def consolidate_by_symbol(
    symbols: Optional[Iterable[str]] = None, flush_every: int = 120
) -> dict[str, int]:
    """Rewrite the per-day files as one parquet per underlying.

    Streams for the same reason `stock_options.chain_cache` does: day files
    are read in order and per-symbol buckets flushed to numbered part files
    every `flush_every` days, then concatenated once per symbol so the whole
    store is never held in memory at once.
    """
    days = cached_days()
    if not days:
        return {}
    wanted = set(symbols) if symbols is not None else None
    SYMBOL_DIR.mkdir(parents=True, exist_ok=True)
    parts_dir = SYMBOL_DIR / "_parts"
    if parts_dir.exists():
        for stale in parts_dir.glob("*.parquet"):
            stale.unlink()
    parts_dir.mkdir(parents=True, exist_ok=True)

    buckets: dict[str, list[pd.DataFrame]] = {}
    part_index = 0

    def flush(index: int) -> None:
        for symbol, frames in buckets.items():
            if frames:
                pd.concat(frames, ignore_index=True).to_parquet(
                    parts_dir / f"{symbol}__{index:04d}.parquet", index=False
                )
        buckets.clear()

    for position, day in enumerate(days, start=1):
        frame = pd.read_parquet(_day_path(day))
        if not frame.empty:
            for symbol, group in frame.groupby("symbol", sort=False):
                if wanted is not None and symbol not in wanted:
                    continue
                buckets.setdefault(str(symbol), []).append(group)
        if position % flush_every == 0:
            flush(part_index)
            part_index += 1
    flush(part_index)

    counts: dict[str, int] = {}
    all_symbols = sorted({p.stem.split("__")[0] for p in parts_dir.glob("*.parquet")})
    for symbol in all_symbols:
        parts = sorted(parts_dir.glob(f"{symbol}__*.parquet"))
        merged = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        merged = merged.sort_values(["trade_date", "expiry", "strike", "opt_type"])
        merged.reset_index(drop=True).to_parquet(SYMBOL_DIR / f"{symbol}.parquet", index=False)
        counts[symbol] = len(merged)
        for p in parts:
            p.unlink()
    parts_dir.rmdir()
    return counts


def load_symbol(symbol: str) -> pd.DataFrame:
    path = SYMBOL_DIR / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"No option chain for {symbol} at {path}. Run "
            f"`python -m research.mcx_options.fetch` then "
            f"`python -m research.mcx_options.fetch --consolidate`."
        )
    return pd.read_parquet(path)


def cached_symbols() -> list[str]:
    if not SYMBOL_DIR.exists():
        return []
    return sorted(p.stem for p in SYMBOL_DIR.glob("*.parquet") if "__" not in p.stem)


__all__ = [
    "CACHE_DIR", "DAY_DIR", "SYMBOL_DIR", "COLUMNS", "rows_to_frame",
    "is_day_cached", "save_day", "cached_days", "consolidate_by_symbol",
    "load_symbol", "cached_symbols",
]
