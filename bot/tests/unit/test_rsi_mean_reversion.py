"""Known-input/known-output test for the RSI mean-reversion strategy.

RSI here uses simple (unweighted) average gain/loss over `period` diffs (a
"Cutler's RSI" variant) rather than Wilder's exponential smoothing -- an
explicit, documented choice (see rsi_mean_reversion.py) that keeps this hand
computation unambiguous. Values below are computed independently of the
implementation.

closes = [50, 48, 45, 44, 47, 52, 58, 55, 50, 46], period=3, oversold=30, overbought=70

diffs: -2, -3, -1, +3, +5, +6, -3, -5, -4

idx0-2: <4 closes (need period+1=4) -> HOLD
idx3 (44): diffs window [-2,-3,-1] -> avg_gain=0, avg_loss=2.0 -> RSI=0 (first
      computable value, no prior RSI to cross from) -> HOLD
idx4 (47): diffs window [-3,-1,+3] -> avg_gain=1.0, avg_loss=4/3 -> RS=0.75 ->
      RSI=100-100/1.75=42.857. prev RSI=0 (<=30) crosses above 30 -> BUY
idx5 (52): diffs window [-1,+3,+5] -> avg_gain=8/3, avg_loss=1/3 -> RS=8 ->
      RSI=100-100/9=88.889. prev=42.857 (neither <=30 nor >=70) -> HOLD
idx6 (58): diffs window [+3,+5,+6] -> avg_gain=14/3, avg_loss=0 -> RSI=100.
      prev=88.889 (>=70) but new RSI=100 is not <70 -> no fresh downcross -> HOLD
idx7 (55): diffs window [+5,+6,-3] -> avg_gain=11/3, avg_loss=1.0 -> RS=3.667 ->
      RSI=100-100/4.667=78.571. prev=100 (>=70), new RSI still not <70 -> HOLD
idx8 (50): diffs window [+6,-3,-5] -> avg_gain=2.0, avg_loss=8/3 -> RS=0.75 ->
      RSI=42.857. prev=78.571 (>=70), new RSI<70 -> crosses back below
      overbought -> SELL
idx9 (46): diffs window [-3,-5,-4] -> avg_gain=0, avg_loss=4.0 -> RSI=0.
      prev=42.857 (neither <=30 nor >=70) -- crossing INTO oversold isn't
      itself a signal, only crossing back OUT is -> HOLD
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.rsi_mean_reversion import RsiMeanReversionStrategy

CLOSES = [50, 48, 45, 44, 47, 52, 58, 55, 50, 46]
EXPECTED = [
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.BUY,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.HOLD,
    SignalAction.SELL,
    SignalAction.HOLD,
]


def test_rsi_mean_reversion_known_sequence():
    strategy = RsiMeanReversionStrategy(period=3, oversold=30, overbought=70)
    actual = []
    for close in CLOSES:
        bar = SimpleNamespace(close=close)
        signal = strategy.on_bar(bar, position_state=None)
        actual.append(signal.action)

    assert actual == EXPECTED


def test_rsi_mean_reversion_requires_valid_thresholds():
    with pytest.raises(ValueError):
        RsiMeanReversionStrategy(period=14, oversold=70, overbought=30)


def test_rsi_mean_reversion_requires_positive_period():
    with pytest.raises(ValueError):
        RsiMeanReversionStrategy(period=0)


def test_rsi_mean_reversion_debug_state_exposes_rsi():
    strategy = RsiMeanReversionStrategy(period=3)
    assert strategy.debug_state() == {"rsi": None}
    for close in [50, 48, 45, 44]:  # enough for the first computable RSI value
        strategy.on_bar(SimpleNamespace(close=close), position_state=None)
    assert strategy.debug_state()["rsi"] == pytest.approx(0.0)
