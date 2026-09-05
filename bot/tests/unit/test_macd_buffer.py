"""Tests for MacdTrendStrategy's optional no-trade buffer.

The default (0.0) must reproduce the raw crossover EXACTLY -- every existing
backtest number depends on that -- and a non-zero band must suppress marginal
crossings rather than merely postpone them.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.macd_trend import MacdTrendStrategy

PARAMS = {"fast_period": 5, "slow_period": 13, "signal_period": 5}


def _bar(close, rng=2.0):
    return SimpleNamespace(open=close, high=close + rng, low=close - rng, close=close)


def _actions(strategy, closes):
    return [strategy.on_bar(_bar(c), None).action for c in closes]


def _wiggly(n):
    """A drifting series with frequent small reversals -- the regime a
    no-trade band is supposed to help in."""
    out, price = [], 100.0
    for i in range(n):
        price *= 1.0015 if (i // 3) % 2 == 0 else 0.9987
        out.append(price)
    return out


def test_the_default_reproduces_the_raw_crossover_bit_for_bit():
    closes = _wiggly(400)
    plain = _actions(MacdTrendStrategy(**PARAMS), closes)
    explicit_zero = _actions(MacdTrendStrategy(**PARAMS, buffer_atr=0.0), closes)
    assert plain == explicit_zero


def test_a_band_cuts_the_number_of_trades():
    closes = _wiggly(400)
    raw = _actions(MacdTrendStrategy(**PARAMS), closes)
    banded = _actions(MacdTrendStrategy(**PARAMS, buffer_atr=0.25), closes)

    raw_trades = sum(a != SignalAction.HOLD for a in raw)
    banded_trades = sum(a != SignalAction.HOLD for a in banded)
    assert raw_trades > 0
    assert banded_trades < raw_trades


def test_a_wider_band_cuts_more():
    closes = _wiggly(400)
    counts = [
        sum(a != SignalAction.HOLD for a in _actions(
            MacdTrendStrategy(**PARAMS, buffer_atr=theta), closes))
        for theta in (0.0, 0.1, 0.25, 1.0)
    ]
    assert counts == sorted(counts, reverse=True)


def test_a_suppressed_crossing_does_not_merely_get_postponed_one_bar():
    """If the band flipped the remembered stance while refusing to trade, the
    next bar would fire anyway and the band would achieve nothing but a
    one-bar delay. The stance must stay put."""
    s = MacdTrendStrategy(**PARAMS, buffer_atr=5.0)      # absurdly wide: nothing qualifies
    actions = _actions(s, _wiggly(400))
    assert all(a == SignalAction.HOLD for a in actions)


def test_a_decisive_crossing_still_fires_through_the_band():
    """A band that suppresses everything is a broken strategy, not a filter.

    The fixture is a real reversal in both directions, because a COMPOUNDING
    decline does not produce a bearish MACD stance -- as prices shrink the
    absolute fast/slow gap narrows toward zero from below, which reads as
    improving momentum. Only a genuine turn from an uptrend does.
    """
    s = MacdTrendStrategy(**PARAMS, buffer_atr=0.25)
    peak = 100 * 1.01 ** 59
    trough = peak * 0.97 ** 39
    sequence = (
        [100 * 1.01 ** i for i in range(60)]          # up
        + [peak * 0.97 ** i for i in range(1, 40)]    # decisive turn down
        + [trough * 1.04 ** i for i in range(1, 40)]  # decisive turn back up
    )
    actions = _actions(s, sequence)
    assert SignalAction.SELL in actions
    assert SignalAction.BUY in actions
    assert actions.index(SignalAction.SELL) < actions.index(SignalAction.BUY)


def test_a_negative_band_is_rejected():
    with pytest.raises(ValueError):
        MacdTrendStrategy(**PARAMS, buffer_atr=-0.1)


def test_no_atr_is_computed_when_the_band_is_off():
    """Costs nothing on the hot path for every existing config."""
    assert MacdTrendStrategy(**PARAMS)._atr is None
    assert MacdTrendStrategy(**PARAMS, buffer_atr=0.25)._atr is not None
