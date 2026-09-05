"""Tests for VolFilteredStrategy -- binary volatility admission.

Two properties carry the whole design: an EXIT is never blocked (a filter
that can trap you during a vol spike is the opposite of a risk control), and
the threshold is built from bars strictly BEFORE the one being judged.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.risk.vol_filter import VolFilteredStrategy, build_vol_filtered
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class _Always(Strategy):
    def __init__(self, action=SignalAction.BUY):
        self.action = action

    def on_bar(self, bar, position_state):
        return Signal(action=self.action)

    def debug_state(self):
        return {"inner_marker": 1.0}


def _bar(close):
    return SimpleNamespace(open=close, high=close * 1.01, low=close * 0.99, close=close)


def _calm_then_wild(calm_n=200, wild_n=40):
    """Low-vol drift, then a violent stretch -- exactly the regime split the
    filter exists to notice."""
    closes, price = [], 100.0
    for i in range(calm_n):
        price *= 1.0005 if i % 2 else 0.9997
        closes.append(price)
    for i in range(wild_n):
        price *= 1.06 if i % 2 else 0.945
        closes.append(price)
    return closes


def test_entries_are_suppressed_once_volatility_reaches_the_top_decile():
    s = VolFilteredStrategy(_Always(), vol_window=20, lookback=504, percentile_cap=0.90)
    for c in _calm_then_wild():
        s.on_bar(_bar(c), None)
    assert s.vetoed > 0, "a violent stretch after a calm one must trip the filter"


def test_a_calm_series_is_never_filtered():
    """A cap at the 90th percentile of the instrument's OWN history should
    barely bind when nothing unusual happens."""
    s = VolFilteredStrategy(_Always(), vol_window=20, lookback=504, percentile_cap=0.90)
    for i in range(300):
        s.on_bar(_bar(100 * 1.0005 ** i), None)
    assert s.vetoed <= s.allowed * 0.2


def test_an_exit_is_never_suppressed_however_wild_the_market():
    s = VolFilteredStrategy(_Always(SignalAction.SELL), vol_window=20, percentile_cap=0.01)
    actions = [s.on_bar(_bar(c), None).action for c in _calm_then_wild()]
    assert all(a == SignalAction.SELL for a in actions)
    assert s.vetoed == 0


def test_todays_volatility_does_not_set_the_threshold_it_must_clear():
    """Appending before comparing would let a spike bar authorise itself,
    because it would sit at the top of its own distribution."""
    s = VolFilteredStrategy(_Always(), vol_window=20, lookback=504, percentile_cap=0.90)
    for c in _calm_then_wild(calm_n=200, wild_n=0):
        s.on_bar(_bar(c), None)
    before = len(s._history)
    s.on_bar(_bar(1000.0), None)         # an enormous single-bar move
    assert len(s._history) == before + 1
    assert s.vetoed >= 1, "the spike bar must be judged against the CALM history"


def test_no_threshold_exists_until_there_is_enough_history():
    s = VolFilteredStrategy(_Always(), vol_window=20, lookback=504)
    for i in range(25):
        s.on_bar(_bar(100 + i), None)
    assert s._threshold() is None
    assert s.vetoed == 0, "an unformed threshold must admit, not reject"


def test_percentile_cap_of_one_admits_everything():
    s = VolFilteredStrategy(_Always(), vol_window=20, percentile_cap=1.0)
    for c in _calm_then_wild():
        s.on_bar(_bar(c), None)
    assert s.vetoed == 0


def test_debug_state_merges_the_inner_and_exposes_the_vol_and_threshold():
    s = VolFilteredStrategy(_Always(), vol_window=20)
    for c in _calm_then_wild(calm_n=100, wild_n=0):
        s.on_bar(_bar(c), None)
    state = s.debug_state()
    assert state["inner_marker"] == 1.0
    assert state["realized_vol"] is not None
    assert "vol_threshold" in state


def test_bad_configuration_is_rejected():
    with pytest.raises(ValueError):
        VolFilteredStrategy(_Always(), percentile_cap=0.0)
    with pytest.raises(ValueError):
        VolFilteredStrategy(_Always(), percentile_cap=1.5)
    with pytest.raises(ValueError):
        VolFilteredStrategy(_Always(), vol_window=20, lookback=10)


def test_it_composes_around_a_risk_managed_strategy():
    """Wrapping the risk-managed strategy (rather than the reverse) keeps the
    stop logic seeing every bar while only the decision to OPEN is gated."""
    s = build_vol_filtered({
        "inner_strategy": "risk_managed",
        "inner_params": {
            "inner_strategy": "macd_trend",
            "inner_params": {"fast_period": 5, "slow_period": 13, "signal_period": 5},
            "initial_stop_atr": 2.0, "trail_atr": 3.0,
        },
        "vol_window": 20, "percentile_cap": 0.9,
    })
    for c in _calm_then_wild():
        s.on_bar(_bar(c), None)
    assert s.debug_state()["atr"] is not None


def test_build_requires_an_inner_strategy():
    with pytest.raises(ValueError, match="inner_strategy"):
        build_vol_filtered({})
