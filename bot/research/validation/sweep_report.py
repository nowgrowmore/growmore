"""Rank the latest sweep, always against buy-and-hold.

    python -m research.validation.sweep_report

The old ranking table asked "which strategy scored highest?". That question
produced a five-year answer that inverted the moment a benchmark was added, so
this report never shows a ranking without the benchmark row beside it, and
reports the MARGIN over holding as its own column. A strategy that cannot beat
holding the contract has not earned a place at the top of a table, whatever
its Sharpe.

Reads only the latest run per (strategy, version, instrument) -- re-running
the sweep leaves older copies behind and counting them would double-count.
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd
from sqlalchemy import create_engine, text

from growmore_bot.config import Settings
from growmore_bot.persistence.db import normalize_database_url

BENCHMARK = "buy_and_hold"

QUERY = text(
    """
    SELECT DISTINCT ON (s.name, s.version, i.symbol)
           s.name AS strategy, s.version, i.symbol,
           br.sharpe_ratio, br.cagr_pct, br.max_drawdown_pct,
           br.profit_factor, br.win_rate_pct, br.dsr,
           br.total_transaction_cost, br.initial_capital,
           (SELECT COUNT(*) FROM backtest_trades t WHERE t.backtest_run_id = br.id
              AND t.exit_price IS NOT NULL) AS trades
    FROM backtest_runs br
    JOIN strategies s ON s.id = br.strategy_id
    JOIN instruments i ON i.id = br.instrument_id
    WHERE br.started_at > NOW() - (:hours * INTERVAL '1 hour')
      AND br.sharpe_ratio IS NOT NULL
    ORDER BY s.name, s.version, i.symbol, br.started_at DESC
    """
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)

    engine = create_engine(normalize_database_url(Settings().database_url))
    with engine.connect() as conn:
        df = pd.read_sql(QUERY, conn, params={"hours": args.hours})
    if df.empty:
        print(f"No runs in the last {args.hours}h.", file=sys.stderr)
        return 1

    bench = (df[df.strategy == BENCHMARK]
             .set_index("symbol")[["sharpe_ratio", "cagr_pct", "max_drawdown_pct"]])
    print(f"{len(df)} runs across {df.symbol.nunique()} instruments, "
          f"{df.version.nunique()} variants\n")

    print("=== BUY & HOLD, the benchmark everything below is measured against ===")
    print(f"{'inst':<11}{'CAGR':>9}{'Sharpe':>8}{'MaxDD':>9}")
    for sym, r in bench.sort_values("sharpe_ratio", ascending=False).iterrows():
        print(f"{sym:<11}{r.cagr_pct:>8.1f}%{r.sharpe_ratio:>8.2f}{r.max_drawdown_pct:>8.1f}%")

    strat = df[df.strategy != BENCHMARK].copy()
    strat["bench_sharpe"] = strat.symbol.map(bench.sharpe_ratio)
    strat["bench_cagr"] = strat.symbol.map(bench.cagr_pct)
    strat["edge"] = strat.sharpe_ratio - strat.bench_sharpe
    strat["cagr_edge"] = strat.cagr_pct - strat.bench_cagr

    print(f"\n=== TOP {args.top} BY SHARPE, with the margin over holding ===")
    print(f"{'strategy':<15}{'version':<40}{'inst':<10}{'trds':>5}{'CAGR':>8}"
          f"{'Sharpe':>7}{'MaxDD':>8}{'vs hold':>9}{'DSR':>6}")
    for _, r in strat.sort_values("sharpe_ratio", ascending=False).head(args.top).iterrows():
        dsr = "  --" if pd.isna(r.dsr) else f"{r.dsr:5.2f}"
        flag = "" if r.edge > 0 else "   <- loses to holding"
        print(f"{r.strategy:<15}{r.version:<40}{r.symbol:<10}{int(r.trades):>5}"
              f"{r.cagr_pct:>7.1f}%{r.sharpe_ratio:>7.2f}{r.max_drawdown_pct:>7.1f}%"
              f"{r.edge:>+9.2f}{dsr:>6}{flag}")

    print("\n=== BEST VARIANT PER INSTRUMENT vs HOLDING ===")
    print(f"{'inst':<10}{'best variant':<44}{'Sharpe':>8}{'hold':>7}{'edge':>8}{'CAGR edge':>11}")
    beats = 0
    for sym, g in strat.groupby("symbol"):
        b = g.loc[g.sharpe_ratio.idxmax()]
        beats += b.edge > 0
        print(f"{sym:<10}{b.version[:43]:<44}{b.sharpe_ratio:>8.2f}"
              f"{b.bench_sharpe:>7.2f}{b.edge:>+8.2f}{b.cagr_edge:>+10.1f}%")
    print(f"\nthe best variant beat buy-and-hold on {beats} of {strat.symbol.nunique()} instruments")
    print("NOTE: in-sample. docs/walk-forward-results.md is the out-of-sample answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
