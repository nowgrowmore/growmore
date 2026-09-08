"""Built-once storage for the reduced per-symbol panels.

Rebuilding a panel means re-reading a ~100 MB parquet and re-solving implied
vol by bisection for every cycle; over 210 symbols that is minutes, and the
study replays the same panels 14 times. So the reduction is cached, exactly
the way `research/fno/bar_cache.py` and `research/stock_options/chain_cache.py`
cache their own inputs -- and, like both of those, this MIRRORS their layout
convention rather than trying to generalise it.

Pickle rather than parquet: a panel is a list of dense NumPy matrices with
different shapes per cycle, which is not a table. `allow_pickle` is safe here
because nothing outside this repo ever writes these files.
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional

from research.wheel_basket.panel import SymbolPanel, build_symbol_panel

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "wheel_basket_panels"


def _path(symbol: str, cache_dir: Optional[Path] = None) -> Path:
    return (cache_dir or CACHE_DIR) / f"{symbol}.pkl"


def is_cached(symbol: str, cache_dir: Optional[Path] = None) -> bool:
    return _path(symbol, cache_dir).exists()


def save(panel: SymbolPanel, cache_dir: Optional[Path] = None) -> Path:
    path = _path(panel.symbol, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(panel, handle, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def load(symbol: str, cache_dir: Optional[Path] = None) -> Optional[SymbolPanel]:
    path = _path(symbol, cache_dir)
    if not path.exists():
        return None
    with path.open("rb") as handle:
        return pickle.load(handle)


def build_and_cache(symbol: str, cache_dir: Optional[Path] = None) -> Optional[SymbolPanel]:
    """Return the cached panel, building and storing it if absent.

    A symbol with too little option history returns None and is cached as a
    MISS marker, so a second pass does not re-read its parquet only to reject
    it again -- "unmeasured", not "weak", and not re-litigated every run.
    """
    marker = _path(symbol, cache_dir).with_suffix(".miss")
    if marker.exists():
        return None
    cached = load(symbol, cache_dir)
    if cached is not None:
        return cached
    panel = build_symbol_panel(symbol)
    if panel is None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return None
    save(panel, cache_dir)
    return panel


__all__ = ["CACHE_DIR", "is_cached", "save", "load", "build_and_cache"]
