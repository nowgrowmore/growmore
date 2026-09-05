"""Persist a portfolio backtest's results to the real Neon database, so the
dashboard's Smallcap tab can read them -- see
growmore_bot.persistence.models.PortfolioBacktestRun and
docs/smallcap-momentum-backtest-results.md.

Deliberately separate from run_backtest.py's own local CSV output: the CLI
always writes local files first (review the numbers before they reach the
shared database), and persisting is a distinct, explicit step -- same
"local review, then an explicit persist" pattern already used for the
commodity strategy sweep (docs/backtest-results.md).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from growmore_bot.persistence.db import session_scope
from growmore_bot.persistence.models import (
    PortfolioBacktestRun,
    PortfolioEquityCurvePoint,
    PortfolioRebalanceHolding,
)
from research.smallcap_momentum.portfolio_engine import PortfolioResult


def persist_portfolio_backtest(
    universe: str,
    variant: str,
    result: PortfolioResult,
    summary: dict,
    period_start: date,
    period_end: date,
    top_n: int,
    initial_capital: float,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        session.add(
            PortfolioBacktestRun(
                id=run_id,
                universe=universe,
                variant=variant,
                started_at=now,
                period_start=datetime.combine(period_start, datetime.min.time(), tzinfo=timezone.utc),
                period_end=datetime.combine(period_end, datetime.min.time(), tzinfo=timezone.utc),
                top_n=top_n,
                initial_capital=initial_capital,
                final_equity=result.final_equity,
                rebalance_count=len(result.rebalances),
                sharpe_ratio=summary["sharpe_ratio"],
                max_drawdown_pct=summary["max_drawdown_pct"],
                win_rate_pct=summary["win_rate_pct"],
                cagr_pct=summary["cagr_pct"],
                quality_coverage_pct=summary["quality_coverage_pct"],
            )
        )
        for d, equity in result.equity_curve:
            session.add(
                PortfolioEquityCurvePoint(
                    id=uuid.uuid4(),
                    portfolio_backtest_run_id=run_id,
                    ts=datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc),
                    equity=equity,
                )
            )
        for rebalance in result.rebalances:
            for symbol, weight in rebalance.weights.items():
                session.add(
                    PortfolioRebalanceHolding(
                        id=uuid.uuid4(),
                        portfolio_backtest_run_id=run_id,
                        rebalance_date=datetime.combine(
                            rebalance.date, datetime.min.time(), tzinfo=timezone.utc
                        ),
                        symbol=symbol,
                        weight=weight,
                        composite_score=rebalance.scores.get(symbol),
                    )
                )
    return run_id


__all__ = ["persist_portfolio_backtest"]
