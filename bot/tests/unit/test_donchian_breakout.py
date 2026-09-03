"""Known-input/known-output test for the Donchian channel breakout strategy.

N=3. Channel at bar i is the high/low over the N bars strictly BEFORE i (no
lookahead, and the current bar is excluded from its own channel).

highs = lows = closes = [10, 11, 9, 8, 13, 7, 6, 20]

idx0,1,2: fewer than N prior bars -> HOLD
idx3: prior bars [10,11,9] -> high=11, low=9.  close=8  < 9  -> SELL
idx4: prior bars [11,9,8]  -> high=11, low=8.  close=13 > 11 -> BUY
idx5: prior bars [9,8,13]  -> high=13, low=8.  close=7  < 8  -> SELL
idx6: prior bars [8,13,7]  -> high=13, low=7.  close=6  < 7  -> SELL
idx7: prior bars [13,7,6]  -> high=13, low=6.  close=20 > 13 -> BUY
"""
from __future__ import annotations

from types import SimpleNamespace

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.donchian_breakout import DonchianBreakoutStrategy

VALUES = [10, 11, 9, 8, 13, 7, 6, 20]
EXPECTED = [
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.SELL,
    SignalAction.BUY,
    SignalAction.SELL,
    SignalAction.SELL,
    SignalAction.BUY,
]


def test_donchian_breakout_known_sequence():
    strategy = DonchianBreakoutStrategy(period=3)
    actual = []
    for v in VALUES:
        bar = SimpleNamespace(high=v, low=v, close=v)
        signal = strategy.on_bar(bar, position_state=None)
        actual.append(signal.action)

    assert actual == EXPECTED


def test_donchian_breakout_requires_positive_period():
    import pytest

    with pytest.raises(ValueError):
        DonchianBreakoutStrategy(period=0)


def test_donchian_breakout_debug_state_exposes_channel():
    strategy = DonchianBreakoutStrategy(period=2)
    assert strategy.debug_state() == {"channel_high": None, "channel_low": None}
    strategy.on_bar(SimpleNamespace(close=10, high=12, low=8), position_state=None)
    strategy.on_bar(SimpleNamespace(close=10, high=14, low=6), position_state=None)
    strategy.on_bar(SimpleNamespace(close=10, high=10, low=10), position_state=None)
    state = strategy.debug_state()
    assert state["channel_high"] == 14
    assert state["channel_low"] == 6
