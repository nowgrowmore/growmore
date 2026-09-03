"""Known-input/known-output test for the AlwaysFlip demo strategy.

Not a real trading strategy -- exists purely so the paper-trading pipeline
(fills, P&L calc, position open/close, logging) can be exercised end-to-end
on demand, without waiting for real market conditions to happen to satisfy a
real strategy's entry/exit rules. BUY whenever there's no open position,
SELL whenever there is one -- deterministic regardless of price.
"""
from __future__ import annotations

from types import SimpleNamespace

from growmore_bot.strategies.always_flip import AlwaysFlipStrategy
from growmore_bot.strategies.base import SignalAction


def test_buys_when_no_position_open():
    strategy = AlwaysFlipStrategy()
    signal = strategy.on_bar(SimpleNamespace(close=100), position_state=None)
    assert signal.action == SignalAction.BUY


def test_sells_when_position_open():
    strategy = AlwaysFlipStrategy()
    position_state = {"quantity": 1, "avg_entry_price": 100.0}
    signal = strategy.on_bar(SimpleNamespace(close=105), position_state=position_state)
    assert signal.action == SignalAction.SELL


def test_alternates_across_calls_following_position_state():
    strategy = AlwaysFlipStrategy()
    actions = []
    position_state = None
    for close in [100, 101, 99, 102]:
        signal = strategy.on_bar(SimpleNamespace(close=close), position_state=position_state)
        actions.append(signal.action)
        # Mimic the engine flipping position_state based on the fill.
        position_state = None if position_state else {"quantity": 1, "avg_entry_price": close}
    assert actions == [
        SignalAction.BUY,
        SignalAction.SELL,
        SignalAction.BUY,
        SignalAction.SELL,
    ]


def test_debug_state_exposes_last_close():
    strategy = AlwaysFlipStrategy()
    assert strategy.debug_state() == {"last_close": None}
    strategy.on_bar(SimpleNamespace(close=123.45), position_state=None)
    assert strategy.debug_state() == {"last_close": 123.45}
