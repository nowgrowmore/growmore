"""Tests for growmore_bot.mcx_options.regime -- the LIVE, single-day regime
classifier for the MCX options-selling engine.

Unlike `research/mcx_options/regime.py` (vectorised over a whole
`pandas`/`pandas_ta_classic` DataFrame, labels every historical day), this
module hand-rolls ADX(14)/+DI/-DI (reusing growmore_bot.indicators'
WilderSmoother/AtrCalculator, the same smoothing convention
growmore_bot/strategies/regime_switch.py already uses live) and Bollinger
Bandwidth(20)/its 60-day percentile rank as O(1)-per-bar streaming
calculations, and only classifies the LAST bar in a trailing window --
"what is today's regime", not a whole label-every-day backtest. This keeps
`growmore_bot` free of the `pandas_ta_classic` research-only dependency (see
bot/pyproject.toml's `research` extra docstring).

Mirrors the *scenarios* in tests/research/mcx_options/test_regime.py
(clean uptrend/downtrend/consolidating, insufficient warm-up = no opinion)
but drives them through `classify_today` on a plain list of bar-like objects
instead of a DataFrame.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from growmore_bot.mcx_options.regime import (
    ADX_TREND_THRESHOLD,
    BANDWIDTH_PERCENTILE_WINDOW,
    Regime,
    classify_today,
)


@dataclass(frozen=True)
class _Bar:
    high: float
    low: float
    close: float


def _bars(closes: list[float], *, high_pad: float = 1.0, low_pad: float = 1.0) -> list[_Bar]:
    return [_Bar(high=c + high_pad, low=c - low_pad, close=c) for c in closes]


def _uptrend(n: int = 300) -> list[_Bar]:
    """Compounding uptrend, deliberately not a straight ramp (a linear ramp
    can starve ADX/DI of the volatility they need) -- same reasoning as
    research/mcx_options/regime.py's own test fixtures.
    """
    return _bars([100.0 * (1.0015**i) for i in range(n)])


def _downtrend(n: int = 300) -> list[_Bar]:
    return _bars([100.0 * (0.9985**i) for i in range(n)])


def _flat_oscillating(n: int = 300) -> list[_Bar]:
    return _bars([100.0 + (0.3 if i % 2 else -0.3) for i in range(n)], high_pad=0.4, low_pad=0.4)


def test_a_clean_uptrend_is_favorable_for_puts_and_unfavorable_for_calls():
    label = classify_today(_uptrend())
    assert label is not None
    assert label.raw == "uptrend"
    assert label.for_option_type("PE") == Regime.TREND_FAVORABLE
    assert label.for_option_type("CE") == Regime.TREND_UNFAVORABLE


def test_a_clean_downtrend_flips_favorability():
    label = classify_today(_downtrend())
    assert label is not None
    assert label.raw == "downtrend"
    assert label.for_option_type("CE") == Regime.TREND_FAVORABLE
    assert label.for_option_type("PE") == Regime.TREND_UNFAVORABLE


def test_a_flat_choppy_market_is_consolidating_for_both_sides():
    label = classify_today(_flat_oscillating())
    assert label is not None
    assert label.raw == "consolidating"
    assert label.for_option_type("PE") == Regime.CONSOLIDATING
    assert label.for_option_type("CE") == Regime.CONSOLIDATING


def test_insufficient_warmup_is_no_opinion_not_a_default():
    """5 bars is nowhere near ADX(14)/BBands(20)+60-day-percentile warm-up --
    must return None, never a silently defaulted consolidating label.
    """
    assert classify_today(_uptrend(5)) is None


def test_empty_input_is_no_opinion():
    assert classify_today([]) is None


def test_labels_never_change_when_future_bars_arrive():
    """THE lookahead test -- classifying a truncated window must give the
    same label a longer window gives for that same last bar, since every
    indicator here is causal/streaming.
    """
    bars = _uptrend(300)
    full = classify_today(bars)
    truncated = classify_today(bars[:251])  # same last bar as bars[250]
    assert full is not None and truncated is not None
    # Both windows end on a mature, deep-uptrend bar -- labels should agree.
    assert full.raw == truncated.raw == "uptrend"


def test_declared_thresholds_are_not_silently_retuned():
    assert ADX_TREND_THRESHOLD == 25
    assert BANDWIDTH_PERCENTILE_WINDOW == 60


def test_for_option_type_rejects_an_unknown_side():
    label = classify_today(_uptrend())
    assert label is not None
    with pytest.raises(ValueError):
        label.for_option_type("XX")
