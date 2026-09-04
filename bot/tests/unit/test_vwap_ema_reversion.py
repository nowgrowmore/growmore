"""Tests for growmore_bot.strategies.vwap_ema_reversion.VwapEmaReversionStrategy.

Every case hand-computed. Flat bars (high=low=close) used throughout so
typical_price == close, keeping the VWAP arithmetic simple to verify by
hand -- vwap_period=3, ema_fast=2, ema_slow=3.

Bars (price, volume): (100,10) (102,10) (98,10) (105,10) (90,10)
- Bar3: first VWAP computable = (100+102+98)*10/30 = 100.0; close=98 < vwap
  -> first "above_vwap" reading (False), nothing to cross from yet -> HOLD.
- Bar4: VWAP over bars 2-4 = (102+98+105)*10/30 = 101.667; close=105 > vwap
  -> crosses UP from bar3's False. fast EMA=103.0 >= slow EMA=102.5 -> BUY.
- Bar5: VWAP over bars 3-5 = (98+105+90)*10/30 = 97.667; close=90 < vwap
  -> crosses DOWN from bar4's True. fast EMA=94.333 <= slow EMA=96.25 -> SELL.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.vwap_ema_reversion import VwapEmaReversionStrategy


def _flat_bar(price, volume):
    return SimpleNamespace(high=price, low=price, close=price, volume=volume)


BARS = [
    _flat_bar(100, 10),
    _flat_bar(102, 10),
    _flat_bar(98, 10),
    _flat_bar(105, 10),
    _flat_bar(90, 10),
]


def test_hand_computed_sequence_produces_hold_hold_hold_buy_sell():
    strategy = VwapEmaReversionStrategy(vwap_period=3, ema_fast=2, ema_slow=3)
    actions = [strategy.on_bar(bar, None).action for bar in BARS]
    assert actions == [
        SignalAction.HOLD,
        SignalAction.HOLD,
        SignalAction.HOLD,
        SignalAction.BUY,
        SignalAction.SELL,
    ]


def test_rejects_invalid_params():
    with pytest.raises(ValueError):
        VwapEmaReversionStrategy(vwap_period=0, ema_fast=2, ema_slow=3)
    with pytest.raises(ValueError):
        VwapEmaReversionStrategy(vwap_period=3, ema_fast=5, ema_slow=3)


def test_debug_state_reports_vwap_and_emas_after_enough_bars():
    strategy = VwapEmaReversionStrategy(vwap_period=3, ema_fast=2, ema_slow=3)
    for bar in BARS[:4]:
        strategy.on_bar(bar, None)
    state = strategy.debug_state()
    assert state["vwap"] == pytest.approx(101.66667, abs=1e-3)
    assert state["ema_fast"] == pytest.approx(103.0)
    assert state["ema_slow"] == pytest.approx(102.5)


class TestSnapshot:
    def test_empty_before_any_crossing_reference_exists(self):
        strategy = VwapEmaReversionStrategy(vwap_period=3, ema_fast=2, ema_slow=3)
        assert strategy.get_state_snapshot() == {}

    def test_round_trip_restores_crossing_reference(self):
        strategy = VwapEmaReversionStrategy(vwap_period=3, ema_fast=2, ema_slow=3)
        for bar in BARS[:4]:
            strategy.on_bar(bar, None)
        snapshot = strategy.get_state_snapshot()
        assert snapshot == {"prev_above_vwap": True}

        fresh = VwapEmaReversionStrategy(vwap_period=3, ema_fast=2, ema_slow=3)
        fresh.load_state_snapshot(snapshot)
        assert fresh._prev_above_vwap is True

    def test_load_state_snapshot_tolerates_empty_dict(self):
        strategy = VwapEmaReversionStrategy(vwap_period=3, ema_fast=2, ema_slow=3)
        strategy.load_state_snapshot({})  # must not raise
