"""Tests for growmore_bot.mcx_options.pricing -- the LIVE port of
research/mcx_options/pricing.py's Black-76 math.

Freshly written (not imported) per this repo's one-directional
research -> growmore_bot convention (see growmore_bot/wheel_basket/universe.py's
own comment), but the formulas and reference values are identical: Black-76
prices MCX commodity options off the underlying FUTURES price, not spot.
"""
from __future__ import annotations

import math

import pytest

from growmore_bot.mcx_options.pricing import black76_delta, black76_price, implied_vol_b76


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

    def test_negative_time_treated_as_expired(self):
        assert black76_price("CE", 110.0, 100.0, -0.01, 0.2, 0.05) == pytest.approx(10.0, abs=1e-9)

    def test_expired_call_delta_itm_is_one(self):
        assert black76_delta("CE", 110.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(1.0, abs=1e-9)

    def test_expired_put_delta_itm_is_negative_one(self):
        assert black76_delta("PE", 90.0, 100.0, 0.0, 0.2, 0.05) == pytest.approx(-1.0, abs=1e-9)

    def test_implied_vol_below_intrinsic_returns_none(self):
        F, K, T, r = 110.0, 100.0, 0.25, 0.05
        discount = math.exp(-r * T)
        intrinsic = discount * (F - K)
        below = intrinsic - 1.0
        assert implied_vol_b76("CE", below, F, K, T, r) is None

    def test_implied_vol_non_positive_price_returns_none(self):
        assert implied_vol_b76("CE", 0.0, 100.0, 100.0, 0.25, 0.05) is None

    def test_implied_vol_expired_returns_none(self):
        assert implied_vol_b76("CE", 5.0, 100.0, 100.0, 0.0, 0.05) is None

    def test_implied_vol_price_too_high_returns_none(self):
        assert implied_vol_b76("CE", 1e6, 100.0, 100.0, 0.25, 0.05) is None


# ---------------------------------------------------------------------------
# realised_vol -- the denominator of the variance-risk-premium filter
# (MCXOptionsConfig.min_iv_minus_realised_vol). Mirrored into growmore_bot
# from research/stock_options/pricing.py, which the live bot may not import.
# ---------------------------------------------------------------------------


def test_realised_vol_of_a_flat_series_is_zero():
    from growmore_bot.mcx_options.pricing import realised_vol

    assert realised_vol([100.0] * 30) == 0.0


def test_realised_vol_matches_a_hand_computed_annualisation():
    import math

    from growmore_bot.mcx_options.pricing import MCX_TRADING_DAYS, realised_vol

    # Alternating +1%/-1% log steps: population stdev of the log returns is
    # exactly the step size, annualised by sqrt(periods_per_year).
    step = 0.01
    closes = [100.0]
    for i in range(20):
        closes.append(closes[-1] * math.exp(step if i % 2 == 0 else -step))

    assert realised_vol(closes) == pytest.approx(step * math.sqrt(MCX_TRADING_DAYS), rel=1e-9)


def test_realised_vol_refuses_a_too_short_series_rather_than_guessing():
    from growmore_bot.mcx_options.pricing import realised_vol

    assert realised_vol([]) == 0.0
    assert realised_vol([100.0]) == 0.0
    assert realised_vol([100.0, 101.0]) == 0.0


def test_realised_vol_ignores_non_positive_closes():
    """A zero/None close is missing data, not a -100% return -- taking its log
    would raise or produce a nonsense vol.
    """
    from growmore_bot.mcx_options.pricing import realised_vol

    clean = [100.0, 101.0, 102.0, 101.5, 103.0]
    dirty = [100.0, 101.0, 0.0, 102.0, 101.5, 103.0]

    assert realised_vol(dirty) == pytest.approx(realised_vol(clean))


def test_realised_vol_from_bars_reads_closes_off_bar_objects():
    from growmore_bot.broker.dhan_client import Bar
    from growmore_bot.mcx_options.pricing import realised_vol, realised_vol_from_bars

    closes = [100.0, 102.0, 101.0, 104.0, 103.0]
    bars = [
        Bar(timestamp=None, open=c, high=c, low=c, close=c, volume=1) for c in closes
    ]

    assert realised_vol_from_bars(bars) == pytest.approx(realised_vol(closes))
