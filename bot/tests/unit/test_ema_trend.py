"""Tests for EmaTrendStrategy -- the slow end of the trend spectrum."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.ema_trend import EmaTrendStrategy


def _bar(close):
    return SimpleNamespace(open=close, high=close + 1, low=close - 1, close=close)


def _feed(strategy, closes):
    return [strategy.on_bar(_bar(c), None).action for c in closes]


def test_no_signal_before_the_ema_can_be_computed():
    s = EmaTrendStrategy(period=10)
    assert _feed(s, [100.0] * 9) == [SignalAction.HOLD] * 9
    assert s.debug_state()["ema"] is None


def test_the_first_computable_bar_only_sets_the_reference():
    """Otherwise every restart of the bot opens a position on bar one."""
    s = EmaTrendStrategy(period=5)
    actions = _feed(s, [100, 101, 102, 103, 104])
    assert actions[-1] == SignalAction.HOLD
    assert s.debug_state()["ema"] == pytest.approx(102.0)


def test_a_cross_above_the_ema_buys_and_a_cross_below_sells():
    s = EmaTrendStrategy(period=5)
    _feed(s, [100, 100, 100, 100, 100])       # flat: ema 100, price not above
    assert s.on_bar(_bar(110), None).action == SignalAction.BUY
    # Now drive it back under.
    actions = _feed(s, [90, 80, 70])
    assert SignalAction.SELL in actions


def test_it_does_not_re_fire_while_the_stance_is_unchanged():
    """A trend follower that re-buys every bar of an uptrend pays the spread
    every bar of the uptrend."""
    s = EmaTrendStrategy(period=5)
    _feed(s, [100] * 5)
    assert s.on_bar(_bar(110), None).action == SignalAction.BUY
    assert _feed(s, [120, 130, 140, 150]) == [SignalAction.HOLD] * 4


def test_the_ema_matches_the_standard_recurrence_from_an_sma_seed():
    """Same seeding convention as MacdTrendStrategy, so the two are
    comparable rather than nearly-comparable."""
    s = EmaTrendStrategy(period=3)
    _feed(s, [10, 20, 30])
    assert s.debug_state()["ema"] == pytest.approx(20.0)     # SMA seed
    s.on_bar(_bar(40), None)
    k = 2 / 4
    assert s.debug_state()["ema"] == pytest.approx(40 * k + 20 * (1 - k))


def test_a_slow_period_trades_rarely_on_five_years_of_daily_bars():
    """The honest caveat, pinned as a test: EMA(112) on ~1,200 bars of a
    trending series fires a handful of times. Any Sharpe computed from that
    has enormous error bars and the trade count must be reported with it."""
    s = EmaTrendStrategy(period=112)
    closes = [100 * (1.0004 ** i) for i in range(1200)]
    actions = _feed(s, closes)
    assert sum(a != SignalAction.HOLD for a in actions) < 15


def test_period_must_be_at_least_two():
    with pytest.raises(ValueError):
        EmaTrendStrategy(period=1)


def test_snapshot_round_trips():
    s = EmaTrendStrategy(period=5)
    _feed(s, [100, 101, 102, 103, 104, 110])
    restored = EmaTrendStrategy(period=5)
    restored.load_state_snapshot(s.get_state_snapshot())
    assert restored.debug_state() == s.debug_state()


def test_an_empty_snapshot_leaves_a_fresh_strategy_untouched():
    s = EmaTrendStrategy(period=5)
    s.load_state_snapshot({})
    assert s.debug_state()["ema"] is None
