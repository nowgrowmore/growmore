"""Tests for growmore_bot.backtest.deflated_sharpe.

The problem this addresses: this repo now runs 24 strategy variants x 8
instruments = 192 backtests and reports the best of them. The maximum Sharpe
of N trials is inflated even when every candidate is pure noise, so the
headline number is not comparable to a Sharpe that was hypothesised once and
tested once. Bailey & Lopez de Prado's Deflated Sharpe Ratio corrects for
exactly that, plus for the non-normal returns a trend-following equity curve
always has.

Reference values below come from the properties of the formulae (the
expected-maximum term, monotonicity in N and T) rather than from this
implementation's own output, and the normal CDF/inverse are checked against
known analytic values.
"""
from __future__ import annotations

import math

import pytest

from growmore_bot.backtest.deflated_sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    norm_cdf,
    norm_ppf,
)


class TestNormalHelpers:
    def test_cdf_at_known_points(self):
        assert norm_cdf(0.0) == pytest.approx(0.5)
        assert norm_cdf(1.959963985) == pytest.approx(0.975, abs=1e-6)
        assert norm_cdf(-1.959963985) == pytest.approx(0.025, abs=1e-6)

    def test_ppf_is_the_inverse_of_cdf(self):
        for p in (0.01, 0.1, 0.5, 0.9, 0.975, 0.999):
            assert norm_cdf(norm_ppf(p)) == pytest.approx(p, abs=1e-7)

    def test_ppf_at_known_points(self):
        assert norm_ppf(0.975) == pytest.approx(1.959963985, abs=1e-6)
        assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)

    def test_ppf_rejects_out_of_range_probabilities(self):
        for bad in (0.0, 1.0, -0.1, 1.1):
            with pytest.raises(ValueError):
                norm_ppf(bad)


class TestExpectedMaxSharpe:
    def test_more_trials_raises_the_bar(self):
        """The core insight: the more combinations you try, the higher the
        best one has to score before it means anything."""
        few = expected_max_sharpe(sharpe_variance=0.25, n_trials=5)
        many = expected_max_sharpe(sharpe_variance=0.25, n_trials=200)
        assert many > few > 0

    def test_a_single_trial_has_no_selection_bias_to_remove(self):
        assert expected_max_sharpe(sharpe_variance=0.25, n_trials=1) == pytest.approx(0.0)

    def test_wider_dispersion_across_trials_raises_the_bar(self):
        narrow = expected_max_sharpe(sharpe_variance=0.01, n_trials=100)
        wide = expected_max_sharpe(sharpe_variance=1.0, n_trials=100)
        assert wide > narrow


class TestDeflatedSharpeRatio:
    def test_a_sharpe_far_above_the_selection_bar_is_significant(self):
        dsr = deflated_sharpe_ratio(
            observed_sharpe=0.20, sharpe_variance=0.0005, n_trials=20, n_observations=1250
        )
        assert dsr > 0.95

    def test_a_sharpe_at_the_selection_bar_is_a_coin_flip(self):
        """When the observed Sharpe is exactly what you'd expect the LUCKIEST
        of N random trials to produce, the probability it reflects real skill
        is ~50%."""
        var = 0.0005
        bar = expected_max_sharpe(sharpe_variance=var, n_trials=20)
        dsr = deflated_sharpe_ratio(
            observed_sharpe=bar, sharpe_variance=var, n_trials=20, n_observations=1250
        )
        assert dsr == pytest.approx(0.5, abs=0.01)

    def test_testing_more_combinations_lowers_the_same_result(self):
        common = dict(observed_sharpe=0.10, sharpe_variance=0.0005, n_observations=1250)
        assert deflated_sharpe_ratio(n_trials=5, **common) > deflated_sharpe_ratio(
            n_trials=500, **common
        )

    def test_a_longer_track_record_raises_confidence(self):
        common = dict(observed_sharpe=0.10, sharpe_variance=0.0005, n_trials=20)
        assert deflated_sharpe_ratio(n_observations=2500, **common) > deflated_sharpe_ratio(
            n_observations=250, **common
        )

    def test_negative_skew_and_fat_tails_reduce_confidence(self):
        """A trend-following equity curve is negatively skewed and fat-tailed;
        both make the same Sharpe less trustworthy, which the standard
        formula ignores and this one does not."""
        common = dict(
            observed_sharpe=0.10, sharpe_variance=0.0005, n_trials=20, n_observations=1250
        )
        normal = deflated_sharpe_ratio(skew=0.0, kurtosis=3.0, **common)
        nasty = deflated_sharpe_ratio(skew=-1.5, kurtosis=8.0, **common)
        assert nasty < normal

    def test_annualised_inputs_must_be_converted_by_the_caller(self):
        """Guard against the easiest misuse: passing an annualised Sharpe
        (e.g. 1.45) with a per-observation variance produces nonsense, so the
        function refuses an implausibly large observed Sharpe."""
        with pytest.raises(ValueError):
            deflated_sharpe_ratio(
                observed_sharpe=1.45, sharpe_variance=0.0005, n_trials=20, n_observations=1250
            )

    def test_requires_at_least_two_observations(self):
        with pytest.raises(ValueError):
            deflated_sharpe_ratio(
                observed_sharpe=0.1, sharpe_variance=0.0005, n_trials=20, n_observations=1
            )


def test_annualisation_helper_round_trips():
    from growmore_bot.backtest.deflated_sharpe import annualised_to_per_observation

    per_obs = annualised_to_per_observation(1.45, periods_per_year=252)
    assert per_obs == pytest.approx(1.45 / math.sqrt(252))
