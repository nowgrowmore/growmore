"""Tests for growmore_bot.risk.wrapper.RiskManagedStrategy.

The wrapper's whole job is to add stops to an unmodified inner strategy, so
the tests here are mostly about PRECEDENCE and STATE OWNERSHIP rather than
arithmetic (that's covered in test_risk_exits.py).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.risk.wrapper import RiskManagedStrategy
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


def _bar(high, low, close):
    return SimpleNamespace(high=high, low=low, close=close)


class _Scripted(Strategy):
    """Inner strategy that emits a fixed sequence, so the wrapper's own
    decisions are the only variable."""

    def __init__(self, actions):
        self._actions = list(actions)
        self.seen_position_states = []

    def on_bar(self, bar, position_state):
        self.seen_position_states.append(position_state)
        action = self._actions.pop(0) if self._actions else SignalAction.HOLD
        return Signal(action=action)

    def debug_state(self):
        return {"inner_marker": 1.0}


def _warm(strategy, n=20, price=100.0):
    """Feed flat bars so ATR becomes computable (ATR(14) needs 14)."""
    for _ in range(n):
        strategy.on_bar(_bar(price + 5, price - 5, price), None)


def _long(qty=1.0, entry=100.0, risk=None):
    return {"quantity": qty, "avg_entry_price": entry, "risk": risk or {}}


def test_a_buy_while_flat_gets_an_initial_stop_two_atrs_below():
    inner = _Scripted([])
    s = RiskManagedStrategy(inner, atr_period=14, initial_stop_atr=2.0, trail_atr=None)
    _warm(s)                       # flat bars of range 10 -> ATR converges to 10
    inner._actions = [SignalAction.BUY]
    signal = s.on_bar(_bar(105, 95, 100), None)

    assert signal.action == SignalAction.BUY
    assert signal.stop_price == pytest.approx(100.0 - 2.0 * 10.0)
    assert signal.risk_state["direction"] == 1
    assert signal.risk_state["bars_held"] == 0


def test_no_stop_is_placed_before_atr_is_computable():
    """Falling back to the entry price would exit on the first adverse tick."""
    inner = _Scripted([SignalAction.BUY])
    s = RiskManagedStrategy(inner, atr_period=14)
    signal = s.on_bar(_bar(105, 95, 100), None)
    assert signal.action == SignalAction.BUY
    assert signal.stop_price is None


def test_a_breached_stop_exits_and_overrides_the_inner_strategy_saying_hold():
    """A protective stop that loses to the inner strategy's opinion isn't a
    protective stop."""
    inner = _Scripted([])
    s = RiskManagedStrategy(inner, atr_period=14, initial_stop_atr=2.0, trail_atr=None)
    _warm(s)
    # Stop is at 80 (100 - 2*10); this bar's low pierces it.
    signal = s.on_bar(_bar(101, 75, 78), _long(risk={"stop_price": 80.0, "high_water": 100.0,
                                                    "bars_held": 1, "direction": 1}))
    assert signal.action == SignalAction.SELL
    assert signal.exit_reason in ("stop", "trail")


def test_the_trailing_stop_ratchets_up_and_never_loosens():
    """Asserted as a property rather than an exact level: a wide bar also
    widens ATR, so pinning a hand-computed number here would be testing the
    ATR recurrence twice rather than the ratchet."""
    inner = _Scripted([])
    s = RiskManagedStrategy(inner, atr_period=14, initial_stop_atr=2.0, trail_atr=3.0)
    _warm(s)

    # Price makes a new high -> the chandelier pulls the stop up from 80.
    first = s.on_bar(_bar(130, 118, 125), _long(risk={"stop_price": 80.0, "high_water": 100.0,
                                                     "bars_held": 1, "direction": 1}))
    raised = first.risk_state["stop_price"]
    assert raised > 80.0
    assert first.risk_state["high_water"] == pytest.approx(130.0)

    # Price pulls back. The chandelier now computes LOWER, so the stop must
    # hold where it is rather than loosening back toward 80.
    second = s.on_bar(_bar(120, 112, 115), _long(risk=first.risk_state))
    assert second.risk_state["stop_price"] == pytest.approx(raised)
    assert second.risk_state["high_water"] == pytest.approx(130.0)


def test_the_time_stop_fires_regardless_of_price():
    inner = _Scripted([])
    s = RiskManagedStrategy(inner, atr_period=14, trail_atr=None, max_bars_held=3)
    _warm(s)
    signal = s.on_bar(_bar(105, 95, 100), _long(risk={"stop_price": 1.0, "high_water": 100.0,
                                                     "bars_held": 2, "direction": 1}))
    assert signal.action == SignalAction.SELL
    assert signal.exit_reason == "time"


def test_the_inner_strategy_still_decides_when_no_protective_exit_fires():
    inner = _Scripted([])
    s = RiskManagedStrategy(inner, atr_period=14, trail_atr=None)
    _warm(s)                       # warm-up would otherwise eat the scripted action
    inner._actions = [SignalAction.SELL]
    signal = s.on_bar(_bar(105, 95, 100), _long(risk={"stop_price": 1.0, "high_water": 100.0,
                                                     "bars_held": 1, "direction": 1}))
    assert signal.action == SignalAction.SELL
    assert signal.exit_reason == "signal"


def test_the_wrapper_holds_no_position_state_of_its_own():
    """The scheduler rebuilds strategies every tick and warms them up with
    position_state=None, so any position-derived state kept on the instance
    would be garbage by the time the live quote arrives. All per-trade risk
    state must travel on the Signal instead."""
    s = RiskManagedStrategy(_Scripted([]), atr_period=14)
    _warm(s)
    attrs = {k: v for k, v in vars(s).items() if not k.startswith("_")}
    assert "stop_price" not in attrs and "high_water" not in attrs and "bars_held" not in attrs


def test_debug_state_merges_the_inner_strategys_indicators_and_adds_atr():
    s = RiskManagedStrategy(_Scripted([]), atr_period=14)
    _warm(s)
    state = s.debug_state()
    assert state["inner_marker"] == 1.0
    assert state["atr"] == pytest.approx(10.0)


def test_snapshot_round_trips_through_the_inner_strategy():
    from growmore_bot.strategies.sma_crossover import SmaCrossoverStrategy

    inner = SmaCrossoverStrategy(fast_period=2, slow_period=3)
    s = RiskManagedStrategy(inner, atr_period=14)
    for close in (10, 11, 12):
        s.on_bar(_bar(close + 1, close - 1, close), None)
    snap = s.get_state_snapshot()
    assert "inner" in snap

    restored = RiskManagedStrategy(SmaCrossoverStrategy(fast_period=2, slow_period=3), atr_period=14)
    restored.load_state_snapshot(snap)
    assert restored.inner._prev_fast_above_slow == inner._prev_fast_above_slow


def test_requires_intraday_flatten_is_inherited_from_the_inner_strategy():
    from growmore_bot.strategies.vwap_session_bounce import VwapSessionBounceStrategy

    assert RiskManagedStrategy(VwapSessionBounceStrategy()).requires_intraday_flatten is True
    assert RiskManagedStrategy(_Scripted([])).requires_intraday_flatten is False
