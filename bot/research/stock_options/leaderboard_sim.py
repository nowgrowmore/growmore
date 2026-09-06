"""Would trading the top of the leaderboard actually have worked?

    python -m research.stock_options.leaderboard_sim

A ranked table of 210 stocks is backward-looking by construction. The only
question that matters is whether you could have USED it: each year, take the
top N stocks by their record so far, trade only those the following year, and
roll. That is the decision a person actually makes, and it is testable.

Three arms, every year, for every strategy:

    top-N      trade last year's best N names
    all        trade the whole universe equally
    buy&hold   own the stocks instead

If top-N does not beat "all", the ranking carries no usable information even
if some stocks genuinely rank higher than others -- because by the time you
can see the ranking, the edge has moved. That is the honest reading, and it is
the same trap the F&O equity study's best-of-193 leaderboard sat in.

Per-year figures come from SLICING each stock's single equity curve, not from
re-running the engine per year. It is the same path either way, and slicing
keeps positions carried across a year boundary intact -- which is what
actually happens.
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
import sys
from typing import Optional, Sequence

import pandas as pd

from research.fno.manifest import load_manifest
from research.stock_options import chain_cache
from research.stock_options.run_strategies import (
    INITIAL_CAPITAL,
    OUTPUT_DIR,
    run_symbol,
)

#: A year needs at least this many marked days to be scored.
MIN_DAYS_PER_YEAR = 100


def yearly_returns(result) -> dict[int, float]:
    """Calendar-year total return, from slicing the single equity curve."""
    if not result.days or len(result.days) != len(result.equity_curve):
        return {}
    frame = pd.DataFrame({"day": pd.to_datetime(result.days), "eq": result.equity_curve})
    out: dict[int, float] = {}
    for year, group in frame.groupby(frame["day"].dt.year):
        if len(group) < MIN_DAYS_PER_YEAR:
            continue
        first, last = float(group["eq"].iloc[0]), float(group["eq"].iloc[-1])
        if first > 0:
            out[int(year)] = last / first - 1.0
    return out


def simulate(
    per_stock_year: dict[str, dict[str, dict[int, float]]],
    top_n: int,
) -> list[dict]:
    """For each strategy and year, compare top-N against the whole universe.

    Selection uses only years STRICTLY BEFORE the year being scored, so no
    ranking ever sees the returns it is judged on.
    """
    rows: list[dict] = []
    for tag, by_symbol in per_stock_year.items():
        years = sorted({y for r in by_symbol.values() for y in r})
        for year in years[1:]:
            trailing = {
                s: st.mean([v for y, v in r.items() if y < year])
                for s, r in by_symbol.items()
                if any(y < year for y in r) and year in r
            }
            scored = {s: by_symbol[s][year] for s in trailing}
            if len(scored) < top_n + 5:
                continue
            picked = sorted(trailing, key=lambda s: -trailing[s])[:top_n]
            rows.append({
                "strategy": tag,
                "year": year,
                "n_universe": len(scored),
                "top_n": top_n,
                "top_n_return_pct": round(100 * st.mean(scored[s] for s in picked), 3),
                "all_return_pct": round(100 * st.mean(scored.values()), 3),
                "edge_pct": round(
                    100 * (st.mean(scored[s] for s in picked) - st.mean(scored.values())), 3
                ),
            })
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top", type=int, nargs="*", default=[10, 20, 40])
    args = parser.parse_args(argv)

    manifest = {r.symbol for r in load_manifest()}
    symbols = [s for s in chain_cache.cached_symbols() if s in manifest]
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"{len(symbols)} symbols", file=sys.stderr)

    per_stock_year: dict[str, dict[str, dict[int, float]]] = {}
    for index, symbol in enumerate(symbols, start=1):
        try:
            results, _ = run_symbol(symbol)
        except Exception:  # noqa: BLE001 -- one stock must not lose the run
            continue
        for r in results:
            ys = yearly_returns(r)
            if ys:
                per_stock_year.setdefault(r.tag, {})[symbol] = ys
        if index % 25 == 0:
            print(f"  [{index}/{len(symbols)}]", file=sys.stderr)

    if not per_stock_year:
        print("Nothing measured.", file=sys.stderr)
        return 1

    all_rows: list[dict] = []
    for n in args.top:
        all_rows.extend(simulate(per_stock_year, n))
    if not all_rows:
        print("Not enough years to simulate.", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "leaderboard_sim.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n{'strategy':<28}{'topN':>6}{'years':>7}{'top-N %/yr':>12}"
          f"{'all %/yr':>11}{'edge':>9}  verdict")
    print("-" * 88)
    for tag in sorted({r["strategy"] for r in all_rows}):
        for n in args.top:
            g = [r for r in all_rows if r["strategy"] == tag and r["top_n"] == n]
            if not g:
                continue
            edge = st.mean(r["edge_pct"] for r in g)
            wins = sum(1 for r in g if r["edge_pct"] > 0)
            verdict = (
                f"beat the field in {wins}/{len(g)} years" if edge > 0
                else f"LOST to the field ({wins}/{len(g)} years)"
            )
            print(f"{tag:<28}{n:>6}{len(g):>7}"
                  f"{st.mean(r['top_n_return_pct'] for r in g):>11.1f}%"
                  f"{st.mean(r['all_return_pct'] for r in g):>10.1f}%"
                  f"{edge:>+8.1f}%  {verdict}")

    print("\nSelection uses only years strictly BEFORE the year scored, so no")
    print("ranking ever sees the returns it is judged on.")
    print(f"\n-> {path}", file=sys.stderr)
    return 0


__all__ = ["yearly_returns", "simulate", "MIN_DAYS_PER_YEAR", "INITIAL_CAPITAL"]


if __name__ == "__main__":
    raise SystemExit(main())
