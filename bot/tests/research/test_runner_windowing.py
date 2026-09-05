"""Tests for research.dailydata.runner's walk-forward windowing.

`evaluate_from` is the only part of the runner with logic worth breaking: it
decides which bars count toward a score while still letting the strategy warm
up on the bars before them.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from research.dailydata import cache
from research.dailydata.runner import risk_managed, run_variant

META = {"TESTSYM": {"lot_size": 1, "tick_size": 1.0, "security_id": "x"}}


def _ramp(n, start=100.0, step=1.0):
    """A clean uptrend -- enough for a MACD to take a position and hold it."""
    bars = []
    for i in range(n):
        c = start + i * step
        bars.append(
            cache.CachedBar(
                timestamp=datetime(2021, 1, 1) + timedelta(days=i),
                open=c, high=c + 1, low=c - 1, close=c, volume=1.0,
            )
        )
    return bars


def test_evaluate_from_scores_only_the_tail_of_the_equity_curve():
    bars = _ramp(200)
    params = risk_managed("macd_trend", {"fast_period": 5, "slow_period": 13,
                                         "signal_period": 5}, 2.0, 3.0)
    full = run_variant("TESTSYM", "risk_managed", params, "f", bars=bars, meta=META)
    tail = run_variant("TESTSYM", "risk_managed", params, "t", bars=bars, meta=META,
                       evaluate_from=150)

    assert len(full.equity_curve) == 200
    assert len(tail.equity_curve) == 50
    assert tail.equity_curve == full.equity_curve[150:]
    # Both runs end in the same place -- windowing scores a slice, it does not
    # change what was traded.
    assert tail.final_equity == pytest.approx(full.final_equity)


def test_the_scored_windows_capital_base_is_the_equity_carried_in():
    """Otherwise a fold that inherits a doubled account reports its CAGR
    against the original stake and looks twice as good as it was."""
    bars = _ramp(200)
    params = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
    full = run_variant("TESTSYM", "macd_trend", params, "f", bars=bars, meta=META)
    tail = run_variant("TESTSYM", "macd_trend", params, "t", bars=bars, meta=META,
                       evaluate_from=150)

    assert tail.initial_capital == pytest.approx(full.equity_curve[150])
    assert tail.initial_capital != pytest.approx(full.initial_capital)


def test_a_trade_entered_before_the_window_is_not_scored_in_it():
    """A position carried across the boundary belongs to the warm-up window."""
    bars = _ramp(200)
    params = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
    full = run_variant("TESTSYM", "macd_trend", params, "f", bars=bars, meta=META)
    tail = run_variant("TESTSYM", "macd_trend", params, "t", bars=bars, meta=META,
                       evaluate_from=150)

    assert tail.trades <= full.trades


def test_evaluate_from_zero_is_the_whole_series():
    bars = _ramp(120)
    params = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
    a = run_variant("TESTSYM", "macd_trend", params, "a", bars=bars, meta=META)
    b = run_variant("TESTSYM", "macd_trend", params, "b", bars=bars, meta=META,
                    evaluate_from=0)
    assert a.equity_curve == b.equity_curve
    assert a.trades == b.trades


def test_an_empty_bar_list_is_rejected_rather_than_scored_as_zero():
    with pytest.raises(ValueError, match="No bars"):
        run_variant("TESTSYM", "macd_trend", {"fast_period": 5, "slow_period": 13,
                                              "signal_period": 5}, "x", bars=[], meta=META)
