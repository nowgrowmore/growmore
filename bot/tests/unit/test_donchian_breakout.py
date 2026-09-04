"""Known-input/known-output test for the Donchian channel breakout strategy.

N=3. Channel at bar i is the high/low over the N bars strictly BEFORE i (no
lookahead, and the current bar is excluded from its own channel). Like every
other signalling strategy here, a signal only fires on the bar where the
close FIRST breaks outside the channel -- staying outside on a later bar is
HOLD, not a repeated signal (see the strategy's own docstring for the real
live bug this was fixed to prevent).

highs = lows = closes = [10, 11, 9, 8, 13, 7, 6, 20]

idx0,1,2: fewer than N prior bars -> HOLD
idx3: prior bars [10,11,9] -> high=11, low=9.  close=8  < 9  -> below, prev=None  -> SELL (new)
idx4: prior bars [11,9,8]  -> high=11, low=8.  close=13 > 11 -> above, prev=below -> BUY (new)
idx5: prior bars [9,8,13]  -> high=13, low=8.  close=7  < 8  -> below, prev=above -> SELL (new)
idx6: prior bars [8,13,7]  -> high=13, low=7.  close=6  < 7  -> below, prev=below -> HOLD (still below, no new cross)
idx7: prior bars [13,7,6]  -> high=13, low=6.  close=20 > 13 -> above, prev=below -> BUY (new)
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
    SignalAction.HOLD,
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


def test_donchian_breakout_does_not_refire_while_price_stays_broken_out():
    # Regression: found via independent code review 2026-09-04 -- with no
    # crossing state, a fresh strategy instance (rebuilt every scheduler
    # tick) would re-signal BUY on every single tick price stayed above the
    # channel, which either silently pyramids a position (loose
    # max_position_size) or gets rejected in a way that skips
    # mark-to-market, freezing unrealized P&L for the rest of the day.
    strategy = DonchianBreakoutStrategy(period=3)
    for v in [10, 11, 9]:  # seed the 3-bar channel (high=11, low=9)
        strategy.on_bar(SimpleNamespace(high=v, low=v, close=v), position_state=None)

    first = strategy.on_bar(SimpleNamespace(high=20, low=20, close=20), position_state=None)
    assert first.action == SignalAction.BUY

    # Simulates the scheduler feeding a fresh warmed-up instance (same
    # history) the SAME live quote again next tick, with the crossing
    # reference restored from the last live tick.
    snapshot = strategy.get_state_snapshot()
    fresh = DonchianBreakoutStrategy(period=3)
    for v in [10, 11, 9]:
        fresh.on_bar(SimpleNamespace(high=v, low=v, close=v), position_state=None)
    fresh.load_state_snapshot(snapshot)
    second = fresh.on_bar(SimpleNamespace(high=20, low=20, close=20), position_state=None)
    assert second.action == SignalAction.HOLD


def test_donchian_breakout_state_snapshot_round_trips_the_breakout_state():
    strategy = DonchianBreakoutStrategy(period=3)
    assert strategy.get_state_snapshot() == {}
    for v in VALUES[:4]:  # through the known SELL at idx3
        strategy.on_bar(SimpleNamespace(high=v, low=v, close=v), position_state=None)
    snapshot = strategy.get_state_snapshot()
    assert snapshot == {"prev_breakout_state": "below"}

    fresh = DonchianBreakoutStrategy(period=3)
    fresh.load_state_snapshot(snapshot)
    assert fresh._prev_breakout_state == "below"


def test_donchian_breakout_load_state_snapshot_ignores_unknown_keys_and_empty_dict():
    strategy = DonchianBreakoutStrategy(period=3)
    strategy.load_state_snapshot({})  # must not raise
    strategy.load_state_snapshot({"unrelated": 1})  # must not raise
