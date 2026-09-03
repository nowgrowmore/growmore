"""Tests for growmore_bot.backtest.run_all -- ranking logic.

The Dhan fetch / DB writes are exercised at the integration level; here we
unit test the pure ranking function: rank by profit factor descending
(scale-invariant across commodities at very different price levels, unlike
Sharpe/drawdown before the lot-size fix -- see docs/technical-debt.md), flag
(never silently exclude) anything breaching the drawdown guardrail or below
the minimum trade-count threshold, so a human decides what's actually
actionable rather than the tool hiding results.
"""
from __future__ import annotations

from growmore_bot.backtest.run_all import RunSummary, profit_factor_for_ranking, rank_results


def _summary(**overrides):
    defaults = dict(
        strategy="s",
        version="v1",
        instrument="X",
        trade_count=20,
        profit_factor=1.0,
        win_rate_pct=50.0,
        sharpe_ratio=0.5,
        max_drawdown_pct=5.0,
    )
    defaults.update(overrides)
    return RunSummary(**defaults)


def test_profit_factor_for_ranking_treats_none_as_infinite_not_zero():
    # Regression: BacktestRun.profit_factor is None specifically to mean
    # "infinite" (zero losing trades) -- mapping that to 0.0 ranked a
    # perfect, zero-loss run as the worst possible result instead of the
    # best. Found in this sweep's first real output: several 100% win-rate
    # rows showed profit_factor=0.00.
    assert profit_factor_for_ranking(None) == float("inf")
    assert profit_factor_for_ranking(2.5) == 2.5
    assert profit_factor_for_ranking(0.0) == 0.0


def test_rank_results_orders_by_profit_factor_descending():
    results = [
        _summary(strategy="a", profit_factor=1.2),
        _summary(strategy="b", profit_factor=3.5),
        _summary(strategy="c", profit_factor=2.0),
    ]
    ranked = rank_results(results, max_drawdown_guardrail_pct=50, min_trade_count=15)
    assert [r.strategy for r in ranked] == ["b", "c", "a"]


def test_rank_results_flags_results_over_drawdown_guardrail():
    results = [
        _summary(strategy="safe", profit_factor=2.0, max_drawdown_pct=10),
        _summary(strategy="risky", profit_factor=3.0, max_drawdown_pct=60),
    ]
    ranked = rank_results(results, max_drawdown_guardrail_pct=50, min_trade_count=15)

    by_name = {r.strategy: r for r in ranked}
    assert by_name["safe"].flagged_drawdown is False
    assert by_name["risky"].flagged_drawdown is True
    # Still ranked by profit factor even when flagged -- exclusion is a caller/human decision.
    assert [r.strategy for r in ranked] == ["risky", "safe"]


def test_rank_results_flags_thin_samples_without_excluding():
    results = [
        _summary(strategy="thick", profit_factor=1.5, trade_count=30),
        _summary(strategy="thin", profit_factor=8.8, trade_count=4),
    ]
    ranked = rank_results(results, max_drawdown_guardrail_pct=50, min_trade_count=15)

    by_name = {r.strategy: r for r in ranked}
    assert by_name["thick"].flagged_thin_sample is False
    assert by_name["thin"].flagged_thin_sample is True
    # A thin-sample 8.8 still outranks a thick 1.5 numerically -- flagging is
    # metadata for the human reading the output, not automatic exclusion,
    # same philosophy as the drawdown guardrail.
    assert [r.strategy for r in ranked] == ["thin", "thick"]
