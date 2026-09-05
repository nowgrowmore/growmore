"""CLI: fetch real Nifty Smallcap 250 / Midcap 150 universes, 5 years of
Dhan price history, and best-effort fundamentals, then run the
momentum(-only)/momentum+quality/momentum+quality+trend-filter portfolio
backtest for each universe and print a ranked comparison.

Usage (from bot/, with the `research` extra installed):
    python -m research.smallcap_momentum.run_backtest \\
        --from-date 2021-09-04 --to-date 2026-09-04

Reuses growmore_bot.backtest.metrics (sharpe_ratio/max_drawdown_pct/
cagr_pct/win_rate_pct) for comparability with the existing commodity
backtest sweep's metric definitions -- see docs/backtest-results.md.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from growmore_bot.backtest.metrics import cagr_pct, max_drawdown_pct, sharpe_ratio, win_rate_pct
from growmore_bot.broker.dhan_client import DhanClient
from growmore_bot.broker.instrument_master import fetch_instrument_master_csv
from growmore_bot.config import Settings
from research.smallcap_momentum.fundamentals import fetch_all_fundamentals, load_cached_fundamentals
from research.smallcap_momentum.portfolio_engine import (
    PortfolioResult,
    index_on_or_before,
    run_portfolio_backtest,
)
from research.smallcap_momentum.price_data import fetch_all_price_histories, load_cached_bars
from research.smallcap_momentum.security_mapping import match_nse_equity_security_id
from research.smallcap_momentum.universe import (
    MIDCAP_150_URL,
    SMALLCAP_250_URL,
    fetch_index_constituents_csv,
    parse_constituents,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent  # bot/research/
CACHE_DIR = BASE_DIR / ".cache"
OUTPUT_DIR = BASE_DIR / ".output" / "smallcap_momentum"

TOP_N = 30
LOOKBACK_DAYS_6M = 126
LOOKBACK_DAYS_12M = 252
TREND_SMA_DAYS = 200

VARIANTS = [
    ("momentum_only", {"use_quality": False, "trend_filter": False}),
    ("momentum_quality", {"use_quality": True, "trend_filter": False}),
    ("momentum_quality_trend", {"use_quality": True, "trend_filter": True}),
]


@dataclass
class UniverseData:
    name: str
    symbols: list[str]
    unmatched_symbols: list[str]


def semiannual_rebalance_dates(from_date: date, to_date: date) -> list[date]:
    dates: list[date] = []
    year = from_date.year
    while True:
        for month, day in [(6, 30), (12, 31)]:
            d = date(year, month, day)
            if from_date <= d <= to_date:
                dates.append(d)
        year += 1
        if date(year, 1, 1) > to_date:
            break
    return dates


def build_universe(name: str, url: str, scrip_master_csv: str) -> UniverseData:
    csv_text = fetch_index_constituents_csv(url)
    constituents = parse_constituents(csv_text)
    symbols: list[str] = []
    unmatched: list[str] = []
    symbol_to_security_id: dict[str, str] = {}
    for c in constituents:
        security_id = match_nse_equity_security_id(scrip_master_csv, c.symbol)
        if security_id is None:
            unmatched.append(c.symbol)
            continue
        symbols.append(c.symbol)
        symbol_to_security_id[c.symbol] = security_id
    logger.info(
        "%s: %d/%d constituents matched to a Dhan NSE_EQ security_id (%d unmatched)",
        name,
        len(symbols),
        len(constituents),
        len(unmatched),
    )
    return UniverseData(name=name, symbols=symbols, unmatched_symbols=unmatched), symbol_to_security_id


def _holding_period_pnls(
    result: PortfolioResult, price_series: dict[str, list[tuple[date, float]]]
) -> list[float]:
    """One return per (rebalance, held symbol) -- entry at that rebalance's
    price, exit at the next rebalance's price (or the backtest's final
    known price for the last period). Feeds win_rate_pct for comparability
    with the per-trade win rate the commodity sweep reports.
    """
    pnls: list[float] = []
    rebalances = result.rebalances
    for i, rebalance in enumerate(rebalances):
        next_date = rebalances[i + 1].date if i + 1 < len(rebalances) else result.equity_curve[-1][0]
        for symbol in rebalance.selected:
            dates = [d for d, _ in price_series[symbol]]
            closes = [c for _, c in price_series[symbol]]
            entry_idx = index_on_or_before(dates, rebalance.date)
            exit_idx = index_on_or_before(dates, next_date)
            if entry_idx is None or exit_idx is None:
                continue
            pnls.append(closes[exit_idx] - closes[entry_idx])
    return pnls


def summarize(
    universe_name: str, variant_name: str, result: PortfolioResult,
    price_series: dict[str, list[tuple[date, float]]], from_date: date, to_date: date,
) -> dict:
    equity_values = [e for _, e in result.equity_curve]
    daily_returns = [
        equity_values[i] / equity_values[i - 1] - 1
        for i in range(1, len(equity_values))
        if equity_values[i - 1] != 0
    ]
    years = (to_date - from_date).days / 365.25
    pnls = _holding_period_pnls(result, price_series)
    coverage = (
        result.rebalances[-1].quality_coverage_count / result.rebalances[-1].eligible_count * 100
        if result.rebalances and result.rebalances[-1].eligible_count
        else 0.0
    )
    return {
        "universe": universe_name,
        "variant": variant_name,
        "rebalances": len(result.rebalances),
        "final_equity": result.final_equity,
        "cagr_pct": cagr_pct(equity_values[0], result.final_equity, years) if equity_values else 0.0,
        "sharpe_ratio": sharpe_ratio(daily_returns),
        "max_drawdown_pct": max_drawdown_pct(equity_values),
        "win_rate_pct": win_rate_pct(pnls),
        "holding_period_count": len(pnls),
        "quality_coverage_pct": coverage,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Write results to the real Neon database (dashboard's Smallcap tab reads this). "
        "Off by default -- review the local summary.csv/equity CSVs first.",
    )
    args = parser.parse_args(argv)
    from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    to_date = datetime.strptime(args.to_date, "%Y-%m-%d").date()

    settings = Settings()
    dhan_client = DhanClient(client_id=settings.dhan_client_id, access_token=settings.dhan_access_token)
    dhan_client.refresh_access_token_if_needed()

    logger.info("Fetching Dhan instrument master...")
    scrip_master_csv = fetch_instrument_master_csv()

    universes = {}
    all_symbol_to_security_id: dict[str, str] = {}
    for name, url in [("smallcap250", SMALLCAP_250_URL), ("midcap150", MIDCAP_150_URL)]:
        universe, symbol_to_security_id = build_universe(name, url, scrip_master_csv)
        universes[name] = universe
        all_symbol_to_security_id.update(symbol_to_security_id)

    logger.info("Fetching price history for %d unique symbols...", len(all_symbol_to_security_id))
    price_fetch_result = fetch_all_price_histories(
        dhan_client,
        all_symbol_to_security_id,
        from_date=args.from_date,
        to_date=args.to_date,
        cache_dir=CACHE_DIR / "prices",
    )
    logger.info(
        "Prices: %d fetched, %d already cached, %d failed",
        len(price_fetch_result.fetched),
        len(price_fetch_result.cached),
        len(price_fetch_result.failed),
    )

    logger.info("Fetching fundamentals for %d symbols...", len(all_symbol_to_security_id))
    fundamentals_result = fetch_all_fundamentals(
        list(all_symbol_to_security_id.keys()), cache_dir=CACHE_DIR / "fundamentals"
    )
    logger.info(
        "Fundamentals: %d covered, %d missing", len(fundamentals_result.covered), len(fundamentals_result.missing)
    )

    price_series: dict[str, list[tuple[date, float]]] = {}
    for symbol in all_symbol_to_security_id:
        df = load_cached_bars(CACHE_DIR / "prices", symbol)
        if df is None or df.empty:
            continue
        price_series[symbol] = list(zip(df["date"].tolist(), df["close"].tolist()))

    fundamentals: dict[str, tuple[float, float, float]] = {}
    for symbol in all_symbol_to_security_id:
        f = load_cached_fundamentals(CACHE_DIR / "fundamentals", symbol)
        if f is not None:
            fundamentals[symbol] = f

    rebalance_dates = semiannual_rebalance_dates(from_date, to_date)
    logger.info("Rebalance dates: %s", rebalance_dates)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries = []
    runs = []  # (universe_name, variant_name, result, universe_price_series, summary)
    for universe_name, universe in universes.items():
        universe_price_series = {s: price_series[s] for s in universe.symbols if s in price_series}
        for variant_name, variant_kwargs in VARIANTS:
            result = run_portfolio_backtest(
                price_series=universe_price_series,
                fundamentals=fundamentals,
                rebalance_dates=rebalance_dates,
                top_n=TOP_N,
                initial_capital=1_000_000.0,
                lookback_days_6m=LOOKBACK_DAYS_6M,
                lookback_days_12m=LOOKBACK_DAYS_12M,
                trend_sma_days=TREND_SMA_DAYS,
                **variant_kwargs,
            )
            summary = summarize(
                universe_name, variant_name, result, universe_price_series, from_date, to_date
            )
            summaries.append(summary)
            runs.append((universe_name, variant_name, result, universe_price_series, summary))

            equity_csv = OUTPUT_DIR / f"equity_{universe_name}_{variant_name}.csv"
            with equity_csv.open("w") as f:
                f.write("date,equity\n")
                for d, e in result.equity_curve:
                    f.write(f"{d.isoformat()},{e}\n")

    summary_csv = OUTPUT_DIR / "summary.csv"
    with summary_csv.open("w") as f:
        headers = list(summaries[0].keys()) if summaries else []
        f.write(",".join(headers) + "\n")
        for s in summaries:
            f.write(",".join(str(s[h]) for h in headers) + "\n")

    print(f"{'universe':<14}{'variant':<24}{'reb':>5}{'cagr%':>9}{'sharpe':>9}{'maxdd%':>9}{'win%':>8}{'qcov%':>8}")
    for s in sorted(summaries, key=lambda r: r["cagr_pct"], reverse=True):
        print(
            f"{s['universe']:<14}{s['variant']:<24}{s['rebalances']:>5}"
            f"{s['cagr_pct']:>9.2f}{s['sharpe_ratio']:>9.2f}{s['max_drawdown_pct']:>9.2f}"
            f"{s['win_rate_pct']:>8.1f}{s['quality_coverage_pct']:>8.1f}"
        )
    print(f"\nWritten: {summary_csv}")

    if args.persist:
        from research.smallcap_momentum.persist_results import persist_portfolio_backtest

        logger.info("Persisting %d runs to the real Neon database...", len(runs))
        for universe_name, variant_name, result, _, summary in runs:
            run_id = persist_portfolio_backtest(
                universe=universe_name,
                variant=variant_name,
                result=result,
                summary=summary,
                period_start=from_date,
                period_end=to_date,
                top_n=TOP_N,
                initial_capital=1_000_000.0,
            )
            logger.info("Persisted %s/%s as %s", universe_name, variant_name, run_id)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "semiannual_rebalance_dates", "build_universe", "summarize"]
