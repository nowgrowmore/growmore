"""Tests for growmore_bot.strategies.base -- the Strategy interface and Signal type."""
from __future__ import annotations

import pytest


def test_signal_action_enum_has_buy_sell_hold():
    from growmore_bot.strategies.base import SignalAction

    assert SignalAction.BUY.value == "BUY"
    assert SignalAction.SELL.value == "SELL"
    assert SignalAction.HOLD.value == "HOLD"


def test_signal_defaults_to_hold_with_no_size():
    from growmore_bot.strategies.base import Signal, SignalAction

    signal = Signal(action=SignalAction.HOLD)
    assert signal.action == SignalAction.HOLD
    assert signal.size is None


def test_signal_can_carry_a_size():
    from growmore_bot.strategies.base import Signal, SignalAction

    signal = Signal(action=SignalAction.BUY, size=2)
    assert signal.size == 2


def test_strategy_is_abstract_and_requires_on_bar():
    from growmore_bot.strategies.base import Strategy

    with pytest.raises(TypeError):
        Strategy()  # type: ignore[abstract]


def test_concrete_strategy_subclass_can_implement_on_bar():
    from growmore_bot.strategies.base import Signal, SignalAction, Strategy

    class AlwaysHold(Strategy):
        def on_bar(self, bar, position_state):
            return Signal(action=SignalAction.HOLD)

    strat = AlwaysHold()
    result = strat.on_bar(bar=None, position_state=None)
    assert result.action == SignalAction.HOLD
