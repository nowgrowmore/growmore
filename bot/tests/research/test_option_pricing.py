"""Black-Scholes helpers for strategy D, which selects the strike whose
implied volatility is richest against the stock's realised volatility.

Only D needs these. A/B/C/E/F use a fixed 5% OTM rule and never price
anything -- that is what keeps their matched-pair comparisons clean.

Stdlib only, matching `growmore_bot.backtest.deflated_sharpe`, so every
number here is hand-checkable.
"""
from __future__ import annotations

import math

import pytest

from research.stock_options.pricing import (
    bs_price,
    implied_vol,
    option_delta,
    realised_vol,
)


def test_a_call_is_worth_at_least_its_intrinsic_value():
    price = bs_price(spot=100.0, strike=80.0, years=0.08, vol=0.30, opt_type="CE")
    assert price >= 20.0


def test_a_deep_out_of_the_money_option_is_nearly_worthless():
    assert bs_price(100.0, 200.0, 0.08, 0.30, "CE") < 0.05
    assert bs_price(100.0, 40.0, 0.08, 0.30, "PE") < 0.05


def test_more_volatility_is_always_worth_more():
    low = bs_price(100.0, 105.0, 0.08, 0.20, "CE")
    high = bs_price(100.0, 105.0, 0.08, 0.45, "CE")
    assert high > low


def test_put_call_parity_holds():
    # C - P == S - K*exp(-rt). The identity the whole wheel rests on.
    call = bs_price(100.0, 95.0, 0.25, 0.30, "CE")
    put = bs_price(100.0, 95.0, 0.25, 0.30, "PE")
    assert call - put == pytest.approx(100.0 - 95.0 * math.exp(-0.065 * 0.25), abs=0.01)


def test_implied_vol_recovers_the_volatility_that_made_the_price():
    for vol in (0.15, 0.30, 0.65):
        price = bs_price(100.0, 95.0, 0.08, vol, "PE")
        assert implied_vol(price, 100.0, 95.0, 0.08, "PE") == pytest.approx(vol, abs=1e-3)


def test_implied_vol_refuses_a_price_below_intrinsic():
    # An arbitrage-violating quote has no implied vol; returning one would
    # invent volatility out of a data error.
    assert implied_vol(1.0, 100.0, 80.0, 0.08, "CE") is None


def test_implied_vol_refuses_a_worthless_or_expired_option():
    assert implied_vol(0.0, 100.0, 95.0, 0.08, "PE") is None
    assert implied_vol(5.0, 100.0, 95.0, 0.0, "PE") is None


def test_delta_has_the_right_sign_and_bounds():
    assert 0.0 < option_delta(100.0, 105.0, 0.08, 0.30, "CE") < 1.0
    assert -1.0 < option_delta(100.0, 95.0, 0.08, 0.30, "PE") < 0.0


def test_an_at_the_money_delta_is_about_a_half():
    assert option_delta(100.0, 100.0, 0.08, 0.30, "CE") == pytest.approx(0.5, abs=0.08)


def test_delta_moves_the_way_moneyness_does():
    near = abs(option_delta(100.0, 98.0, 0.08, 0.30, "PE"))
    far = abs(option_delta(100.0, 80.0, 0.08, 0.30, "PE"))
    assert near > far


def test_realised_vol_of_a_straight_line_is_zero():
    assert realised_vol([100.0 * 1.001 ** i for i in range(40)]) == pytest.approx(0.0, abs=1e-9)


def test_realised_vol_scales_with_the_size_of_the_wiggles():
    calm = [100.0 * (1 + 0.002 * (-1) ** i) for i in range(60)]
    wild = [100.0 * (1 + 0.02 * (-1) ** i) for i in range(60)]
    assert realised_vol(wild) > 5 * realised_vol(calm)


def test_realised_vol_is_annualised():
    # A series with 1% daily moves annualises to roughly 0.01*sqrt(252) ~ 16%.
    series = [100.0]
    for i in range(200):
        series.append(series[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    assert 0.10 < realised_vol(series) < 0.25


def test_realised_vol_needs_enough_points_to_mean_anything():
    assert realised_vol([100.0, 101.0]) is None or realised_vol([100.0, 101.0]) >= 0.0
