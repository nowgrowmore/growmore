"""Known-input/known-output test for the MACD trend strategy.

MACD line = EMA(fast) - EMA(slow); signal line = EMA(macd, signal_period).
Both EMAs seed as a plain SMA of their first `period` closes, then update via
the standard EMA recurrence value*k + prev*(1-k), k = 2/(period+1). The
signal line seeds the same way once `signal_period` macd values exist. Values
below (fast=2, slow=3, signal=2) are computed independently of the
implementation, following that same standard, unambiguous formula.

closes = [10, 12, 14, 11, 9, 16, 18, 15, 12, 20]

idx0 (10): only 1 close -> no fast EMA yet (needs 2)              -> HOLD
idx1 (12): fast EMA seeds = SMA(10,12) = 11.0; slow needs 3 closes -> HOLD
idx2 (14): fast EMA updates: 14*(2/3)+11.0*(1/3) = 13.0
           slow EMA seeds = SMA(10,12,14) = 12.0
           macd = 13.0-12.0 = 1.0 (1st macd value, signal needs 2)  -> HOLD
idx3 (11): fast = 11*(2/3)+13.0*(1/3) = 11.667; slow = 11*.5+12.0*.5 = 11.5
           macd = 0.167; signal seeds = SMA(1.0, 0.167) = 0.5835
           macd(0.167) < signal(0.5835), no prior relation yet     -> HOLD
idx4 (9):  fast = 9*(2/3)+11.667*(1/3) = 9.889; slow = 9*.5+11.5*.5 = 10.25
           macd = -0.361; signal = -0.361*(2/3)+0.5835*(1/3) ~= -0.046
           macd < signal, same as prev (both below)                -> HOLD
idx5 (16): fast = 16*(2/3)+9.889*(1/3) = 13.963; slow = 16*.5+10.25*.5 = 13.125
           macd = 0.838; signal = 0.838*(2/3)+(-0.046)*(1/3) ~= 0.543
           macd(0.838) > signal(0.543) -- was below, now above      -> BUY
idx6 (18): fast = 18*(2/3)+13.963*(1/3) = 16.654; slow = 18*.5+13.125*.5 = 15.5625
           macd = 1.0915; signal ~= 1.0915*(2/3)+0.543*(1/3) ~= 0.909
           macd still above signal, no new cross                    -> HOLD
idx7 (15): fast = 15*(2/3)+16.654*(1/3) = 15.551; slow = 15*.5+15.5625*.5 = 15.281
           macd = 0.270; signal ~= 0.270*(2/3)+0.909*(1/3) ~= 0.483
           macd(0.270) < signal(0.483) -- was above, now below       -> SELL
idx8 (12): fast = 12*(2/3)+15.551*(1/3) = 13.184; slow = 12*.5+15.281*.5 = 13.6405
           macd = -0.4565; signal ~= -0.4565*(2/3)+0.483*(1/3) ~= -0.144
           macd still below signal, no new cross                     -> HOLD
idx9 (20): fast = 20*(2/3)+13.184*(1/3) = 17.728; slow = 20*.5+13.6405*.5 = 16.820
           macd = 0.908; signal ~= 0.908*(2/3)+(-0.144)*(1/3) ~= 0.558
           macd(0.908) > signal(0.558) -- was below, now above       -> BUY
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.macd_trend import MacdTrendStrategy

CLOSES = [10, 12, 14, 11, 9, 16, 18, 15, 12, 20]
EXPECTED = [
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.BUY,
    SignalAction.HOLD,
    SignalAction.SELL,
    SignalAction.HOLD,
    SignalAction.BUY,
]


def test_macd_trend_known_sequence():
    strategy = MacdTrendStrategy(fast_period=2, slow_period=3, signal_period=2)
    actual = []
    for close in CLOSES:
        bar = SimpleNamespace(close=close)
        signal = strategy.on_bar(bar, position_state=None)
        actual.append(signal.action)

    assert actual == EXPECTED


def test_macd_trend_requires_fast_less_than_slow():
    with pytest.raises(ValueError):
        MacdTrendStrategy(fast_period=26, slow_period=12, signal_period=9)


def test_macd_trend_requires_positive_signal_period():
    with pytest.raises(ValueError):
        MacdTrendStrategy(fast_period=12, slow_period=26, signal_period=0)


def test_macd_trend_debug_state_exposes_macd_and_signal():
    strategy = MacdTrendStrategy(fast_period=2, slow_period=3, signal_period=2)
    assert strategy.debug_state() == {"macd": None, "signal": None, "fast_ema": None, "slow_ema": None}
    for close in CLOSES[:4]:  # enough for both macd and signal to be computable
        strategy.on_bar(SimpleNamespace(close=close), position_state=None)
    state = strategy.debug_state()
    assert state["macd"] is not None
    assert state["signal"] is not None
    # Regression: exposing the raw EMAs (not just the derived macd/signal
    # gap) lets a caller solve "how much would price need to move for MACD
    # to cross its signal line" exactly -- the gap alone isn't enough,
    # since the two EMAs' sensitivity to a new price differs.
    assert state["fast_ema"] == pytest.approx(strategy._fast_ema)
    assert state["slow_ema"] == pytest.approx(strategy._slow_ema)


def test_macd_trend_state_snapshot_round_trips_the_crossing_reference():
    # Regression: found live 2026-09-04 -- the scheduler rebuilds a fresh
    # strategy every tick and warms it up from history ending yesterday, so
    # without restoring the crossing reference from the last LIVE tick, a
    # signal that should fire once re-fires every tick for the rest of the
    # day the live value stays past the threshold (compared against
    # yesterday's fixed baseline every time).
    strategy = MacdTrendStrategy(fast_period=2, slow_period=3, signal_period=2)
    for close in CLOSES[:6]:  # through the known BUY at idx5
        strategy.on_bar(SimpleNamespace(close=close), position_state=None)
    snapshot = strategy.get_state_snapshot()
    assert snapshot == {"prev_macd_above_signal": True}

    # A fresh instance (as the scheduler builds every tick) with no snapshot
    # loaded has no crossing reference -- first computable point is HOLD.
    fresh = MacdTrendStrategy(fast_period=2, slow_period=3, signal_period=2)
    for close in CLOSES[:5]:
        fresh.on_bar(SimpleNamespace(close=close), position_state=None)
    # macd/signal already both above their prior relation at idx4 (still
    # below) -- load the restored snapshot before the next live bar so the
    # comparison is against the LAST LIVE TICK's state, not a fresh None.
    fresh.load_state_snapshot(snapshot)
    signal = fresh.on_bar(SimpleNamespace(close=CLOSES[5]), position_state=None)
    # Already "above" per the restored snapshot, and idx5 is also above --
    # correctly HOLD, not a repeated BUY.
    assert signal.action == SignalAction.HOLD


def test_macd_trend_load_state_snapshot_ignores_unknown_keys_and_empty_dict():
    strategy = MacdTrendStrategy(fast_period=2, slow_period=3, signal_period=2)
    strategy.load_state_snapshot({})  # must not raise
    strategy.load_state_snapshot({"unrelated_key": 123})  # must not raise
