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

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from growmore_bot.backtest.run_all import (
    RunSummary,
    build_strategy_grid,
    capital_for_run,
    profit_factor_for_ranking,
    rank_results,
)


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


def _bars(closes):
    """Daily bars with open==close==high==low, enough for a strategy to run on."""
    return [
        SimpleNamespace(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]


def test_build_strategy_grid_yields_a_fresh_strategy_instance_per_call():
    """Regression (independent code review 2026-09-04): the grid used to hold
    ONE already-constructed strategy instance per variant, and `main()`
    reused that same instance for every instrument in the sweep. Strategies
    are stateful (rolling close deques, seeded EMAs, previous-crossing
    flags), so every instrument after the FIRST one started with the
    previous commodity's price history still loaded -- e.g. Gold Mini's
    ~70,000-level closes sitting in the deque as Copper's ~700-level bars
    began arriving, fabricating crossings and corrupting the very rankings
    that decide which strategy gets real money.
    """
    grid = build_strategy_grid()
    assert grid, "grid must not be empty"
    for name, version, _params, factory in grid:
        first = factory()
        second = factory()
        assert first is not second, f"{name}/{version} factory returned the same instance twice"


def test_reusing_one_strategy_instance_across_instruments_corrupts_results():
    """The concrete failure mode the factory guards against: the same
    instance run over a second, differently-scaled price series produces
    different trades than a fresh instance would.
    """
    from growmore_bot.backtest.engine import BacktestEngine

    gold_like = _bars([70000 + 100 * i for i in range(40)])
    copper_like = _bars([700 + (10 if i % 2 else -10) for i in range(40)])

    factory = dict((f"{n}/{v}", f) for n, v, _p, f in build_strategy_grid())[
        "sma_crossover/fast5-slow20"
    ]

    reused = factory()
    BacktestEngine(strategy=reused, initial_capital=100000).run(gold_like)
    leaked = BacktestEngine(strategy=reused, initial_capital=100000).run(copper_like)

    clean = BacktestEngine(strategy=factory(), initial_capital=100000).run(copper_like)

    assert [(t.entry_price, t.exit_price) for t in leaked.trades] != [
        (t.entry_price, t.exit_price) for t in clean.trades
    ]


def test_capital_for_run_notional_mode_equalises_leverage_across_instruments():
    """The whole point: at one flat capital figure, 1 lot of Copper is ~6.9x
    leverage and 1 lot of Crude Oil Mini is ~0.17x, so their CAGRs are not
    comparable. Capitalising each to its own lot notional puts them both at
    exactly 1x."""
    copper_capital = capital_for_run(
        "notional", first_close=1_378.10, lot_size=2500, flat_capital=500_000
    )
    crude_capital = capital_for_run(
        "notional", first_close=8_570.0, lot_size=10, flat_capital=500_000
    )
    assert copper_capital == pytest.approx(3_445_250.0)
    assert crude_capital == pytest.approx(85_700.0)
    # Both are exactly one lot of their own contract -> identical leverage.
    assert 1_378.10 * 2500 / copper_capital == pytest.approx(1.0)
    assert 8_570.0 * 10 / crude_capital == pytest.approx(1.0)


def test_capital_for_run_target_leverage_scales_the_account_down():
    # 2x leverage means half a lot's notional of capital behind one lot.
    assert capital_for_run(
        "notional", first_close=1_000.0, lot_size=100, flat_capital=1, target_leverage=2.0
    ) == pytest.approx(50_000.0)


def test_capital_for_run_flat_mode_reproduces_the_original_behaviour():
    """Retained so the pre-2026-09 runs can be reproduced exactly."""
    for close, lot in [(1_378.10, 2500), (8_570.0, 10)]:
        assert capital_for_run("flat", close, lot, flat_capital=500_000) == 500_000


def test_capital_for_run_rejects_an_unknown_mode_and_bad_leverage():
    with pytest.raises(ValueError):
        capital_for_run("magic", 100.0, 1, flat_capital=1)
    with pytest.raises(ValueError):
        capital_for_run("notional", 100.0, 1, flat_capital=1, target_leverage=0)
