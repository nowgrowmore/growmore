"""TDD for `research.mcx_options.run_strategies`: the config-comparison
harness that runs several `EngineConfig` variants over the SAME market data,
tabulates CAGR/Sharpe/max-drawdown/win-rate (via
`growmore_bot/backtest/metrics.py`, through `results_store.compute_summary`),
and persists each run via `results_store.save_run`.

Uses the same single-candidate-chain fixture style as `test_engine.py` (one
strike per day/expiry/side, so which strike gets picked is never in
question), and hand-computes the expected total P&L for each variant
independently -- a real cross-check, not a tautology against the
implementation.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from growmore_bot.costs import CostModel
from research.mcx_options import results_store
from research.mcx_options.bhavcopy import MCXOptionRow
from research.mcx_options.engine import EngineConfig
from research.mcx_options.regime import Regime, RegimeLabel
from research.mcx_options.run_strategies import run_comparison

LOT_SIZE = 100

OPTION_COST_MODEL = CostModel(
    brokerage_per_order=1e9, brokerage_pct=0.01,
    exchange_txn_pct=0.0, ctt_sell_pct=0.0, stt_both_pct=0.0, stt_sell_pct=0.0,
    stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
    slippage_ticks=0.0, stop_slippage_ticks=0.0, reviewed=True,
)

FAVORABLE_PE = RegimeLabel(
    raw="uptrend", put=Regime.TREND_FAVORABLE, call=Regime.TREND_UNFAVORABLE
)


def _row(trade_date, expiry, strike, opt_type, close, oi=1000) -> MCXOptionRow:
    return MCXOptionRow(
        trade_date=trade_date, symbol="GOLDM", expiry=expiry, strike=strike,
        opt_type=opt_type, open=close, high=close, low=close, close=close,
        previous_close=close, open_interest=oi, volume=10, value=close * 10,
    )


def test_run_comparison_produces_one_row_per_variant_with_expected_metrics(tmp_path):
    d0, d1 = date(2026, 1, 5), date(2026, 2, 4)
    chain = [_row(d0, d1, strike=90.0, opt_type="PE", close=5.0)]
    bars = pd.DataFrame({"close": [100.0, 95.0]}, index=[d0, d1])  # stays OTM
    regime = {d0: FAVORABLE_PE}

    variant_flat = ("flat-1-lot", EngineConfig(
        lot_size=LOT_SIZE, tick_size=0.05, lots=1,
        option_cost_model=OPTION_COST_MODEL, premium_slippage_pct=0.0,
    ))
    variant_dynamic = ("dynamic-2-lot", EngineConfig(
        lot_size=LOT_SIZE, tick_size=0.05, lots=2,
        option_cost_model=OPTION_COST_MODEL, premium_slippage_pct=0.0,
    ))

    db_path = tmp_path / "results.sqlite3"
    rows = run_comparison(
        [variant_flat, variant_dynamic], chain, bars, regime,
        initial_capital=1_000.0, db_path=db_path,
    )

    assert len(rows) == 2
    assert [r.label for r in rows] == ["flat-1-lot", "dynamic-2-lot"]

    credit_1_lot = 5.0 * LOT_SIZE
    expected_1_lot = credit_1_lot - credit_1_lot * 0.01
    credit_2_lot = 5.0 * LOT_SIZE * 2
    expected_2_lot = credit_2_lot - credit_2_lot * 0.01

    assert rows[0].total_pnl == pytest.approx(expected_1_lot)
    assert rows[0].cycles == 1
    assert rows[1].total_pnl == pytest.approx(expected_2_lot)
    assert rows[1].cycles == 1

    assert rows[0].summary["final_equity"] == pytest.approx(1_000.0 + expected_1_lot)
    assert rows[1].summary["final_equity"] == pytest.approx(1_000.0 + expected_2_lot)

    # each variant's run was persisted under its own label
    assert sorted(results_store.list_runs(db_path=db_path)) == [
        "dynamic-2-lot", "flat-1-lot",
    ]
    loaded_flat = results_store.load_run("flat-1-lot", db_path=db_path)
    assert loaded_flat["total_pnl"] == pytest.approx(expected_1_lot)
    assert loaded_flat["config"] == variant_flat[1]
    loaded_dynamic = results_store.load_run("dynamic-2-lot", db_path=db_path)
    assert loaded_dynamic["total_pnl"] == pytest.approx(expected_2_lot)
    assert loaded_dynamic["config"] == variant_dynamic[1]


def test_run_comparison_with_no_variants_returns_empty_list(tmp_path):
    d0, d1 = date(2026, 1, 5), date(2026, 2, 4)
    bars = pd.DataFrame({"close": [100.0, 95.0]}, index=[d0, d1])
    db_path = tmp_path / "results.sqlite3"
    rows = run_comparison([], [], bars, {}, db_path=db_path)
    assert rows == []
    assert results_store.list_runs(db_path=db_path) == []
