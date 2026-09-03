"""Known-input/known-output test for the Bollinger Band reversion strategy.

BUY when price closes back INSIDE the lower band after having closed outside
it (a faded extreme), mirror for SELL at the upper band -- entering an
extreme is never itself a signal, only recovering from one is (same
philosophy as the RSI mean-reversion strategy). Uses population variance
(divide by N) and num_std=1.0 here (not the classic 2.0) purely so a small
hand-computable window actually produces a breach -- see the worked algebra
in this docstring for why N=4/k=2.0 can never breach with a single-bar dip
against an otherwise-flat window (the outlier inflates its own band exactly
enough to stay inside), which is *why* this test uses k=1.0.

closes = [10, 10, 10, 4, 10, 10, 10, 10, 16, 10], period=4, num_std=1.0

idx0-2: <4 closes (need period=4)                                   -> HOLD
idx3 (4): window [10,10,10,4] -> mean=8.5, var=6.75, std=2.598
          lower=5.902, upper=11.098. close=4 < lower (first
          computable point, no prior state to recover from)          -> HOLD
idx4 (10): window [10,10,4,10] -- same multiset, same bands. close=10
          >= lower(5.902) -- was below, now recovered                -> BUY
idx5 (10): window [10,4,10,10] -- same multiset/bands. close=10 inside -> HOLD
idx6 (10): window [4,10,10,10] -- same multiset/bands. close=10 inside -> HOLD
idx7 (10): window [10,10,10,10] -> mean=10, std=0, bands=[10,10].
          close=10 is not strictly < or > either band              -> HOLD
idx8 (16): window [10,10,10,16] -> mean=11.5, var=6.75, std=2.598
          lower=8.902, upper=14.098. close=16 > upper (first entry
          into this extreme, not a recovery)                        -> HOLD
idx9 (10): window [10,10,16,10] -- same multiset/bands. close=10 <=
          upper(14.098) -- was above, now recovered                 -> SELL
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.bollinger_reversion import BollingerReversionStrategy

CLOSES = [10, 10, 10, 4, 10, 10, 10, 10, 16, 10]
EXPECTED = [
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.BUY,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.SELL,
]


def test_bollinger_reversion_known_sequence():
    strategy = BollingerReversionStrategy(period=4, num_std=1.0)
    actual = []
    for close in CLOSES:
        bar = SimpleNamespace(close=close)
        signal = strategy.on_bar(bar, position_state=None)
        actual.append(signal.action)

    assert actual == EXPECTED


def test_bollinger_reversion_requires_period_at_least_two():
    with pytest.raises(ValueError):
        BollingerReversionStrategy(period=1)


def test_bollinger_reversion_requires_positive_num_std():
    with pytest.raises(ValueError):
        BollingerReversionStrategy(period=20, num_std=0)
