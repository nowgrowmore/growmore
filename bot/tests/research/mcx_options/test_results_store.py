"""TDD for `research.mcx_options.results_store`: a local, file-only backtest
results store (SQLite) that must NEVER touch Neon/Postgres or
`growmore_bot.persistence` -- this is offline research, kept structurally
separate from the paper-trading production schema per repo CLAUDE.md.

Round-trip is checked against a small FABRICATED `EngineResult`-like run
(hand-built `CycleRecord`/`LegRecord` objects, not a real `engine.run()`
call -- this module doesn't need the engine to be correct, only to persist
and reload whatever shape it produces), and summary stats are checked
against `growmore_bot/backtest/metrics.py`'s own functions called
independently on the same hand-known equity/pnl series, so the assertion is
a real cross-check rather than a tautology against the implementation.
"""
from __future__ import annotations

from datetime import date

import pytest

from growmore_bot.backtest.metrics import (
    cagr_pct,
    max_drawdown_pct,
    profit_factor,
    sharpe_ratio,
    win_rate_pct,
)
from growmore_bot.costs import CostModel
from research.mcx_options import results_store
from research.mcx_options.engine import CycleRecord, EngineConfig, EngineResult, LegRecord

LOT_SIZE = 100
TICK_SIZE = 0.05

OPTION_COST_MODEL = CostModel(
    brokerage_per_order=1e9, brokerage_pct=0.01,
    exchange_txn_pct=0.0, ctt_sell_pct=0.0, stt_both_pct=0.0, stt_sell_pct=0.0,
    stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
    slippage_ticks=0.0, stop_slippage_ticks=0.0, reviewed=True,
)


def _config() -> EngineConfig:
    return EngineConfig(
        lot_size=LOT_SIZE, tick_size=TICK_SIZE, lots=2,
        consolidating_target_delta=0.25, trend_favorable_target_delta=0.45,
        option_cost_model=OPTION_COST_MODEL,
        futures_roll_cost_per_lot=12.5,
    )


def _fabricated_result() -> tuple[EngineResult, float]:
    d0, d1, d2, d3 = date(2026, 1, 5), date(2026, 2, 4), date(2026, 2, 5), date(2026, 3, 6)

    cycle_a = CycleRecord(
        start_date=d0, end_date=d1, outcome="put_expired_otm", basis=None,
        legs=[LegRecord(date=d0, action="sell_put", strike=90.0, pnl=95.0, cost=5.0)],
        total_pnl=95.0,
    )
    cycle_b = CycleRecord(
        start_date=d2, end_date=d3, outcome="called_away", basis=100.0,
        legs=[
            LegRecord(date=d2, action="assigned", strike=100.0, pnl=-8.0, cost=8.0,
                      margin_used=300.0, note="basis = strike"),
            LegRecord(date=d3, action="called_away", strike=105.0, pnl=-45.0, cost=6.0),
        ],
        total_pnl=-53.0,
    )

    days = [d0, d1, d2, d3]
    daily_pnl = [95.0, 0.0, -8.0, -45.0]
    result = EngineResult(
        cycles=[cycle_a, cycle_b], days=days, daily_pnl=daily_pnl,
        total_pnl=42.0, skipped_entries=3,
    )
    initial_capital = 1_000.0
    return result, initial_capital


def _hand_known_summary(result: EngineResult, initial_capital: float) -> dict:
    equity_curve = [initial_capital]
    for pnl in result.daily_pnl:
        equity_curve.append(equity_curve[-1] + pnl)
    returns = [(b / a - 1) for a, b in zip(equity_curve, equity_curve[1:]) if a != 0]
    years = max((result.days[-1] - result.days[0]).days / 365.25, 0.0)
    trade_pnls = [c.total_pnl for c in result.cycles if c.outcome and c.outcome != "still_open"]
    return {
        "cagr_pct": cagr_pct(initial_capital, equity_curve[-1], years),
        "sharpe": sharpe_ratio(returns),
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "win_rate_pct": win_rate_pct(trade_pnls),
        "profit_factor": profit_factor(trade_pnls),
        "final_equity": equity_curve[-1],
        "equity_curve": equity_curve,
    }


def test_save_and_load_round_trips_config_cycles_and_summary(tmp_path):
    db_path = tmp_path / "results.sqlite3"
    config = _config()
    result, initial_capital = _fabricated_result()
    expected_summary = _hand_known_summary(result, initial_capital)

    saved_summary = results_store.save_run(
        "baseline", config, result, initial_capital=initial_capital, db_path=db_path
    )
    assert saved_summary["final_equity"] == pytest.approx(expected_summary["final_equity"])
    assert saved_summary["cagr_pct"] == pytest.approx(expected_summary["cagr_pct"])
    assert saved_summary["sharpe"] == pytest.approx(expected_summary["sharpe"])
    assert saved_summary["max_drawdown_pct"] == pytest.approx(expected_summary["max_drawdown_pct"])
    assert saved_summary["win_rate_pct"] == pytest.approx(expected_summary["win_rate_pct"])
    assert saved_summary["profit_factor"] == pytest.approx(expected_summary["profit_factor"])

    loaded = results_store.load_run("baseline", db_path=db_path)
    assert loaded is not None

    loaded_config = loaded["config"]
    assert loaded_config == config
    assert loaded_config.option_cost_model == OPTION_COST_MODEL

    loaded_cycles = loaded["cycles"]
    assert len(loaded_cycles) == 2
    assert loaded_cycles[0] == result.cycles[0]
    assert loaded_cycles[1] == result.cycles[1]
    assert loaded_cycles[1].legs[0].note == "basis = strike"

    assert loaded["days"] == result.days
    assert loaded["daily_pnl"] == result.daily_pnl
    assert loaded["total_pnl"] == pytest.approx(result.total_pnl)
    assert loaded["skipped_entries"] == result.skipped_entries

    summary = loaded["summary"]
    for key in ("cagr_pct", "sharpe", "max_drawdown_pct", "win_rate_pct", "profit_factor",
                "final_equity"):
        assert summary[key] == pytest.approx(expected_summary[key])
    assert summary["equity_curve"] == pytest.approx(expected_summary["equity_curve"])


def test_load_missing_run_returns_none(tmp_path):
    db_path = tmp_path / "results.sqlite3"
    assert results_store.load_run("does-not-exist", db_path=db_path) is None


def test_save_run_overwrites_existing_label(tmp_path):
    db_path = tmp_path / "results.sqlite3"
    config = _config()
    result, initial_capital = _fabricated_result()

    results_store.save_run("dup", config, result, initial_capital=initial_capital, db_path=db_path)
    result2, _ = _fabricated_result()
    result2.total_pnl = 999.0
    results_store.save_run("dup", config, result2, initial_capital=initial_capital, db_path=db_path)

    loaded = results_store.load_run("dup", db_path=db_path)
    assert loaded["total_pnl"] == pytest.approx(999.0)
    assert results_store.list_runs(db_path=db_path) == ["dup"]


def test_list_runs_lists_all_saved_labels(tmp_path):
    db_path = tmp_path / "results.sqlite3"
    config = _config()
    result, initial_capital = _fabricated_result()
    results_store.save_run("a", config, result, initial_capital=initial_capital, db_path=db_path)
    results_store.save_run("b", config, result, initial_capital=initial_capital, db_path=db_path)
    assert sorted(results_store.list_runs(db_path=db_path)) == ["a", "b"]
