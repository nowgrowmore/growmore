"""Out-of-sample walk-forward for the F&O universe, with NO re-selection.

    python -m research.fno.walk_forward_run [--symbols ...] [--limit N]

Phase 1's conclusion was not "walk-forward the selection", it was **stop
selecting**: "selection helps when it is stable and hurts when it chases; on
this much data it mostly chases" (docs/walk-forward-results.md). Gold Mini's
fold 4 picked the highest training Sharpe of the whole run, 2.48, and
returned -5.4%. So each config here is FIXED, declared before any result was
seen, and the only thing walk-forward does is guarantee that every bar scored
is a bar no fitting ever touched.

That makes this cheap and honest at the same time. There is no training
step -- nothing is chosen -- so a fold is just [warm-up][test] run in one
pass with only the test tail scored (`evaluate_from`), which keeps
indicators warm without ever crediting the warm-up bars to the result. The
buy-and-hold control runs through the identical folds.

Geometry comes from `growmore_bot.backtest.walk_forward`, hard-coded there
"so a re-run cannot quietly become a different experiment": train 504 /
test 126 / step 126. Over 15 years that is ~22 out-of-sample segments per
stock, against 6 for a five-year window -- which is the whole reason the
store goes back to 2010.

Outputs `research/.output/fno/walkforward.csv` and the stitched per-config
summary. No database.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from growmore_bot.backtest.metrics import max_drawdown_pct, sharpe_ratio
from growmore_bot.backtest.walk_forward import (
    DEFAULT_STEP,
    DEFAULT_TEST,
    DEFAULT_TRAIN,
    grid_hash,
    make_folds,
)
from growmore_bot.costs import NSE_EQUITY_DELIVERY_COST_MODEL
from research.dailydata.runner import run_variant
from research.fno import bar_cache, crosssection
from research.fno.configs import BENCHMARK, CONFIGS
from research.fno.fetch_bars import MIN_BARS_FOR_INCLUSION
from research.fno.manifest import load_manifest
from research.fno.run_configs import OUTPUT_DIR, meta_for


def stitched_oos(
    symbol: str,
    bars: Sequence[Any],
    name: str,
    params: dict,
    label: str,
    meta: dict,
    train: int = DEFAULT_TRAIN,
    test: int = DEFAULT_TEST,
    step: int = DEFAULT_STEP,
) -> Optional[dict]:
    """Concatenate every fold's out-of-sample returns into one record.

    Returns None if the series is too short for even one fold -- an
    "unmeasured" stock, not a weak one, which is the treatment
    docs/walk-forward-results.md gave the short-history MCX contracts.
    """
    folds = make_folds(len(bars), train=train, test=test, step=step)
    if not folds:
        return None

    returns: list[float] = []
    trades = 0
    for fold in folds:
        # One pass over [train_start, test_end); score only the test tail.
        segment = bars[fold.train_start : fold.test_end]
        result = run_variant(
            symbol, name, params, label,
            bars=segment, meta=meta,
            cost_model=NSE_EQUITY_DELIVERY_COST_MODEL,
            size_to_equity=True,
            evaluate_from=fold.test_start - fold.train_start,
        )
        returns.extend(result.returns)
        trades += result.trades

    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))

    return {
        "symbol": symbol,
        "config": label,
        "folds": len(folds),
        "oos_sharpe": sharpe_ratio(returns),
        "oos_total_pct": (equity[-1] - 1.0) * 100.0,
        "oos_maxdd_pct": max_drawdown_pct(equity),
        "oos_trades": trades,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="Measure only the first N eligible stocks (smoke testing).")
    args = parser.parse_args(argv)

    rows = load_manifest()
    if args.symbols:
        wanted = set(args.symbols)
        rows = [r for r in rows if r.symbol in wanted]

    grid = [(tag, name, params) for tag, name, params in [*CONFIGS, BENCHMARK]]
    print(f"geometry: train {DEFAULT_TRAIN} / test {DEFAULT_TEST} / step {DEFAULT_STEP}",
          file=sys.stderr)
    print(f"grid hash: {grid_hash(grid)}", file=sys.stderr)

    records: list[dict] = []
    measured = 0
    for row in rows:
        if args.limit is not None and measured >= args.limit:
            break
        if not bar_cache.is_cached(row.symbol):
            continue
        bars = bar_cache.load(row.symbol)
        if len(bars) < MIN_BARS_FOR_INCLUSION:
            continue
        meta = meta_for(row.symbol, float(bars[0].close))

        per_config: dict[str, dict] = {}
        for tag, name, params in grid:
            try:
                record = stitched_oos(row.symbol, bars, name, params, tag, meta)
            except Exception as exc:  # noqa: BLE001 -- one stock must not lose the run
                print(f"  {row.symbol} {tag}: FAILED {str(exc)[:70]}", file=sys.stderr)
                record = None
            if record is None:
                break
            record["nse_industry"] = row.nse_industry
            record["is_defence"] = row.is_defence
            per_config[tag] = record
        if len(per_config) != len(grid):
            continue

        records.extend(per_config.values())
        measured += 1
        print(f"  {row.symbol:<14} folds={per_config[grid[0][0]]['folds']:>2}  "
              + "  ".join(f"{t.split('-')[0]}:{r['oos_sharpe']:+.2f}"
                          for t, r in per_config.items()), file=sys.stderr)

    if not records:
        print("\nNo stocks measured -- run `python -m research.fno.fetch_bars` first.",
              file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path: Path = OUTPUT_DIR / "walkforward.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    bench_tag = BENCHMARK[0]
    by_symbol: dict[str, dict[str, dict]] = {}
    for record in records:
        by_symbol.setdefault(record["symbol"], {})[record["config"]] = record

    print(f"\n--- OUT-OF-SAMPLE, stitched, vs each stock's own buy-and-hold "
          f"({measured} stocks) ---")
    print(crosssection.HEADER)
    print("-" * len(crosssection.HEADER))
    for tag, _name, _params in CONFIGS:
        pairs = [
            (symbol, configs[tag]["oos_sharpe"], configs[bench_tag]["oos_sharpe"])
            for symbol, configs in by_symbol.items()
        ]
        print(crosssection.summarise_pairs(pairs).as_row(tag))

    print("\nThe sign-test p is an OPTIMISTIC bound -- these stocks are not independent.")
    print(f"\n-> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
