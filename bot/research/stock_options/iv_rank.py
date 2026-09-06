"""Rank the 210-name F&O universe by average ATM implied vol, per year.

    python -m research.stock_options.iv_rank

Feeds the "G restricted to the IV-rich quintile" experiment
(docs/stock-options-results.md Sec 6): a genuine per-stock IV ranking, not a
proxy through any strategy's own trailing return. IV is sampled once per
monthly cycle (`atm_iv_by_cycle`), the same cadence every strategy already
trades at, not a full daily pass over the option chain.

Output: `iv_by_stock_year.csv` (symbol, year, avg_atm_iv), one row per
(stock, year) with at least one measurable cycle that year -- the input
`leaderboard_sim.simulate_cross` needs as its `selection_metric`.
"""
from __future__ import annotations

import argparse
import csv
import sys
from typing import Optional, Sequence

import pandas as pd

from research.fno.manifest import load_manifest
from research.stock_options import chain_cache
from research.stock_options.run_strategies import (
    OUTPUT_DIR,
    adjustment_factors,
    atm_iv_by_cycle,
    cycle_open_days,
    to_adjusted_space,
)


def iv_by_year(symbol: str) -> dict[int, float]:
    """One stock's average ATM implied vol, grouped by calendar year."""
    chain = chain_cache.load_symbol(symbol)
    if chain.empty:
        return {}
    chain["trade_date"] = pd.to_datetime(chain["trade_date"])
    factors = adjustment_factors(symbol, chain)
    if factors is None:
        return {}
    chain = to_adjusted_space(chain, factors)
    if chain.empty:
        return {}
    chain["expiry"] = pd.to_datetime(chain["expiry"])

    opens = cycle_open_days(chain)
    iv_by_day = atm_iv_by_cycle(chain, opens)
    if not iv_by_day:
        return {}
    by_year: dict[int, list[float]] = {}
    for day, iv in iv_by_day.items():
        by_year.setdefault(day.year, []).append(iv)
    return {year: sum(vs) / len(vs) for year, vs in by_year.items()}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    manifest = {r.symbol for r in load_manifest()}
    symbols = [s for s in chain_cache.cached_symbols() if s in manifest]
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"{len(symbols)} symbols", file=sys.stderr)

    rows: list[dict] = []
    for index, symbol in enumerate(symbols, start=1):
        try:
            by_year = iv_by_year(symbol)
        except Exception as exc:  # noqa: BLE001 -- one stock must not lose the run
            print(f"  {symbol}: FAILED {str(exc)[:70]}", file=sys.stderr)
            continue
        for year, avg_iv in sorted(by_year.items()):
            rows.append({"symbol": symbol, "year": year, "avg_atm_iv": round(avg_iv, 4)})
        if index % 25 == 0:
            print(f"  [{index}/{len(symbols)}]", file=sys.stderr)

    if not rows:
        print("Nothing measured.", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "iv_by_stock_year.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "year", "avg_atm_iv"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{len(rows)} (stock, year) rows over "
          f"{len({r['symbol'] for r in rows})} stocks", file=sys.stderr)
    print(f"-> {path}", file=sys.stderr)
    return 0


__all__ = ["iv_by_year"]


if __name__ == "__main__":
    raise SystemExit(main())
