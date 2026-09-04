"""Tests for growmore_bot.strategies.vwap_session_bounce.VwapSessionBounceStrategy.

CPR (Central Pivot Range) sets the day's directional bias (a gate); a live
VWAP crossing is the entry trigger -- a trade only fires when price is
outside the CPR band on the correct side AND crosses back over VWAP in that
same direction. CPR is derived from a historical daily Bar's H/L/C (no
`vwap` attribute); the live trigger comes from a Quote-like object that DOES
carry `vwap` -- the strategy tells the two apart via `getattr(bar, "vwap",
None)`.

Hand-computed CPR for a prior day's bar with high=110, low=90, close=100:
    pivot = (110+90+100)/3 = 100
    bc = (110+90)/2 = 100
    tc = 2*100 - 100 = 100
That's a degenerate (zero-width) CPR band -- fine for confirming the
formula, but the routing tests below use an asymmetric prior day
(high=120, low=90, close=100) instead, for a real, non-degenerate band:
    pivot = (120+90+100)/3 = 103.333
    bc = (120+90)/2 = 105
    tc = 2*103.333 - 105 = 101.667
    -> band = [min(101.667,105), max(...)] = [101.667, 105]
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.vwap_session_bounce import VwapSessionBounceStrategy


def _daily_bar(high, low, close):
    return SimpleNamespace(high=high, low=low, close=close)


def _quote(ltp, vwap):
    return SimpleNamespace(ltp=ltp, vwap=vwap)


PRIOR_DAY = _daily_bar(high=120, low=90, close=100)  # CPR band: [101.667, 105]


def test_cpr_is_computed_from_the_historical_daily_bar_during_warm_up():
    strategy = VwapSessionBounceStrategy()
    signal = strategy.on_bar(PRIOR_DAY, None)
    assert signal.action == SignalAction.HOLD  # no vwap yet, nothing to trade
    assert strategy._current_cpr == pytest.approx((101.66667, 103.33333, 105.0), abs=1e-3)


def test_buy_fires_when_above_cpr_top_and_crosses_up_through_vwap():
    strategy = VwapSessionBounceStrategy()
    strategy.on_bar(PRIOR_DAY, None)  # warm-up: sets CPR = [101.667, 105]

    strategy.on_bar(_quote(ltp=106, vwap=107), None)  # above CPR top, below vwap -- establishes prev
    signal = strategy.on_bar(_quote(ltp=108, vwap=107), None)  # crosses above vwap, still above CPR top

    assert signal.action == SignalAction.BUY


def test_sell_fires_when_below_cpr_bottom_and_crosses_down_through_vwap():
    strategy = VwapSessionBounceStrategy()
    strategy.on_bar(PRIOR_DAY, None)

    strategy.on_bar(_quote(ltp=99, vwap=98), None)  # below CPR bottom, above vwap -- establishes prev
    signal = strategy.on_bar(_quote(ltp=97, vwap=98), None)  # crosses below vwap, still below CPR bottom

    assert signal.action == SignalAction.SELL


def test_no_signal_when_vwap_crosses_but_price_is_inside_the_cpr_band():
    strategy = VwapSessionBounceStrategy()
    strategy.on_bar(PRIOR_DAY, None)

    strategy.on_bar(_quote(ltp=103, vwap=104), None)  # inside CPR band [101.667, 105]
    signal = strategy.on_bar(_quote(ltp=104.5, vwap=104), None)  # crosses vwap, still inside band

    assert signal.action == SignalAction.HOLD


def test_no_signal_on_a_vwap_cross_against_the_cpr_bias():
    strategy = VwapSessionBounceStrategy()
    strategy.on_bar(PRIOR_DAY, None)

    # Price crosses UP through vwap, but is below the CPR bottom (bearish
    # bias day) -- the CPR gate should suppress what would otherwise be a
    # BUY-shaped VWAP cross.
    strategy.on_bar(_quote(ltp=99, vwap=100), None)
    signal = strategy.on_bar(_quote(ltp=101, vwap=100), None)

    assert signal.action == SignalAction.HOLD


def test_first_live_quote_only_establishes_the_crossing_reference():
    strategy = VwapSessionBounceStrategy()
    strategy.on_bar(PRIOR_DAY, None)
    signal = strategy.on_bar(_quote(ltp=106, vwap=107), None)
    assert signal.action == SignalAction.HOLD


def test_tolerates_no_cpr_yet_without_raising():
    # A live quote arriving before any warm-up bar has ever been seen
    # (shouldn't happen in practice, but must not crash).
    strategy = VwapSessionBounceStrategy()
    signal = strategy.on_bar(_quote(ltp=106, vwap=107), None)
    assert signal.action == SignalAction.HOLD


def test_cpr_updates_across_multiple_warm_up_bars_to_the_latest_one():
    strategy = VwapSessionBounceStrategy()
    strategy.on_bar(_daily_bar(high=110, low=100, close=105), None)
    strategy.on_bar(PRIOR_DAY, None)  # the LATEST prior-day bar should win
    assert strategy._current_cpr == pytest.approx((101.66667, 103.33333, 105.0), abs=1e-3)


def test_debug_state_reports_cpr_and_the_latest_live_vwap():
    strategy = VwapSessionBounceStrategy()
    strategy.on_bar(PRIOR_DAY, None)
    strategy.on_bar(_quote(ltp=106, vwap=107), None)
    state = strategy.debug_state()
    assert state["vwap"] == pytest.approx(107)
    assert state["cpr_bottom"] == pytest.approx(101.66667, abs=1e-3)
    assert state["cpr_top"] == pytest.approx(105.0, abs=1e-3)


def test_requires_intraday_flatten_is_true():
    assert VwapSessionBounceStrategy().requires_intraday_flatten is True


class TestSnapshot:
    def test_empty_before_any_crossing_reference_exists(self):
        strategy = VwapSessionBounceStrategy()
        strategy.on_bar(PRIOR_DAY, None)
        assert strategy.get_state_snapshot() == {}

    def test_round_trip_restores_crossing_reference_not_cpr(self):
        strategy = VwapSessionBounceStrategy()
        strategy.on_bar(PRIOR_DAY, None)
        strategy.on_bar(_quote(ltp=106, vwap=107), None)
        snapshot = strategy.get_state_snapshot()
        assert snapshot == {"prev_above_vwap": False}

        fresh = VwapSessionBounceStrategy()
        fresh.load_state_snapshot(snapshot)
        assert fresh._prev_above_vwap is False
        assert fresh._current_cpr is None  # rebuilt by warm-up, not snapshotted

    def test_load_state_snapshot_tolerates_empty_dict(self):
        VwapSessionBounceStrategy().load_state_snapshot({})  # must not raise
