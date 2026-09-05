"""Run the three configs plus the buy-and-hold control across the F&O universe.

    python -m research.fno.run_configs [--symbols ...] [--from-date ...]
                                       [--to-date ...] [--shorts-arm]

**No database, ever.** This goes through `research.dailydata.runner.run_variant`,
which uses the same `BacktestEngine`, the same fill discipline and the same
metrics as the production sweep but touches no session. It deliberately does
NOT use `growmore_bot.backtest.run_all`, which publishes to Neon
unconditionally and has no opt-out flag.

Outputs to `research/.output/fno/`: `perstock.csv`, `crosssection.csv`, and
the console tables.

CAPITAL. Each stock is capitalised at one "lot" of `shares_for_capital(first
close, Rs 5,00,000)` shares, so every stock runs at ~1x leverage and the CAGR
column is comparable across names priced from Rs 15 to Rs 1,50,000 -- the
same fix `capital_for_run`'s notional mode made for MCX contracts whose lot
notionals spanned 40x. The share count is an integer, so expensive stocks
leave capital idle; that drag is measured per stock, not assumed away.

COSTS. `NSE_EQUITY_DELIVERY_COST_MODEL`, not the MCX default -- STT is 0.1%
on both legs where CTT is 0.01% on the sell alone, roughly 20bps a round trip
against 1bp. Tick size is Rs 0.05.

LONG-ONLY is the headline, matching every published result in this repo.
`--shorts-arm` adds a clearly-labelled long/short run of config 1; cash
equity cannot hold a short overnight, so that arm measures what the symmetric
half of these rules would be worth and is NOT executable.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from growmore_bot.costs import NSE_EQUITY_DELIVERY_COST_MODEL
from growmore_bot.risk.sizing import rounding_drag, shares_for_capital
from research.dailydata.runner import RunResult, run_variant
from research.fno import bar_cache, crosssection
from research.fno.configs import BENCHMARK, CONFIGS
from research.fno.fetch_bars import MIN_BARS_FOR_INCLUSION
from research.fno.manifest import load_manifest

OUTPUT_DIR = Path(__file__).resolve().parent.parent / ".output" / "fno"
CAPITAL_PER_STOCK = 500_000.0
EQUITY_TICK_SIZE = 0.05

#: A stock whose rounding drag exceeds this is flagged: its CAGR is
#: understated because an integer share count cannot spend the budget.
ROUNDING_DRAG_FLAG = 0.02


def meta_for(symbol: str, first_close: float) -> dict:
    """The `run_variant` meta shape, with a share count standing in for a lot."""
    shares = shares_for_capital(first_close, CAPITAL_PER_STOCK)
    return {symbol: {"lot_size": shares, "tick_size": EQUITY_TICK_SIZE}}


def run_symbol(
    symbol: str,
    bars: Sequence,
    allow_shorts: bool = False,
) -> dict[str, RunResult]:
    """Every config plus the control, on one stock, over identical bars."""
    meta = meta_for(symbol, float(bars[0].close))
    results: dict[str, RunResult] = {}
    for tag, name, params in [*CONFIGS, BENCHMARK]:
        results[tag] = run_variant(
            symbol, name, params, tag, bars=bars, meta=meta,
            cost_model=NSE_EQUITY_DELIVERY_COST_MODEL,
            allow_shorts=allow_shorts and name != "buy_and_hold",
            size_to_equity=True,
        )
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--shorts-arm", action="store_true",
                        help="Add a long/short run of config 1. NOT executable in cash equity.")
    args = parser.parse_args(argv)

    from_date = date.fromisoformat(args.from_date) if args.from_date else None
    to_date = date.fromisoformat(args.to_date) if args.to_date else None

    rows = load_manifest()
    if args.symbols:
        wanted = set(args.symbols)
        rows = [r for r in rows if r.symbol in wanted]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    per_stock: list[dict] = []
    skipped: list[str] = []
    thin: list[str] = []
    dragged: list[str] = []

    for row in rows:
        if not bar_cache.is_cached(row.symbol):
            skipped.append(row.symbol)
            continue
        bars = bar_cache.load(row.symbol, from_date=from_date, to_date=to_date)
        if len(bars) < MIN_BARS_FOR_INCLUSION:
            thin.append(row.symbol)
            continue

        first_close = float(bars[0].close)
        shares = shares_for_capital(first_close, CAPITAL_PER_STOCK)
        drag = rounding_drag(first_close, shares, CAPITAL_PER_STOCK)
        if drag > ROUNDING_DRAG_FLAG:
            dragged.append(row.symbol)

        try:
            results = run_symbol(row.symbol, bars)
        except Exception as exc:  # noqa: BLE001 -- one bad stock must not lose the run
            print(f"  {row.symbol}: FAILED {str(exc)[:80]}", file=sys.stderr)
            skipped.append(row.symbol)
            continue

        record = {
            "symbol": row.symbol,
            "nse_industry": row.nse_industry,
            "is_defence": row.is_defence,
            "n_bars": len(bars),
            "first_close": round(first_close, 2),
            "shares": shares,
            "rounding_drag_pct": round(drag * 100, 2),
        }
        for tag, result in results.items():
            record[f"{tag}__sharpe"] = round(result.sharpe, 4)
            record[f"{tag}__cagr_pct"] = round(result.cagr_pct, 3)
            record[f"{tag}__maxdd_pct"] = round(result.max_drawdown_pct, 3)
            record[f"{tag}__trades"] = result.trades
            record[f"{tag}__cost"] = round(result.total_cost, 2)
        if args.shorts_arm:
            tag, name, params = CONFIGS[0]
            shorts = run_symbol(row.symbol, bars, allow_shorts=True)[tag]
            record["shorts__sharpe"] = round(shorts.sharpe, 4)
            record["shorts__cagr_pct"] = round(shorts.cagr_pct, 3)
            record["shorts__trades"] = shorts.trades
        per_stock.append(record)
        print(f"  {row.symbol:<14} {len(bars):5d} bars  "
              + "  ".join(f"{t.split('-')[0]}:{r.sharpe:+.2f}" for t, r in results.items()),
              file=sys.stderr)

    if not per_stock:
        print("\nNo stocks measured -- run `python -m research.fno.fetch_bars` first.",
              file=sys.stderr)
        return 1

    per_stock_path = OUTPUT_DIR / "perstock.csv"
    with per_stock_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(per_stock[0].keys()))
        writer.writeheader()
        writer.writerows(per_stock)

    # --- the headline: each config against each stock's OWN buy-and-hold ---
    bench_tag = BENCHMARK[0]
    summaries: list[tuple[str, str, crosssection.CrossSection]] = []
    for metric, better_is_higher in [("sharpe", True), ("cagr_pct", True), ("maxdd_pct", False)]:
        for tag, _name, _params in CONFIGS:
            sign = 1.0 if better_is_higher else -1.0
            pairs = [
                (r["symbol"], sign * r[f"{tag}__{metric}"], sign * r[f"{bench_tag}__{metric}"])
                for r in per_stock
            ]
            summaries.append((metric, tag, crosssection.summarise_pairs(pairs)))

    cross_path = OUTPUT_DIR / "crosssection.csv"
    with cross_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "config", "n", "effective_n", "wins", "win_rate",
                         "mean_delta", "median_delta", "q1_delta", "q3_delta", "sign_test_p"])
        for metric, tag, s in summaries:
            writer.writerow([metric, tag, s.n, s.effective_n, s.wins, round(s.win_rate, 4),
                             round(s.mean_delta, 5), round(s.median_delta, 5),
                             round(s.q1_delta, 5), round(s.q3_delta, 5), round(s.sign_test_p, 6)])

    print(f"\n{len(per_stock)} stocks measured, {len(skipped)} skipped, "
          f"{len(thin)} under {MIN_BARS_FOR_INCLUSION} bars (unmeasured)", file=sys.stderr)
    if dragged:
        print(f"rounding drag > {ROUNDING_DRAG_FLAG:.0%} on {len(dragged)}: "
              f"{', '.join(dragged)}", file=sys.stderr)

    for metric in ("sharpe", "cagr_pct", "maxdd_pct"):
        print(f"\n--- vs each stock's own buy-and-hold: {metric} "
              f"({'higher' if metric != 'maxdd_pct' else 'lower'} is better) ---")
        print(crosssection.HEADER)
        print("-" * len(crosssection.HEADER))
        for m, tag, s in summaries:
            if m == metric:
                print(s.as_row(tag))

    print("\nThe sign-test p is an OPTIMISTIC bound: it assumes the stocks are")
    print("independent, and Indian equities are ~60-70% correlated to the Nifty.")
    print(f"\n-> {per_stock_path}\n-> {cross_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
