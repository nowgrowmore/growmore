"""CLI: run every configured strategy against every configured instrument
over available history, persist results, and print a ranked summary.

Ranking: primary key is Sharpe ratio (descending); `max_drawdown_pct` is a
guardrail, not a sort key -- anything breaching `--max-drawdown-guardrail`
(default 50%) is flagged in the printed table rather than silently dropped,
so a human decides whether "high Sharpe but very risky" is still worth
enabling in `bot_config`.

Usage:
    python -m growmore_bot.backtest.run_all --from-date 2023-01-01 --to-date 2024-01-01
"""
from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.broker.dhan_client import DhanClient
from growmore_bot.config import Settings
from growmore_bot.persistence.db import session_scope
from growmore_bot.persistence.models import Instrument
from growmore_bot.persistence.models import Strategy as StrategyRow
from growmore_bot.strategies.base import Strategy
from growmore_bot.strategies.donchian_breakout import DonchianBreakoutStrategy
from growmore_bot.strategies.sma_crossover import SmaCrossoverStrategy

DEFAULT_MAX_DRAWDOWN_GUARDRAIL_PCT = 50.0


@dataclass
class RunSummary:
    strategy: str
    instrument: str
    sharpe_ratio: float
    max_drawdown_pct: float
    flagged: bool = field(default=False)


def rank_results(
    results: Sequence[RunSummary], max_drawdown_guardrail_pct: float
) -> list[RunSummary]:
    """Return results sorted by Sharpe descending, flagging drawdown breaches.

    Flagging never removes a result from the list -- it's guardrail metadata
    for the human reading the output, not an automated exclusion.
    """
    flagged = [
        RunSummary(
            strategy=r.strategy,
            instrument=r.instrument,
            sharpe_ratio=r.sharpe_ratio,
            max_drawdown_pct=r.max_drawdown_pct,
            flagged=r.max_drawdown_pct > max_drawdown_guardrail_pct,
        )
        for r in results
    ]
    return sorted(flagged, key=lambda r: r.sharpe_ratio, reverse=True)


def _build_strategies() -> dict[str, Strategy]:
    return {
        "sma_crossover": SmaCrossoverStrategy(fast_period=10, slow_period=30),
        "donchian_breakout": DonchianBreakoutStrategy(period=20),
    }


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
    args = parser.parse_args(argv)

    settings = Settings()
    client = DhanClient(client_id=settings.dhan_client_id, access_token=settings.dhan_access_token)
    client.refresh_access_token_if_needed()

    strategies = _build_strategies()
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

            for strategy_name, strategy_impl in strategies.items():
                strategy_row = (
                    session.query(StrategyRow)
                    .filter_by(name=strategy_name)
                    .one_or_none()
                )
                if strategy_row is None:
                    strategy_row = StrategyRow(
                        id=uuid.uuid4(), name=strategy_name, version="1.0", params={}
                    )
                    session.add(strategy_row)
                    session.flush()

                engine = BacktestEngine(
                    strategy=strategy_impl, initial_capital=settings.default_virtual_capital
                )
                run_row = engine.run_and_persist(
                    bars,
                    session=session,
                    strategy_id=strategy_row.id,
                    instrument_id=instrument.id,
                    started_at=datetime.now(timezone.utc),
                )
                summaries.append(
                    RunSummary(
                        strategy=strategy_name,
                        instrument=instrument.symbol,
                        sharpe_ratio=float(run_row.sharpe_ratio or 0),
                        max_drawdown_pct=float(run_row.max_drawdown_pct or 0),
                    )
                )

    ranked = rank_results(summaries, max_drawdown_guardrail_pct=args.max_drawdown_guardrail)
    print(f"{'strategy':<20}{'instrument':<15}{'sharpe':>10}{'max_dd%':>10}  flag")
    for r in ranked:
        flag = "DRAWDOWN!" if r.flagged else ""
        print(
            f"{r.strategy:<20}{r.instrument:<15}{r.sharpe_ratio:>10.2f}"
            f"{r.max_drawdown_pct:>10.2f}  {flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RunSummary", "rank_results", "main"]
