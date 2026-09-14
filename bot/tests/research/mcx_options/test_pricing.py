"""Tests for Black-76 option pricing (bot/research/mcx_options/pricing.py).

Black-76 prices off the underlying FUTURES price, not spot -- MCX commodity
options are options on futures, unlike NSE stock options (Black-Scholes,
see research/stock_options/pricing.py). Written before the implementation
per repo TDD convention.
"""
from __future__ import annotations

import math

import pytest

from research.mcx_options.pricing import (
    black76_delta,
    black76_price,
    implied_vol_b76,
)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _reference(option_type, F, K, T, sigma, r):
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    discount = math.exp(-r * T)
    if option_type == "CE":
        price = discount * (F * _norm_cdf(d1) - K * _norm_cdf(d2))
        delta = discount * _norm_cdf(d1)
    else:
        price = discount * (K * _norm_cdf(-d2) - F * _norm_cdf(-d1))
        delta = discount * (_norm_cdf(d1) - 1.0)
    return price, delta


class TestBlack76KnownValues:
    """F=K=100, T=0.25, sigma=0.20, r=0.05 -- round numbers, hand-verifiable."""

    F, K, T, SIGMA, R = 100.0, 100.0, 0.25, 0.20, 0.05

    def test_call_price(self):
        expected, _ = _reference("CE", self.F, self.K, self.T, self.SIGMA, self.R)
        assert expected == pytest.approx(3.938224, abs=1e-4)
        got = black76_price("CE", self.F, self.K, self.T, self.SIGMA, self.R)
        assert got == pytest.approx(expected, abs=1e-6)

    def test_put_price(self):
        expected, _ = _reference("PE", self.F, self.K, self.T, self.SIGMA, self.R)
        assert expected == pytest.approx(3.938224, abs=1e-4)
        got = black76_price("PE", self.F, self.K, self.T, self.SIGMA, self.R)
        assert got == pytest.approx(expected, abs=1e-6)

    def test_call_delta(self):
        _, expected = _reference("CE", self.F, self.K, self.T, self.SIGMA, self.R)
        assert expected == pytest.approx(0.51348, abs=1e-4)
        got = black76_delta("CE", self.F, self.K, self.T, self.SIGMA, self.R)
        assert got == pytest.approx(expected, abs=1e-6)

    def test_put_delta(self):
        _, expected = _reference("PE", self.F, self.K, self.T, self.SIGMA, self.R)
        assert expected == pytest.approx(-0.474098, abs=1e-4)
        got = black76_delta("PE", self.F, self.K, self.T, self.SIGMA, self.R)
        assert got == pytest.approx(expected, abs=1e-6)


class TestImpliedVolRoundTrip:
    def test_call_round_trip(self):
        F, K, T, r = 100.0, 105.0, 0.5, 0.05
        true_sigma = 0.30
        price = black76_price("CE", F, K, T, true_sigma, r)
        recovered = implied_vol_b76("CE", price, F, K, T, r)
        assert recovered is not None
        assert recovered == pytest.approx(true_sigma, abs=1e-4)

    def test_put_round_trip(self):
        F, K, T, r = 100.0, 95.0, 0.1, 0.065
        true_sigma = 0.45
        price = black76_price("PE", F, K, T, true_sigma, r)
        recovered = implied_vol_b76("PE", price, F, K, T, r)
        assert recovered is not None
        assert recovered == pytest.approx(true_sigma, abs=1e-4)

    def test_deep_itm_converges(self):
        F, K, T, r = 100.0, 50.0, 0.25, 0.05
        true_sigma = 0.25
        price = black76_price("CE", F, K, T, true_sigma, r)
        recovered = implied_vol_b76("CE", price, F, K, T, r)
        assert recovered is not None
        assert recovered == pytest.approx(true_sigma, abs=1e-3)

    def test_deep_otm_converges(self):
        F, K, T, r = 100.0, 200.0, 0.25, 0.05
        true_sigma = 0.60
        price = black76_price("CE", F, K, T, true_sigma, r)
        recovered = implied_vol_b76("CE", price, F, K, T, r)
        assert recovered is not None
        assert recovered == pytest.approx(true_sigma, abs=1e-3)


class TestPutCallParity:
    def test_parity_holds_independently(self):
        F, K, T, sigma, r = 120.0, 110.0, 0.4, 0.35, 0.07
        call = black76_price("CE", F, K, T, sigma, r)
        put = black76_price("PE", F, K, T, sigma, r)
        discount = math.exp(-r * T)
        assert (call - put) == pytest.approx(discount * (F - K), abs=1e-6)


class TestEdgeCases:
    def test_expired_call_price_is_intrinsic(self):
        assert black76_price("CE", 110.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(10.0, abs=1e-9)

    def test_expired_call_out_of_money_price_is_zero(self):
        assert black76_price("CE", 90.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(0.0, abs=1e-9)

    def test_expired_put_price_is_intrinsic(self):
        assert black76_price("PE", 90.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(10.0, abs=1e-9)

    def test_negative_time_treated_as_expired(self):
        assert black76_price("CE", 110.0, 100.0, -0.01, 0.2, 0.05) == pytest.approx(10.0, abs=1e-9)

    def test_expired_call_delta_itm_is_one(self):
        assert black76_delta("CE", 110.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(1.0, abs=1e-9)

    def test_expired_call_delta_otm_is_zero(self):
        assert black76_delta("CE", 90.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(0.0, abs=1e-9)

    def test_expired_put_delta_itm_is_negative_one(self):
        assert black76_delta("PE", 90.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(-1.0, abs=1e-9)

    def test_expired_put_delta_otm_is_zero(self):
        assert black76_delta("PE", 110.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(0.0, abs=1e-9)

    def test_nonpositive_sigma_price_is_intrinsic(self):
        assert black76_price("CE", 110.0, 100.0, 0.25, 0.0, 0.05) == pytest.approx(10.0 * math.exp(-0.05 * 0.25), abs=1e-6)

    def test_negative_sigma_does_not_raise(self):
        # Should not raise (e.g. div by zero / domain error) -- falls back like sigma<=0.
        price = black76_price("CE", 100.0, 100.0, 0.25, -0.1, 0.05)
        assert price >= 0.0

    def test_implied_vol_below_intrinsic_returns_none(self):
        # A call trading below intrinsic value is an arbitrage-violating print.
        F, K, T, r = 110.0, 100.0, 0.25, 0.05
        discount = math.exp(-r * T)
        intrinsic = discount * (F - K)
        below = intrinsic - 1.0
        assert implied_vol_b76("CE", below, F, K, T, r) is None

    def test_implied_vol_non_positive_price_returns_none(self):
        assert implied_vol_b76("CE", 0.0, 100.0, 100.0, 0.25, 0.05) is None
        assert implied_vol_b76("CE", -5.0, 100.0, 100.0, 0.25, 0.05) is None

    def test_implied_vol_expired_returns_none(self):
        assert implied_vol_b76("CE", 5.0, 100.0, 100.0, 0.0, 0.05) is None

    def test_implied_vol_price_too_high_returns_none(self):
        # Above what even max vol could produce.
        assert implied_vol_b76("CE", 1e6, 100.0, 100.0, 0.25, 0.05) is None
