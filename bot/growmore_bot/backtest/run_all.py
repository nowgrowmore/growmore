"""CLI: run a strategy x parameter x instrument sweep over available history,
persist results, and print a ranked summary.

Ranking: primary key is profit factor (descending) -- unlike Sharpe/max
drawdown, it's a ratio of a run's own trade outcomes and stays comparable
across commodities at very different price levels and lot sizes (see
docs/technical-debt.md for the scaling bug this sidesteps). `max_drawdown_pct`
and trade count are guardrails, not sort keys -- anything breaching
`--max-drawdown-guardrail` (default 50%) or below `--min-trade-count`
(default 15) is flagged in the printed table rather than silently dropped, so
a human decides what's actually actionable.

Each (strategy family, parameter variant) gets its own `Strategy` row, keyed
by (name, version) -- version encodes the parameters in a short readable
label (e.g. "fast5-slow20"). Re-running with the same variant reuses that
row; a different variant of the same strategy family gets its own row rather
than silently overwriting a stale one's params (the bug this replaced).

Usage:
    python -m growmore_bot.backtest.run_all --from-date 2021-01-01 --to-date 2026-09-01
"""
from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Any, Callable, Sequence

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.broker.dhan_client import DhanClient
from growmore_bot.config import Settings
from growmore_bot.persistence.db import session_scope
from growmore_bot.persistence.models import BacktestTrade, Instrument
from growmore_bot.persistence.models import Strategy as StrategyRow
from growmore_bot.strategies.base import Strategy
from growmore_bot.strategies.bollinger_reversion import BollingerReversionStrategy
from growmore_bot.strategies.donchian_breakout import DonchianBreakoutStrategy
from growmore_bot.strategies.macd_trend import MacdTrendStrategy
from growmore_bot.strategies.regime_switch import RegimeSwitchStrategy
from growmore_bot.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from growmore_bot.strategies.sma_crossover import SmaCrossoverStrategy

DEFAULT_MAX_DRAWDOWN_GUARDRAIL_PCT = 50.0
DEFAULT_MIN_TRADE_COUNT = 15


@dataclass
class RunSummary:
    strategy: str
    version: str
    instrument: str
    trade_count: int
    profit_factor: float
    win_rate_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    flagged_drawdown: bool = field(default=False)
    flagged_thin_sample: bool = field(default=False)


def rank_results(
    results: Sequence[RunSummary],
    max_drawdown_guardrail_pct: float,
    min_trade_count: int,
) -> list[RunSummary]:
    """Return results sorted by profit factor descending, flagging drawdown
    breaches and thin samples. Flagging never removes a result from the list
    -- it's guardrail metadata for the human reading the output, not an
    automated exclusion.
    """
    flagged = [
        RunSummary(
            strategy=r.strategy,
            version=r.version,
            instrument=r.instrument,
            trade_count=r.trade_count,
            profit_factor=r.profit_factor,
            win_rate_pct=r.win_rate_pct,
            sharpe_ratio=r.sharpe_ratio,
            max_drawdown_pct=r.max_drawdown_pct,
            flagged_drawdown=r.max_drawdown_pct > max_drawdown_guardrail_pct,
            flagged_thin_sample=r.trade_count < min_trade_count,
        )
        for r in results
    ]
    return sorted(flagged, key=lambda r: r.profit_factor, reverse=True)


def profit_factor_for_ranking(stored_value: float | None) -> float:
    """`BacktestRun.profit_factor` is stored as None specifically to mean
    "infinite" (all winning trades, zero losses) -- see
    BacktestEngine.run_and_persist. Mapping None to 0.0 here would rank a
    zero-loss run as the WORST possible result instead of the best (a real
    bug found while reviewing this sweep's first real output: several 100%
    win-rate rows showed profit_factor=0.00).
    """
    return float("inf") if stored_value is None else float(stored_value)


