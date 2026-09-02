"""Tests for growmore_bot.backtest.run_all -- ranking logic.

The Dhan fetch / DB writes are exercised at the integration level; here we
unit test the pure ranking function: rank by Sharpe descending, but flag
(exclude from the "safe" top list) anything breaching the drawdown guardrail.
"""
from __future__ import annotations

from growmore_bot.backtest.run_all import RunSummary, rank_results


def test_rank_results_orders_by_sharpe_descending():
    results = [
        RunSummary(strategy="a", instrument="X", sharpe_ratio=0.5, max_drawdown_pct=5),
        RunSummary(strategy="b", instrument="X", sharpe_ratio=1.5, max_drawdown_pct=5),
        RunSummary(strategy="c", instrument="X", sharpe_ratio=1.0, max_drawdown_pct=5),
    ]
    ranked = rank_results(results, max_drawdown_guardrail_pct=50)
    assert [r.strategy for r in ranked] == ["b", "c", "a"]


def test_rank_results_flags_results_over_drawdown_guardrail():
    results = [
        RunSummary(strategy="safe", instrument="X", sharpe_ratio=1.0, max_drawdown_pct=10),
        RunSummary(strategy="risky", instrument="X", sharpe_ratio=2.0, max_drawdown_pct=60),
    ]
    ranked = rank_results(results, max_drawdown_guardrail_pct=50)

    by_name = {r.strategy: r for r in ranked}
    assert by_name["safe"].flagged is False
    assert by_name["risky"].flagged is True
    # Still ranked by Sharpe even when flagged -- exclusion is a caller decision.
    assert [r.strategy for r in ranked] == ["risky", "safe"]
