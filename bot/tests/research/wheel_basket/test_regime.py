"""Protects the market-regime classifier against the one bug that would
invalidate every regime result: reading the future.

A classifier that peeks even one bar ahead produces a beautiful backtest and
no tradeable edge. `test_labels_never_change_when_future_bars_arrive` is the
cheapest possible check for that and is the reason this module exists.
"""
from __future__ import annotations

from datetime import date, timedelta

from research.wheel_basket.regime import (
    VIX_HIGH_PERCENTILE,
    VIX_PERCENTILE_WINDOW,
    regime_by_day,
)

START = date(2017, 1, 2)


def _days(n: int) -> list[date]:
    return [START + timedelta(days=i) for i in range(n)]


def _series(values: list[float]) -> dict:
    return dict(zip(_days(len(values)), values))


def _rising(n: int = 900) -> dict:
    """A compounding uptrend, deliberately NOT a straight line.

    A perfectly linear ramp has constant momentum, so its MACD histogram
    converges to exactly zero -- see `test_a_perfectly_linear_ramp_is_not_a_bull`.
    Real uptrends compound, and that is what the bull clause is about.
    """
    return _series([100.0 * (1.002 ** i) for i in range(n)])


def _linear(n: int = 900) -> dict:
    return _series([100.0 + i for i in range(n)])


def _falling(n: int = 900) -> dict:
    return _series([100.0 + n - i for i in range(n)])


def _calm_vix(n: int = 900) -> dict:
    return _series([15.0] * n)


def test_a_long_uptrend_with_calm_vol_is_bull():
    labels = regime_by_day(_rising(), _calm_vix())
    assert labels[_days(900)[-1]] == "bull"


def test_a_long_downtrend_is_bearish():
    labels = regime_by_day(_falling(), _calm_vix())
    assert labels[_days(900)[-1]] == "bearish"


def test_high_vol_outranks_trend():
    """A premium seller's regime is first a statement about option pricing.

    Declared in docs/wheel-basket-research.md Sec 5: high_vol is evaluated
    first, so a roaring bull market with a vol spike is still high_vol.
    """
    n = 900
    vix = [15.0] * (n - 1) + [80.0]
    labels = regime_by_day(_rising(n), _series(vix))
    assert labels[_days(n)[-1]] == "high_vol"


def test_a_trendless_market_is_consolidating():
    """Neither the bull nor the bearish clause is satisfied by a chop."""
    n = 900
    values = [100.0 + (5.0 if i % 2 else -5.0) for i in range(n)]
    labels = regime_by_day(_series(values), _calm_vix(n))
    assert labels[_days(n)[-1]] == "consolidating"


def test_labels_never_change_when_future_bars_arrive():
    """THE lookahead test. Classifying a truncated history must give exactly
    the labels the full history gives for those same days.
    """
    n = 900
    closes, vix = _rising(n), _calm_vix(n)
    full = regime_by_day(closes, vix)

    cut = _days(n)[700]
    truncated_closes = {d: v for d, v in closes.items() if d <= cut}
    truncated_vix = {d: v for d, v in vix.items() if d <= cut}
    partial = regime_by_day(truncated_closes, truncated_vix)

    assert partial, "expected some labels before the cut"
    for day, label in partial.items():
        assert full[day] == label, f"label for {day} changed once the future arrived"


def test_no_label_is_emitted_before_the_declared_warmup():
    """200-day SMA and a 504-day VIX percentile both have to be real."""
    labels = regime_by_day(_rising(300), _calm_vix(300))
    assert labels == {}


def test_vix_percentile_threshold_is_the_declared_one():
    """Guards the declared constant against silent drift; the research doc
    fixed it at the 80th percentile of a trailing 504 days.
    """
    assert VIX_HIGH_PERCENTILE == 0.80
    assert VIX_PERCENTILE_WINDOW == 504


def test_a_day_missing_vix_is_unlabelled_rather_than_guessed():
    """A missing vol reading must not silently be treated as calm -- that
    would turn a data gap into a 'safe to sell' signal.
    """
    n = 900
    vix = _calm_vix(n)
    last = _days(n)[-1]
    del vix[last]
    labels = regime_by_day(_rising(n), vix)
    assert last not in labels


def test_a_perfectly_linear_ramp_is_not_a_bull():
    """An edge case worth recording rather than discovering twice.

    Constant momentum drives the MACD histogram to exactly zero (measured:
    macd 7.0 vs signal 7.000000000000002 after 900 linear bars), so the
    declared bull clause -- which requires a POSITIVE histogram -- is not
    met. Price rising in a dead straight line is a mathematical artefact, not
    a market, and classifying it `consolidating` is the honest answer.
    """
    labels = regime_by_day(_linear(900), _calm_vix(900))
    assert labels[_days(900)[-1]] == "consolidating"
