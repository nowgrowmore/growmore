"""Tests for growmore_bot.strategies.ensemble_trend.EnsembleTrendStrategy."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.ensemble_trend import DEFAULT_SPEEDS, EnsembleTrendStrategy


def _bar(close):
    return SimpleNamespace(open=close, high=close + 1, low=close - 1, close=close)


def _feed(strategy, closes):
    return [strategy.on_bar(_bar(c), None) for c in closes]


def test_defaults_to_a_simple_majority_of_five_speeds():
    s = EnsembleTrendStrategy()
    assert len(s._members) == len(DEFAULT_SPEEDS) == 5
    assert s.min_agreement == 3


def test_holds_until_enough_members_have_formed_a_view():
    """A MACD member only speaks on a crossing, so early on most have no
    stance at all. Trading before a quorum exists would be acting on one
    member's opinion while pretending it was the ensemble's."""
    s = EnsembleTrendStrategy(speeds=[(2, 3, 2), (3, 5, 2), (5, 9, 3)], min_agreement=2)
    signals = _feed(s, [100.0 + i for i in range(4)])
    assert all(sig.action == SignalAction.HOLD for sig in signals)


# A perfectly linear ramp is a bad fixture here: MACD and its signal line
# converge to equality in a constant-increment trend, so which is greater
# becomes floating-point noise. These use a flat stretch followed by real
# exponential moves, which is both more realistic and unambiguous.
FLAT = [100.0] * 20
UP = [100.0 * (1.02 ** i) for i in range(1, 26)]
DOWN = [UP[-1] * (0.98 ** i) for i in range(1, 26)]


def test_a_trend_starting_after_a_flat_stretch_turns_the_ensemble_bullish():
    s = EnsembleTrendStrategy(speeds=[(2, 3, 2), (3, 5, 2), (5, 9, 3)], min_agreement=2)
    actions = [sig.action for sig in _feed(s, FLAT + UP)]
    assert SignalAction.BUY in actions
    # It must not fire during the flat stretch -- there is nothing to trade.
    assert actions.index(SignalAction.BUY) >= len(FLAT)
    assert s.debug_state()["bullish_votes"] == 3.0


def test_it_signals_on_the_transition_not_on_every_bar_it_stays_agreed():
    """The same crossing discipline every other strategy here uses."""
    s = EnsembleTrendStrategy(speeds=[(2, 3, 2), (3, 5, 2), (5, 9, 3)], min_agreement=2)
    actions = [sig.action for sig in _feed(s, FLAT + UP)]
    assert actions.count(SignalAction.BUY) == 1


def test_a_reversal_sells_after_it_has_bought():
    s = EnsembleTrendStrategy(speeds=[(2, 3, 2), (3, 5, 2), (5, 9, 3)], min_agreement=2)
    actions = [sig.action for sig in _feed(s, FLAT + UP + DOWN)]
    assert SignalAction.BUY in actions and SignalAction.SELL in actions
    assert actions.index(SignalAction.BUY) < actions.index(SignalAction.SELL)


def test_a_minority_of_dissenting_members_cannot_flip_the_ensemble():
    """The whole premise: fast members defect first on a pullback, and the
    ensemble only turns once a majority has. Uses the real DEFAULT_SPEEDS
    deliberately -- with speeds clustered close together (2,3,2)/(3,5,2)/
    (5,9,3) every member flips on the same bar and the vote adds nothing,
    which is exactly why the shipped speeds are spread from 5/13/5 out to
    26/52/18."""
    s = EnsembleTrendStrategy()
    _feed(s, [100.0] * 80 + [100.0 * (1.01 ** i) for i in range(1, 120)])
    assert s.debug_state()["bullish_votes"] == 5.0

    peak = 100.0 * (1.01 ** 119)
    first = s.on_bar(_bar(peak * 0.99), None)

    # Two of five have defected, which is a minority -- no trade yet.
    assert s.debug_state()["bullish_votes"] == 3.0
    assert first.action == SignalAction.HOLD

    # A second down bar tips the majority, and only now does it sell.
    second = s.on_bar(_bar(peak * 0.99 ** 2), None)
    assert s.debug_state()["bullish_votes"] == 2.0
    assert second.action == SignalAction.SELL


def test_snapshot_round_trip_preserves_the_vote_and_the_reference():
    """The scheduler rebuilds the strategy every tick, so without this a
    settled ensemble would re-signal on the first live quote of every day."""
    s = EnsembleTrendStrategy(speeds=[(2, 3, 2), (3, 5, 2), (5, 9, 3)], min_agreement=2)
    _feed(s, [100.0 + 3 * i for i in range(30)])
    snap = s.get_state_snapshot()

    restored = EnsembleTrendStrategy(speeds=[(2, 3, 2), (3, 5, 2), (5, 9, 3)], min_agreement=2)
    restored.load_state_snapshot(snap)
    assert restored._stance == s._stance
    assert restored._prev_bullish == s._prev_bullish


def test_an_empty_snapshot_is_ignored_rather_than_raising():
    s = EnsembleTrendStrategy()
    s.load_state_snapshot({})
    assert s._prev_bullish is None


def test_rejects_an_impossible_agreement_threshold():
    with pytest.raises(ValueError):
        EnsembleTrendStrategy(speeds=[(2, 3, 2)], min_agreement=2)
    with pytest.raises(ValueError):
        EnsembleTrendStrategy(speeds=[], min_agreement=1)