def build_strategy_grid() -> list[tuple[str, str, dict, Callable[[], Strategy]]]:
    """The parameter sweep grid: (strategy name, version label, params,
    factory). The last element is a FACTORY, not an instance, deliberately.

    Kept small and reasoned (14 variants total across 5 strategy families),
    not exhaustive -- see docs/pending-actions.md for why an unbounded grid
    search would be a multiple-comparisons trap rather than useful analysis.

    Regression (independent code review 2026-09-04): this used to return one
    already-constructed instance per variant, which `main()` then reused for
    EVERY instrument in the sweep. Every strategy here is stateful (rolling
    close deques, seeded EMAs, previous-crossing flags, ADX's previous bar),
    so each instrument after the first began with the previous commodity's
    price history still loaded -- Gold Mini's ~70,000-level closes sitting in
    the window as Copper's ~700-level bars arrived, fabricating crossings and
    breakouts and corrupting the rankings that decide where real money goes.
    A fresh instance per (instrument, variant) is the only safe contract.
    """
    grid: list[tuple[str, str, dict, Callable[[], Strategy]]] = []

    for fast, slow in [(5, 20), (10, 30), (10, 50), (20, 50)]:
        params: dict[str, Any] = {"fast_period": fast, "slow_period": slow}
        grid.append(
            (
                "sma_crossover",
                f"fast{fast}-slow{slow}",
                params,
                partial(SmaCrossoverStrategy, **params),
            )
        )

    for period in [10, 20, 55]:
        params = {"period": period}
        grid.append(
            (
                "donchian_breakout",
                f"period{period}",
                params,
                partial(DonchianBreakoutStrategy, **params),
            )
        )

    for period, oversold, overbought in [(14, 30, 70), (14, 20, 80), (7, 30, 70)]:
        params = {"period": period, "oversold": oversold, "overbought": overbought}
        grid.append(
            (
                "rsi_mean_reversion",
                f"period{period}-{oversold}-{overbought}",
                params,
                partial(RsiMeanReversionStrategy, **params),
            )
        )

    for fast, slow, sig in [(12, 26, 9), (5, 13, 5)]:
        params = {"fast_period": fast, "slow_period": slow, "signal_period": sig}
        grid.append(
            (
                "macd_trend",
                f"fast{fast}-slow{slow}-sig{sig}",
                params,
                partial(MacdTrendStrategy, **params),
            )
        )

    for period, num_std in [(20, 2.0), (20, 2.5)]:
        params = {"period": period, "num_std": num_std}
        grid.append(
            (
                "bollinger_reversion",
                f"period{period}-k{num_std}",
                params,
                partial(BollingerReversionStrategy, **params),
            )
        )

    # ADX-gated regime-switch: routes to MACD when trending, a mean-reversion
    # strategy when ranging. Crosses the two MACD pairings already proven on
    # Gold Mini (see docs/backtest-results.md) with both ranging-mode options
    # -- real data decides which ranging style is actually better, not a
    # guess. See docs/goldmini-regime-switch-results.md for the results.
    macd_variants = [(12, 26, 9), (5, 13, 5)]
    ranging_variants = [
        ("rsi", {"period": 14, "oversold": 30, "overbought": 70}),
        ("vwap_ema", {"vwap_period": 20, "ema_fast": 8, "ema_slow": 21}),
    ]
    for fast, slow, sig in macd_variants:
        for ranging_name, ranging_params in ranging_variants:
            params = {
                "ranging_strategy": ranging_name,
                "macd_params": {"fast_period": fast, "slow_period": slow, "signal_period": sig},
                "ranging_params": ranging_params,
            }
            version = f"adx14-macd{fast}{slow}{sig}-{ranging_name}"
            grid.append(
                ("regime_switch", version, params, partial(RegimeSwitchStrategy, **params))
            )

    return grid


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--max-drawdown-guardrail",
        type=float,
        default=DEFAULT_MAX_DRAWDOWN_GUARDRAIL_PCT,
        help="Flag any result whose max_drawdown_pct exceeds this (default 50).",
    )
    parser.add_argument(
        "--min-trade-count",
        type=int,
        default=DEFAULT_MIN_TRADE_COUNT,
        help="Flag any result with fewer closed trades than this (default 15).",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    client = DhanClient(client_id=settings.dhan_client_id, access_token=settings.dhan_access_token)
    client.refresh_access_token_if_needed()

    grid = build_strategy_grid()
    summaries: list[RunSummary] = []

    with session_scope() as session:
        instruments = session.query(Instrument).all()
        if not instruments:
            print(
                "No instruments configured in the `instruments` table yet -- "
                "add rows with real Dhan security ids before running backtests.",
                file=sys.stderr,
            )
            return 1

        for instrument in instruments:
            bars = client.get_historical_ohlc(
                instrument, from_date=args.from_date, to_date=args.to_date, interval="day"
            )
            if not bars:
                continue

            for strategy_name, version, params, strategy_factory in grid:
                # A FRESH instance per (instrument, variant) -- strategies are
                # stateful, so sharing one across instruments would carry the
                # previous commodity's price history into this one's window.
                strategy_impl = strategy_factory()
                strategy_row = (
                    session.query(StrategyRow)
                    .filter_by(name=strategy_name, version=version)
                    .one_or_none()
                )
                if strategy_row is None:
                    strategy_row = StrategyRow(
                        id=uuid.uuid4(), name=strategy_name, version=version, params=params
                    )
                    session.add(strategy_row)
                    session.flush()

                engine = BacktestEngine(
                    strategy=strategy_impl,
                    initial_capital=settings.default_virtual_capital,
                    lot_size=instrument.lot_size,
                )
                run_row = engine.run_and_persist(
                    bars,
                    session=session,
                    strategy_id=strategy_row.id,
                    instrument_id=instrument.id,
                    started_at=datetime.now(timezone.utc),
                )
                session.flush()
                trade_count = (
                    session.query(BacktestTrade)
                    .filter_by(backtest_run_id=run_row.id)
                    .filter(BacktestTrade.pnl.isnot(None))
                    .count()
                )
                summaries.append(
                    RunSummary(
                        strategy=strategy_name,
                        version=version,
                        instrument=instrument.symbol,
                        trade_count=trade_count,
                        profit_factor=profit_factor_for_ranking(run_row.profit_factor),
                        win_rate_pct=float(run_row.win_rate_pct or 0),
                        sharpe_ratio=float(run_row.sharpe_ratio or 0),
                        max_drawdown_pct=float(run_row.max_drawdown_pct or 0),
                    )
                )

    ranked = rank_results(
        summaries,
        max_drawdown_guardrail_pct=args.max_drawdown_guardrail,
        min_trade_count=args.min_trade_count,
    )
    print(
        f"{'strategy':<20}{'version':<22}{'instrument':<15}{'trades':>7}"
        f"{'pf':>8}{'win%':>8}{'sharpe':>8}{'maxdd%':>8}  flags"
    )
    for r in ranked:
        flags = " ".join(
            f for f in [
                "DRAWDOWN!" if r.flagged_drawdown else "",
                "THIN!" if r.flagged_thin_sample else "",
            ]
            if f
        )
        print(
            f"{r.strategy:<20}{r.version:<22}{r.instrument:<15}{r.trade_count:>7}"
            f"{r.profit_factor:>8.2f}{r.win_rate_pct:>8.1f}{r.sharpe_ratio:>8.2f}"
            f"{r.max_drawdown_pct:>8.2f}  {flags}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RunSummary",
    "build_strategy_grid",
    "rank_results",
    "profit_factor_for_ranking",
    "main",
]
