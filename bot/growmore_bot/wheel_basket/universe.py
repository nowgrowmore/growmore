"""Candidate universe loader for the wheel-basket strategy.

Reads the same committed CSV research/fno/manifest.py produces
(research/fno/universe.csv) directly via plain csv, rather than importing
that module -- every shared-code precedent in this repo runs
research -> growmore_bot, never the reverse, and universe.csv is checked-in
data, not backtest logic.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_UNIVERSE_CSV = (
    Path(__file__).resolve().parents[2] / "research" / "fno" / "universe.csv"
)


@dataclass(frozen=True)
class UniverseRow:
    symbol: str
    security_id: str
    lot_size: int
    #: NSE's own macro sector, one of 18. Mutually exclusive, and the bucket
    #: the basket's sector diversification counts against.
    nse_industry: str = ""
    #: An OVERLAY, not a bucket -- a defence name keeps its NSE sector (BEL is
    #: Capital Goods, BHARATFORG is Automobile). Reported as an exposure,
    #: never used as a 19th sector. See research/fno/sectors.py.
    is_defence: bool = False


def load_universe(path: Path = _DEFAULT_UNIVERSE_CSV) -> list[UniverseRow]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            UniverseRow(
                symbol=row["symbol"],
                security_id=row["security_id"],
                lot_size=int(row["fno_lot_size"]),
                nse_industry=row.get("nse_industry", ""),
                is_defence=row.get("is_defence", "").strip().lower() == "true",
            )
            for row in reader
        ]


__all__ = ["UniverseRow", "load_universe"]
