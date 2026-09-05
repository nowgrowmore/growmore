"""CLI: how much of the sweep's best result is selection luck?

Applies the Deflated Sharpe Ratio to the runs stored in `backtest_runs`. The
number that matters and is easiest to get wrong is N, the EFFECTIVE number of
independent trials. It is emphatically not the raw run count: variants within
a strategy family are near-copies of each other, and instruments within a
correlated block (two precious metals, four base metals) move together, so
192 backtests are nowhere near 192 independent bets.

N is estimated from the stored equity curves themselves via the participation
ratio of the correlation matrix's eigenvalues, N_eff = (sum L)^2 / sum(L^2) --
the standard "how many independent directions are in here" measure. That is
cross-checked against a simple correlation-threshold cluster count, and the
SMALLER (more conservative, i.e. higher bar) of the two is used.

Read-only by default. pandas/numpy are fine here -- this is the research
layer, not growmore_bot. Pass --persist to write each considered run's DSR
back onto `backtest_runs.dsr` (added specifically so the dashboard has a
real data source for a DSR column/explanation instead of a number only
ever hand-pasted into docs/backtest-results.md) -- this is the ONE write
this module ever does, and it only ever touches the `dsr` column.

Usage (from bot/):
    python -m research.validation.deflate_sweep
    python -m research.validation.deflate_sweep --hours 24
    python -m research.validation.deflate_sweep --hours 999999 --persist
"""
from __future__ import annotations

import argparse
import math
import sys
from typing import Sequence

import pandas as pd
from sqlalchemy import create_engine, text

from growmore_bot.backtest.deflated_sharpe import (
    annualised_to_per_observation,
    deflated_sharpe_ratio,
    expected_max_sharpe,
)
from growmore_bot.config import Settings
from growmore_bot.persistence.db import normalize_database_url
from research.validation.effective_trials import effective_trials

TRADING_DAYS = 252


def _engine():
    return create_engine(normalize_database_url(Settings().database_url))


def load_runs(hours: int) -> pd.DataFrame:
    query = text(
        """
        SELECT DISTINCT ON (s.name, s.version, i.symbol)
               br.id, s.name AS strategy, s.version, i.symbol,
               br.sharpe_ratio, br.cagr_pct, br.max_drawdown_pct
        FROM backtest_runs br
        JOIN strategies s ON s.id = br.strategy_id
        JOIN instruments i ON i.id = br.instrument_id
        WHERE br.started_at > NOW() - (:hours * INTERVAL '1 hour')
          AND br.sharpe_ratio IS NOT NULL
        -- Only the LATEST run per (strategy, version, instrument). Re-running
        -- the sweep leaves older copies behind, and counting them would be
        -- doubly wrong: it inflates the trial count, and duplicates are
        -- perfectly correlated with each other, which collapses the
        -- effective-trials estimate this whole calculation turns on.
        ORDER BY s.name, s.version, i.symbol, br.started_at DESC
        """
    )
    with _engine().connect() as conn:
        return pd.read_sql(query, conn, params={"hours": hours})


