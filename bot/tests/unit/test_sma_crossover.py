"""Known-input/known-output test for the SMA crossover strategy.

Signals are hand-computed below (fast=2, slow=3 period SMAs of closing
price), not derived from the implementation, so this genuinely pins expected
behaviour rather than just re-asserting whatever the code happens to do.

closes = [10, 11, 12, 9, 8, 7, 15, 16, 17]

idx0: only 1 close  -> not enough history for slow(3)      -> HOLD
idx1: 2 closes       -> not enough history for slow(3)      -> HOLD
idx2: fast=SMA2(11,12)=11.5  slow=SMA3(10,11,12)=11.0 (fast>slow, first
      comparable point, no prior relation to cross from)    -> HOLD
idx3: fast=SMA2(12,9)=10.5   slow=SMA3(11,12,9)=10.667 (fast<slow; was
      fast>slow -> bearish cross)                            -> SELL
idx4: fast=SMA2(9,8)=8.5     slow=SMA3(12,9,8)=9.667  (still fast<slow) -> HOLD
idx5: fast=SMA2(8,7)=7.5     slow=SMA3(9,8,7)=8.0     (still fast<slow) -> HOLD
idx6: fast=SMA2(7,15)=11.0   slow=SMA3(8,7,15)=10.0   (fast>slow; was
      fast<slow -> bullish cross)                             -> BUY
idx7: fast=SMA2(15,16)=15.5  slow=SMA3(7,15,16)=12.667 (still fast>slow) -> HOLD
idx8: fast=SMA2(16,17)=16.5  slow=SMA3(15,16,17)=16.0  (still fast>slow) -> HOLD
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.sma_crossover import SmaCrossoverStrategy

CLOSES = [10, 11, 12, 9, 8, 7, 15, 16, 17]
EXPECTED = [
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.SELL,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.BUY,
    SignalAction.HOLD,
    SignalAction.HOLD,
]


def test_sma_crossover_known_sequence():
    strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)
    actual = []
    for close in CLOSES:
        bar = SimpleNamespace(close=close)
        signal = strategy.on_bar(bar, position_state=None)
        actual.append(signal.action)

    assert actual == EXPECTED


def test_sma_crossover_requires_fast_less_than_slow():
    import pytest

    with pytest.raises(ValueError):
        SmaCrossoverStrategy(fast_period=5, slow_period=3)


def test_sma_crossover_debug_state_exposes_computed_smas():
    strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)
    assert strategy.debug_state() == {"fast_sma": None, "slow_sma": None}
    for close in CLOSES[:3]:  # enough to compute both SMAs
        strategy.on_bar(SimpleNamespace(close=close), position_state=None)
    state = strategy.debug_state()
    assert state["fast_sma"] == pytest.approx(11.5)  # SMA2(11,12)
    assert state["slow_sma"] == pytest.approx(11.0)  # SMA3(10,11,12)
