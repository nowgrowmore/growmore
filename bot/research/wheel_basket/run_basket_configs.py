"""Run the declared wheel-basket configs and write the results tables.

    python -m research.wheel_basket.run_basket_configs
    python -m research.wheel_basket.run_basket_configs --stage sector

STAGES RUN SEQUENTIALLY, NEVER AS A CROSS PRODUCT. Stage B (sector) is
measured against the B0 baseline; stage C (regime) and stage D (per-stock)
are each measured against stage B's WINNER, one decision at a time. The
cross-product of the same ideas is 48 configs, and with the IV cut and
hysteresis swept it is 432 -- at which size the deflated-Sharpe bar rises
faster than any edge here could clear it. See docs/wheel-basket-research.md
Sec 4.

Nothing here writes to Neon. Output is CSV under research/.output/wheel_basket/.
"""
from __future__ import annotations

import argparse
import csv
import sys

from pathlib import Path
from typing import Optional

from growmore_bot.wheel_basket.universe import load_universe
from research.wheel_basket import panel_cache
from research.wheel_basket.basket_engine import run_basket
from research.wheel_basket.config import (
    BASELINE_CONFIGS,
    CONTROL_CONFIGS,
    BasketConfig,
    per_stock_configs,
    regime_configs,
    sector_configs,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / ".output" / "wheel_basket"
INITIAL_CAPITAL = 10_000_000.0

COLUMNS = [
    "tag", "cagr_pct", "sharpe", "max_drawdown_pct", "win_rate_pct",
    "profit_factor", "cycles", "assignments", "called_away", "max_concurrent_positions", "unfinished_positions",
    "mean_sector_share", "max_sector_share", "defence_share", "peak_deployed_fraction",
    "peak_entry_exposure_fraction", "max_put_reserve_ratio",
    "time_underwater_pct", "time_frozen_pct", "min_cash",
    "total_cost", "final_equity", "symbols_traded", "per_year_return_pct",
]


def load_panels(limit: Optional[int] = None) -> dict:
    """Every symbol with a cached panel. Built by `--build-panels` first."""
    panels = {}
    for path in sorted(panel_cache.CACHE_DIR.glob("*.pkl")):
        panel = panel_cache.load(path.stem)
        if panel is not None:
            panels[panel.symbol] = panel
        if limit and len(panels) >= limit:
            break
    return panels


def sector_maps() -> tuple[dict, dict]:
    rows = load_universe()
    return (
        {r.symbol: r.nse_industry for r in rows},
        {r.symbol: r.is_defence for r in rows},
    )


def _row(result) -> dict:
    return {
        "tag": result.tag,
        "cagr_pct": round(result.cagr_pct, 3),
        "sharpe": round(result.sharpe, 3),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "win_rate_pct": round(result.win_rate_pct, 2),
        "profit_factor": "" if result.profit_factor is None else round(result.profit_factor, 3),
        "cycles": result.cycles,
        "assignments": result.assignments,
        "called_away": result.called_away,
        "max_concurrent_positions": result.max_concurrent_positions,
        "unfinished_positions": result.unfinished_positions,
        "mean_sector_share": round(result.mean_sector_share, 4),
        "max_sector_share": round(result.max_sector_share, 4),
        "defence_share": round(result.defence_share, 4),
        "peak_deployed_fraction": round(result.peak_deployed_fraction, 4),
        "peak_entry_exposure_fraction": round(result.peak_entry_exposure_fraction, 4),
        "max_put_reserve_ratio": round(result.max_put_reserve_ratio, 4),
        "time_underwater_pct": round(result.time_underwater_pct, 2),
        "time_frozen_pct": round(result.time_frozen_pct, 2),
        "min_cash": round(result.min_cash, 2),
        "total_cost": round(result.total_cost, 2),
        "final_equity": round(result.final_equity, 2),
        "symbols_traded": len(result.symbols_traded),
        "per_year_return_pct": " ".join(
            f"{y}:{v}" for y, v in result.per_year_return_pct.items()
        ),
    }


def wins(candidate, baseline) -> bool:
    """The accept rule declared in Sec 7, applied without discretion.

    Beats the baseline on CAGR without worsening drawdown by more than 2
    points, OR improves drawdown by more than 3 points for no more than 0.5
    CAGR points. Per-year and matched-pair conditions are checked separately
    in the results write-up; this is the mechanical screen.
    """
    better_return = (
        candidate.cagr_pct > baseline.cagr_pct
        and candidate.max_drawdown_pct <= baseline.max_drawdown_pct + 2.0
    )
    better_risk = (
        candidate.max_drawdown_pct < baseline.max_drawdown_pct - 3.0
        and candidate.cagr_pct >= baseline.cagr_pct - 0.5
    )
    return better_return or better_risk


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-panels", action="store_true",
                        help="build and cache the per-symbol panels, then exit")
    parser.add_argument("--limit", type=int, default=None,
                        help="use only the first N symbols (a smoke test, not a result)")
    parser.add_argument("--stage", choices=("baseline", "sector", "regime", "per_stock", "all"),
                        default="all")
    args = parser.parse_args(argv)

    if args.build_panels:
        from research.stock_options import chain_cache
        built = 0
        for symbol in chain_cache.cached_symbols():
            if panel_cache.build_and_cache(symbol) is not None:
                built += 1
        print(f"cached {built} panels in {panel_cache.CACHE_DIR}")
        return 0

    panels = load_panels(limit=args.limit)
    if not panels:
        print("No cached panels. Run with --build-panels first.", file=sys.stderr)
        return 1
    sectors, defence = sector_maps()
    print(f"{len(panels)} symbols, "
          f"{sum(len(p.cycles) for p in panels.values())} symbol-cycles")

    def run(config: BasketConfig):
        result = run_basket(panels, sectors, defence, config,
                            initial_capital=INITIAL_CAPITAL)
        row = _row(result)
        print("  " + "  ".join(
            f"{k}={row[k]}" for k in
            ("tag", "cagr_pct", "sharpe", "max_drawdown_pct",
             "mean_sector_share", "peak_deployed_fraction", "cycles")
        ))
        return result, row

    rows, results = [], {}
    for config in BASELINE_CONFIGS:
        result, row = run(config)
        rows.append(row)
        results[config.tag] = result

    for config in CONTROL_CONFIGS:
        result, row = run(config)
        rows.append(row)
        results[config.tag] = result

    # Sector variants are measured against the CONTROL, not the live baseline:
    # B0 leaves a large share of the pool idle, so anything that shrinks the
    # candidate list would otherwise score a deployment gain as a sector gain.
    baseline = results["B1-fixed-slots"]
    stage_b_winner = CONTROL_CONFIGS[0]

    if args.stage in ("sector", "all"):
        best = baseline
        for config in sector_configs(stage_b_winner):
            result, row = run(config)
            rows.append(row)
            if wins(result, best):
                best, stage_b_winner = result, config
        print(f"stage B winner: {stage_b_winner.tag}")

    if args.stage in ("regime", "all"):
        # Regime needs the index series, which needs a live Dhan token. Until
        # it is fetched these run with no labels, which by design behaves
        # exactly like the baseline -- so they are reported as NOT RUN rather
        # than as a result, since a null result here would be indistinguishable
        # from "the mechanism does nothing".
        print("stage C (regime): SKIPPED -- no index data cached yet "
              "(see docs/wheel-basket-research.md Sec 3)")

    if args.stage in ("per_stock", "all"):
        for config in per_stock_configs(stage_b_winner):
            if config.relative_strength_tiebreak:
                print(f"  {config.tag}: SKIPPED -- needs the index series")
                continue
            result, row = run(config)
            rows.append(row)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "configs.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
