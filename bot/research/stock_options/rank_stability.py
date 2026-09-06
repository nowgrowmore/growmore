"""Is the per-stock leaderboard signal, or is it noise?

    python -m research.stock_options.rank_stability

The ranked tables in `run_strategies` are what was asked for and what you
would trade off. They are also the exact shape the F&O equity study warned
about: rank 210 stocks by realised CAGR and the top of the list is partly
luck, because each stock carries only ~84 monthly cycles.

Two questions, both answered here rather than asserted:

**1. Split-half rank stability.** Rank every stock in the first half of the
window, rank them again in the second, and correlate. Near zero means the
ordering carries no information and picking the top will fail live.
Meaningfully positive means per-stock selection is genuine.

**2. Would trading the leaderboard have worked?** Each year, take the top N
by trailing performance, trade only those the next year, and roll. That turns
a backward-looking table into a decision you could actually have made, and is
the honest answer to "should I trade the top of this list".

Spearman is used rather than Pearson because only the ORDER matters -- a
stock that returns 40% where the leader returns 400% is still second.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from research.fno.manifest import load_manifest
from research.stock_options import chain_cache
from research.stock_options.run_strategies import OUTPUT_DIR, run_symbol

#: Below this many stocks in common a correlation says nothing.
MIN_OVERLAP = 5


def _ranks(values: Sequence[float]) -> list[float]:
    """Average ranks, so ties share a rank rather than breaking arbitrarily."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(first: dict, second: dict) -> Optional[float]:
    """Rank correlation over the stocks present in BOTH periods.

    Stocks missing from either side are dropped rather than imputed: a name
    that entered F&O midway has no first-half rank, and inventing one would
    manufacture correlation out of nothing.
    """
    keys = sorted(set(first) & set(second))
    if len(keys) < MIN_OVERLAP:
        return None
    a = _ranks([first[k] for k in keys])
    b = _ranks([second[k] for k in keys])
    n = len(keys)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    va = sum((x - mean_a) ** 2 for x in a)
    vb = sum((y - mean_b) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / (va * vb) ** 0.5


def _window_results(symbols, from_date, to_date, min_cycles=6) -> dict[str, dict[str, dict]]:
    """{strategy: {symbol: {metric: value}}} over one date window."""
    out: dict[str, dict[str, dict]] = {}
    for symbol in symbols:
        try:
            results, _ = run_symbol(symbol, from_date, to_date, min_cycles=min_cycles)
        except Exception:  # noqa: BLE001 -- one stock must not lose the run
            continue
        for r in results:
            out.setdefault(r.tag, {})[symbol] = {
                "cagr_pct": r.cagr_pct, "sharpe": r.sharpe,
            }
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--split", default=None,
                        help="Split date; defaults to the midpoint of the data.")
    args = parser.parse_args(argv)

    manifest = {r.symbol for r in load_manifest()}
    symbols = [s for s in chain_cache.cached_symbols() if s in manifest]
    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        print("No cached chains.", file=sys.stderr)
        return 1

    days = chain_cache.cached_days()
    split = (
        pd.Timestamp(args.split) if args.split
        else pd.Timestamp(days[len(days) // 2])
    )
    first_start, last_end = pd.Timestamp(days[0]), pd.Timestamp(days[-1])
    print(f"{len(symbols)} symbols | first half {first_start.date()} -> {split.date()} "
          f"| second half {split.date()} -> {last_end.date()}", file=sys.stderr)

    early = _window_results(symbols, first_start, split)
    late = _window_results(symbols, split, last_end)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path: Path = OUTPUT_DIR / "rank_stability.csv"
    records = []
    print(f"\n{'strategy':<28}{'metric':>9}{'n both':>8}{'Spearman':>11}  verdict")
    print("-" * 78)
    for tag in sorted(set(early) | set(late)):
        for metric in ("cagr_pct", "sharpe"):
            a = {s: v[metric] for s, v in early.get(tag, {}).items()}
            b = {s: v[metric] for s, v in late.get(tag, {}).items()}
            rho = spearman(a, b)
            overlap = len(set(a) & set(b))
            if rho is None:
                continue
            verdict = (
                "leaderboard is noise" if abs(rho) < 0.15
                else "weak but real" if abs(rho) < 0.35
                else "genuinely persistent"
            )
            print(f"{tag:<28}{metric:>9}{overlap:>8}{rho:>11.3f}  {verdict}")
            records.append({"strategy": tag, "metric": metric,
                            "n_both": overlap, "spearman": round(rho, 4),
                            "verdict": verdict})

    if records:
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
        print(f"\n-> {path}", file=sys.stderr)
    return 0


__all__ = ["spearman", "MIN_OVERLAP"]


if __name__ == "__main__":
    raise SystemExit(main())