def load_return_matrix(run_ids: Sequence[str]) -> pd.DataFrame:
    """Daily returns per run, aligned on date. Runs are the columns."""
    query = text(
        """
        SELECT backtest_run_id, ts, equity
        FROM equity_curve_points
        WHERE backtest_run_id = ANY(:ids)
        ORDER BY ts
        """
    )
    with _engine().connect() as conn:
        raw = pd.read_sql(query, conn, params={"ids": list(run_ids)})
    if raw.empty:
        return pd.DataFrame()
    wide = raw.pivot_table(index="ts", columns="backtest_run_id", values="equity")
    return wide.astype(float).pct_change(fill_method=None).dropna(how="all")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=6,
                        help="Consider runs started within this many hours (default 6).")
    parser.add_argument("--persist", action="store_true",
                        help="Write each considered run's DSR back onto backtest_runs.dsr.")
    args = parser.parse_args(argv)

    runs = load_runs(args.hours)
    if runs.empty:
        print(f"No backtest runs in the last {args.hours}h.", file=sys.stderr)
        return 1

    sharpes = runs["sharpe_ratio"].astype(float)
    per_obs = sharpes.apply(lambda s: annualised_to_per_observation(s, TRADING_DAYS))
    sharpe_variance = float(per_obs.var(ddof=0))

    returns = load_return_matrix(runs["id"].tolist())
    participation, clusters = effective_trials(returns)
    n_eff = max(1, int(round(min(participation, clusters))))

    n_obs = int(returns.shape[0]) if not returns.empty else TRADING_DAYS * 5
    bar_per_obs = expected_max_sharpe(sharpe_variance, n_eff)
    bar_annual = bar_per_obs * math.sqrt(TRADING_DAYS)

    print(f"runs considered           : {len(runs)}")
    print(f"observations per run      : {n_obs}")
    print(f"stdev of Sharpe across runs: {math.sqrt(sharpe_variance) * math.sqrt(TRADING_DAYS):.3f} (annualised)")
    print(f"effective trials          : {n_eff}  "
          f"(participation ratio {participation:.1f}, correlation clusters {clusters})")
    print(f"selection-luck bar        : Sharpe {bar_annual:.2f} annualised "
          f"-- the best of {n_eff} independent trials would be expected to post this by chance")
    print()

    best = runs.assign(per_obs=per_obs).sort_values("sharpe_ratio", ascending=False)

    # Computed for EVERY considered run (not just the printed top 15) --
    # --persist needs the full set, and computing it once here means the
    # print loop below and the persist step can't disagree with each other.
    dsr_by_run_id: dict[str, float] = {}
    for _, row in best.iterrows():
        column = returns.get(row["id"])
        skew = float(column.skew()) if column is not None and column.notna().sum() > 3 else 0.0
        kurt = float(column.kurtosis()) + 3.0 if column is not None and column.notna().sum() > 3 else 3.0
        try:
            dsr_by_run_id[row["id"]] = deflated_sharpe_ratio(
                observed_sharpe=row["per_obs"],
                sharpe_variance=sharpe_variance,
                n_trials=n_eff,
                n_observations=n_obs,
                skew=skew,
                kurtosis=kurt,
            )
        except ValueError:
            dsr_by_run_id[row["id"]] = float("nan")

    print(f"{'strategy':<20}{'version':<34}{'inst':<11}{'Sharpe':>8}{'DSR':>8}  verdict")
    print("-" * 100)
    for _, row in best.head(15).iterrows():
        dsr = dsr_by_run_id[row["id"]]
        verdict = "significant" if dsr >= 0.95 else ("borderline" if dsr >= 0.80 else "NOT distinguishable from luck")
        print(f"{row['strategy']:<20}{row['version']:<34}{row['symbol']:<11}"
              f"{float(row['sharpe_ratio']):>8.2f}{dsr:>8.2f}  {verdict}")

    if args.persist:
        persisted = persist_dsr(dsr_by_run_id)
        print(f"\npersisted DSR for {persisted} run(s) onto backtest_runs.dsr")

    return 0


def persist_dsr(dsr_by_run_id: dict[str, float]) -> int:
    """Write each run's DSR back onto its own `backtest_runs.dsr` -- the one
    write this read-only-by-default module ever does, and only this column.
    NaN values (a run whose DSR couldn't be computed, e.g. too few
    observations) are skipped, not written as NULL-via-NaN.
    """
    clean = {rid: dsr for rid, dsr in dsr_by_run_id.items() if not math.isnan(dsr)}
    if not clean:
        return 0
    with _engine().begin() as conn:
        for run_id, dsr in clean.items():
            conn.execute(
                text("UPDATE backtest_runs SET dsr = :dsr WHERE id = :id"),
                {"dsr": dsr, "id": run_id},
            )
    return len(clean)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["load_runs", "load_return_matrix", "effective_trials", "persist_dsr", "main"]
