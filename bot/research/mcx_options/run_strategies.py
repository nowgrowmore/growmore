"""Comparison harness for `research.mcx_options.engine.run`: run several
`EngineConfig` variants over the SAME market data and tabulate CAGR/Sharpe/
max-drawdown/win-rate side by side, persisting each run via
`results_store.save_run`.

    python -m research.mcx_options.run_strategies

**No live Dhan/network fetch, ever.** Market data comes from the caller
(`run_comparison`'s `option_chain`/`futures_daily_bars`/`regime_by_day`
parameters, exactly the ones `engine.run` itself takes) or, for `main()`'s
demo, a small fabricated one-underlying history -- the real bhavcopy
scraping/caching pipeline (`fetch.py`, `bhavcopy.py`, `chain_cache.py`) is a
separate, not-yet-verified concern (see those modules' docstrings), not
wired up here.

Mirrors `research/stock_options/run_strategies.py`'s tabulated-comparison
convention (a `HEADER` string plus a per-row formatter), scaled down: that
module ranks 210 stocks x 6 strategies; this one compares a handful of
regime -> target-delta mappings for ONE underlying's cycle history, so a
`leaderboard_sim.py`-style ranking pass would be over-engineering here.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

import pandas as pd

from research.mcx_options import results_store
from research.mcx_options.bhavcopy import MCXOptionRow
from research.mcx_options.engine import EngineConfig, EngineResult, run
from research.mcx_options.regime import Regime, RegimeLabel

#: Matches `research/stock_options/run_strategies.py::INITIAL_CAPITAL`'s
#: role: a declared starting capital for the equity curve/CAGR calculation,
#: not a margin or capital-adequacy gate (the engine itself trades whole
#: lots regardless of this figure -- see `EngineConfig.margin_multiple_of_premium`).
DEFAULT_INITIAL_CAPITAL = 1_000_000.0

HEADER = (
    f"{'label':<24}{'cycles':>8}{'CAGR%':>9}{'Sharpe':>9}{'MaxDD%':>9}"
    f"{'Win%':>8}{'PF':>7}{'final_equity':>16}"
)


@dataclass
class ComparisonRow:
    """One variant's result: its label, cycle/skip counts, and the summary
    dict `results_store.compute_summary` produced (CAGR/Sharpe/max-drawdown/
    win-rate/profit-factor/final-equity/equity-curve)."""

    label: str
    cycles: int
    total_pnl: float
    skipped_entries: int
    summary: dict

    def as_row(self) -> str:
        s = self.summary
        pf = s["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        return (
            f"{self.label:<24}{self.cycles:>8}{s['cagr_pct']:>8.2f}%"
            f"{s['sharpe']:>9.2f}{s['max_drawdown_pct']:>8.2f}%"
            f"{s['win_rate_pct']:>7.1f}%{pf_str:>7}{s['final_equity']:>16,.2f}"
        )


def run_comparison(
    variants: Sequence[tuple[str, EngineConfig]],
    option_chain: Sequence[MCXOptionRow],
    futures_daily_bars: pd.DataFrame,
    regime_by_day: dict,
    futures_contract_expiries: Optional[Sequence[date]] = None,
    realised_vol_by_day: Optional[dict] = None,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    db_path=results_store.DB_PATH,
) -> list[ComparisonRow]:
    """Run every `(label, EngineConfig)` variant over the identical market
    data, persist each run under its own label via `results_store.save_run`,
    and return one `ComparisonRow` per variant in the order given.

    Every variant sees exactly the same `option_chain`/`futures_daily_bars`/
    `regime_by_day`/`futures_contract_expiries`/`realised_vol_by_day` -- the
    whole point of the comparison is holding market data fixed and varying
    only the config (e.g. a flat 0.30-delta baseline vs. a
    regime -> target-delta mapping).
    """
    rows: list[ComparisonRow] = []
    for label, config in variants:
        result: EngineResult = run(
            config, option_chain, futures_daily_bars, regime_by_day,
            futures_contract_expiries=futures_contract_expiries,
            realised_vol_by_day=realised_vol_by_day,
        )
        summary = results_store.save_run(
            label, config, result, initial_capital=initial_capital, db_path=db_path
        )
        rows.append(ComparisonRow(
            label=label,
            cycles=len(result.cycles),
            total_pnl=result.total_pnl,
            skipped_entries=result.skipped_entries,
            summary=summary,
        ))
    return rows


def print_comparison(rows: Sequence[ComparisonRow]) -> None:
    print(HEADER)
    print("-" * len(HEADER))
    for row in rows:
        print(row.as_row())


#: Two configs from the approved plan's comparison, per this module's
#: docstring: a flat 0.30-delta baseline in every regime vs. a
#: regime -> target-delta mapping (~0.30 CONSOLIDATING / ~0.50
#: TREND_FAVORABLE -- `EngineConfig`'s own defaults).
def default_variants(**common: object) -> list[tuple[str, EngineConfig]]:
    return [
        ("flat-0.30-delta", EngineConfig(
            consolidating_target_delta=0.30, trend_favorable_target_delta=0.30, **common,
        )),
        ("dynamic-0.30-0.50-delta", EngineConfig(
            consolidating_target_delta=0.30, trend_favorable_target_delta=0.50, **common,
        )),
    ]


def _synthetic_demo_inputs() -> tuple[list[MCXOptionRow], pd.DataFrame, dict]:
    """A tiny, FABRICATED one-underlying history for `main()`'s demo run --
    not real market data (see module docstring). Just enough for one full
    put-sell -> assignment -> covered-call -> called-away cycle so the demo
    table has something non-trivial to show."""
    d0 = date(2026, 1, 5)    # sell put
    d1 = date(2026, 2, 4)    # put expires ITM -> assigned
    d2 = date(2026, 2, 5)    # sell covered call
    d3 = date(2026, 3, 6)    # call expires ITM -> called away

    def _row(trade_date, expiry, strike, opt_type, close) -> MCXOptionRow:
        return MCXOptionRow(
            trade_date=trade_date, symbol="GOLDM", expiry=expiry, strike=strike,
            opt_type=opt_type, open=close, high=close, low=close, close=close,
            previous_close=close, open_interest=1000, volume=10, value=close * 10,
        )

    chain = [
        _row(d0, d1, strike=90.0, opt_type="PE", close=5.0),
        _row(d2, d3, strike=100.0, opt_type="CE", close=4.0),
    ]
    bars = pd.DataFrame(
        {"close": [100.0, 80.0, 95.0, 110.0]}, index=[d0, d1, d2, d3]
    )
    favorable_pe = RegimeLabel(
        raw="uptrend", put=Regime.TREND_FAVORABLE, call=Regime.TREND_UNFAVORABLE
    )
    favorable_ce = RegimeLabel(
        raw="downtrend", put=Regime.TREND_UNFAVORABLE, call=Regime.TREND_FAVORABLE
    )
    regime = {d0: favorable_pe, d2: favorable_ce}
    return chain, bars, regime


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--initial-capital", type=float, default=DEFAULT_INITIAL_CAPITAL,
    )
    args = parser.parse_args(argv)

    print(
        "No real MCX data pipeline is wired up yet (see fetch.py/bhavcopy.py "
        "docstrings) -- running the comparison over a tiny FABRICATED demo "
        "history, not real market data.",
        file=sys.stderr,
    )
    chain, bars, regime = _synthetic_demo_inputs()
    variants = default_variants(lot_size=100, tick_size=0.05, lots=1)
    rows = run_comparison(
        variants, chain, bars, regime, initial_capital=args.initial_capital,
    )
    print_comparison(rows)
    return 0


__all__ = [
    "DEFAULT_INITIAL_CAPITAL", "HEADER", "ComparisonRow", "run_comparison",
    "print_comparison", "default_variants",
]


if __name__ == "__main__":
    raise SystemExit(main())
