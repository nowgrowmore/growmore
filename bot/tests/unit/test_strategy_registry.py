"""Tests for growmore_bot.strategies.registry.

The map of strategy-name -> constructor previously lived inline in
scheduler/run.py. The walk-forward harness needs exactly the same map, and a
second hand-maintained copy would drift -- a strategy registered in one place
and not the other fails only at runtime, on whichever code path happens to
touch it. These tests pin the shared version.
"""
from __future__ import annotations

import pytest

from growmore_bot.strategies.base import Strategy
from growmore_bot.strategies.registry import STRATEGY_NAMES, build_strategy


def test_every_registered_name_builds_a_strategy():
    params = {
        "sma_crossover": {"fast_period": 5, "slow_period": 20},
        "donchian_breakout": {"period": 20},
        "rsi_mean_reversion": {"period": 14},
        "macd_trend": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
        "ensemble_trend": {"min_agreement": 3},
        "bollinger_reversion": {"period": 20, "num_std": 2.0},
        "regime_switch": {
            "ranging_strategy": "rsi",
            "macd_params": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
            "ranging_params": {"period": 14},
        },
        "vwap_session_bounce": {},
        "ema_trend": {"period": 112},
        "vol_filtered": {
            "inner_strategy": "macd_trend",
            "inner_params": {"fast_period": 5, "slow_period": 13, "signal_period": 5},
            "vol_window": 20,
            "percentile_cap": 0.9,
        },
        "always_flip": {},
        "risk_managed": {
            "inner_strategy": "macd_trend",
            "inner_params": {"fast_period": 5, "slow_period": 13, "signal_period": 5},
            "atr_period": 14,
            "initial_stop_atr": 2.0,
            "trail_atr": 3.0,
        },
    }
    assert set(params) == set(STRATEGY_NAMES), "registry and this test have drifted"
    for name in STRATEGY_NAMES:
        assert isinstance(build_strategy(name, params[name]), Strategy), name


def test_an_unknown_name_lists_what_is_available():
    with pytest.raises(KeyError, match="macd_trend"):
        build_strategy("no_such_strategy", {})


def test_the_params_dict_is_not_mutated():
    """build_risk_managed pops from its params; callers reuse their dicts
    across instruments, so a mutation would silently blank the second run."""
    params = {
        "inner_strategy": "macd_trend",
        "inner_params": {"fast_period": 5, "slow_period": 13, "signal_period": 5},
        "initial_stop_atr": 2.0,
    }
    build_strategy("risk_managed", params)
    assert params["inner_strategy"] == "macd_trend"
    assert params["inner_params"] == {"fast_period": 5, "slow_period": 13, "signal_period": 5}


def test_the_scheduler_uses_the_shared_registry_rather_than_its_own_copy():
    import inspect

    from growmore_bot.scheduler import run as scheduler_run

    source = inspect.getsource(scheduler_run)
    assert "build_strategy" in source, "scheduler must delegate to the registry"
