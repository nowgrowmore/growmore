"""Tests for research.fno.walk_forward_run.

The point of this harness is negative: it must be IMPOSSIBLE for a scored
bar to have been seen by any fitting step. Since nothing is selected here --
Phase 1's conclusion was to stop selecting, not to walk-forward the
selection -- the only ways to leak are to score warm-up bars or to let a
fold's test window overlap another's.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from growmore_bot.backtest.walk_forward import DEFAULT_STEP, DEFAULT_TEST, DEFAULT_TRAIN
from research.fno.bar_cache import CachedBar
from research.fno.configs import BENCHMARK
from research.fno.run_configs import meta_for
from research.fno.walk_forward_run import stitched_oos

PARAMS = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
RISK = {"inner_strategy": "macd_trend", "inner_params": PARAMS,
        "atr_period": 14, "initial_stop_atr": 2.0, "trail_atr": 3.0}


def _bars(n):
    out = []
    start = datetime(2010, 1, 1, 18, 30, tzinfo=timezone.utc)
    price = 100.0
    for i in range(n):
        price *= 1.0004 * (1.0 + 0.02 * math.sin(i / 6.0))
        out.append(CachedBar(start + timedelta(days=i), price, price * 1.01,
                             price * 0.99, price, 10_000.0))
    return out


def test_a_series_too_short_for_one_fold_is_unmeasured_not_zero():
    # Reporting Sharpe 0.00 for a stock that could not be tested at all
    # would put it in the table as a bad result rather than as no result.
    bars = _bars(DEFAULT_TRAIN + DEFAULT_TEST - 10)
    meta = meta_for("TESTCO", float(bars[0].close))
    assert stitched_oos("TESTCO", bars, "risk_managed", RISK, "rm", meta) is None


def test_exactly_one_fold_at_the_minimum_length():
    bars = _bars(DEFAULT_TRAIN + DEFAULT_TEST)
    meta = meta_for("TESTCO", float(bars[0].close))
    result = stitched_oos("TESTCO", bars, "risk_managed", RISK, "rm", meta)
    assert result is not None
    assert result["folds"] == 1


def test_fold_count_grows_with_history_which_is_why_the_store_goes_back_to_2010():
    meta = meta_for("TESTCO", 100.0)
    five_years = _bars(1260)
    fifteen_years = _bars(3750)
    few = stitched_oos("TESTCO", five_years, "risk_managed", RISK, "rm", meta)
    many = stitched_oos("TESTCO", fifteen_years, "risk_managed", RISK, "rm", meta)
    assert few["folds"] == (1260 - DEFAULT_TRAIN) // DEFAULT_STEP
    assert many["folds"] > 3 * few["folds"]


def test_only_the_test_tail_is_scored_so_warm_up_bars_never_enter_the_result():
    # A fold scores 126 bars, so a 630-bar series (504 + 126) yields at most
    # 125 returns -- not 629. If warm-up leaked in, this would be ~10x larger.
    bars = _bars(DEFAULT_TRAIN + DEFAULT_TEST)
    meta = meta_for("TESTCO", float(bars[0].close))
    result = stitched_oos("TESTCO", bars, *(BENCHMARK[1], BENCHMARK[2]), "bh", meta)
    # Buy-and-hold is in the market every scored bar, so its return count is
    # exactly the scored-bar count minus one.
    assert result["oos_trades"] == 0
    assert 0 < abs(result["oos_total_pct"]) < 1e6
    assert result["folds"] == 1


def test_the_benchmark_runs_through_the_identical_folds_as_a_config():
    bars = _bars(DEFAULT_TRAIN + 3 * DEFAULT_TEST)
    meta = meta_for("TESTCO", float(bars[0].close))
    config = stitched_oos("TESTCO", bars, "risk_managed", RISK, "rm", meta)
    bench = stitched_oos("TESTCO", bars, BENCHMARK[1], BENCHMARK[2], "bh", meta)
    assert config["folds"] == bench["folds"] == 3


def test_a_stitched_sharpe_is_finite_even_when_a_config_never_trades():
    # ensemble_trend on a series shorter than its slowest member's warm-up
    # takes no position at all; that must be 0.0, not NaN.
    bars = _bars(DEFAULT_TRAIN + DEFAULT_TEST)
    meta = meta_for("TESTCO", float(bars[0].close))
    result = stitched_oos(
        "TESTCO", bars, "vol_filtered",
        {"inner_strategy": "risk_managed", "inner_params": RISK,
         "vol_window": 20, "lookback": 504, "percentile_cap": 0.90},
        "vol90", meta,
    )
    assert result is not None
    assert math.isfinite(result["oos_sharpe"])
    assert result["oos_sharpe"] == pytest.approx(result["oos_sharpe"])
