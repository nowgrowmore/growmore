"""`run_variant` must be able to charge NSE equity costs and to open shorts,
without changing what it does for the MCX research that already uses it.

Both parameters are additive with MCX defaults, which is the Phase 0 control
discipline: a new capability must not move a single published number.
"""
from __future__ import annotations

import inspect
import math
from datetime import datetime, timedelta, timezone

import pytest

from growmore_bot.costs import DEFAULT_COST_MODEL, FREE_COST_MODEL, NSE_EQUITY_DELIVERY_COST_MODEL
from research.dailydata.runner import run_variant
from research.fno.bar_cache import CachedBar


def _trending_bars(n=400):
    """An oscillating series with a mid-point regime flip.

    A monotonic trend is the wrong fixture here: MACD crosses its signal
    line once and the position never closes, so `trades` stays 0 and the
    cost comparison has nothing to compare. The cycle makes real round
    trips; the second-half drift down gives a short-capable run something
    a long-only run cannot take.
    """
    bars = []
    start = datetime(2020, 1, 1, 18, 30, tzinfo=timezone.utc)
    price = 100.0
    for i in range(n):
        drift = 1.004 if i < n // 2 else 0.996
        price *= drift * (1.0 + 0.03 * math.sin(i / 4.0))
        bars.append(
            CachedBar(
                timestamp=start + timedelta(days=i),
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=10_000.0,
            )
        )
    return bars


META = {"TESTCO": {"lot_size": 100, "tick_size": 0.05}}
PARAMS = {"fast_period": 5, "slow_period": 13, "signal_period": 5}


def test_the_cost_model_defaults_to_mcx_so_existing_callers_are_unchanged():
    signature = inspect.signature(run_variant)
    assert signature.parameters["cost_model"].default is DEFAULT_COST_MODEL
    assert signature.parameters["allow_shorts"].default is False


def test_equity_costs_are_charged_and_exceed_mcx_costs_on_the_same_series():
    bars = _trending_bars()
    mcx = run_variant("TESTCO", "macd_trend", PARAMS, "mcx", bars=bars, meta=META)
    equity = run_variant(
        "TESTCO", "macd_trend", PARAMS, "eq", bars=bars, meta=META,
        cost_model=NSE_EQUITY_DELIVERY_COST_MODEL,
    )
    assert mcx.trades == equity.trades > 0
    # Same trades, same series -- the only difference is the tax schedule.
    assert equity.total_cost > mcx.total_cost


def test_turning_costs_off_still_wins_over_an_explicit_cost_model():
    # `with_costs=False` is how existing callers reproduce pre-cost numbers;
    # it must keep overriding whatever cost_model is passed.
    bars = _trending_bars()
    free = run_variant(
        "TESTCO", "macd_trend", PARAMS, "free", bars=bars, meta=META,
        with_costs=False, cost_model=NSE_EQUITY_DELIVERY_COST_MODEL,
    )
    assert free.total_cost == pytest.approx(0.0)
    assert FREE_COST_MODEL.stt_both_pct == 0.0


def test_allowing_shorts_changes_the_result_on_a_series_that_reverses():
    bars = _trending_bars()
    long_only = run_variant("TESTCO", "macd_trend", PARAMS, "long", bars=bars, meta=META)
    with_shorts = run_variant(
        "TESTCO", "macd_trend", PARAMS, "ls", bars=bars, meta=META, allow_shorts=True,
    )
    # The second half of the series falls, so a short-capable run must take
    # trades the long-only run cannot.
    assert with_shorts.trades > long_only.trades
