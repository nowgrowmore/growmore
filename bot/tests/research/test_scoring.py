"""Tests for research.smallcap_momentum.scoring -- pure functions, every
case hand-computed. See scoring.py's module docstring for the documented
simplifications relative to NSE's own (not fully public) exact methodology.
"""
from __future__ import annotations

import math

import pytest

from research.smallcap_momentum.scoring import (
    composite_score,
    momentum_score,
    quality_score,
    zscore_cross_sectionally,
)


class TestMomentumScore:
    def test_hand_computed_positive_momentum(self):
        # (0.5*0.20 + 0.5*0.40) / 0.30 = 0.30 / 0.30 = 1.0
        result = momentum_score(returns_6m=0.20, returns_12m=0.40, annualized_vol=0.30)
        assert result == pytest.approx(1.0)

    def test_hand_computed_negative_momentum(self):
        # (0.5*-0.10 + 0.5*-0.10) / 0.20 = -0.10 / 0.20 = -0.5
        result = momentum_score(returns_6m=-0.10, returns_12m=-0.10, annualized_vol=0.20)
        assert result == pytest.approx(-0.5)

    def test_zero_volatility_returns_none_rather_than_dividing_by_zero(self):
        assert momentum_score(returns_6m=0.10, returns_12m=0.10, annualized_vol=0.0) is None


class TestQualityScore:
    def test_hand_computed(self):
        # Weights: 0.4*roe + 0.3*(-debt_to_equity) + 0.3*eps_growth, per
        # scoring.py's documented composite.
        # 0.4*0.20 + 0.3*(-0.5) + 0.3*0.15 = 0.08 - 0.15 + 0.045 = -0.025
        result = quality_score(roe=0.20, debt_to_equity=0.5, eps_growth=0.15)
        assert result == pytest.approx(-0.025)

    def test_higher_roe_is_better_all_else_equal(self):
        low = quality_score(roe=0.05, debt_to_equity=0.3, eps_growth=0.10)
        high = quality_score(roe=0.25, debt_to_equity=0.3, eps_growth=0.10)
        assert high > low

    def test_lower_debt_to_equity_is_better_all_else_equal(self):
        high_debt = quality_score(roe=0.15, debt_to_equity=2.0, eps_growth=0.10)
        low_debt = quality_score(roe=0.15, debt_to_equity=0.2, eps_growth=0.10)
        assert low_debt > high_debt


class TestZscoreCrossSectionally:
    def test_hand_computed(self):
        # mean=3, population stdev = sqrt(((1-3)^2+(2-3)^2+(3-3)^2+(4-3)^2+(5-3)^2)/5) = sqrt(2) ≈ 1.4142
        result = zscore_cross_sectionally([1.0, 2.0, 3.0, 4.0, 5.0])
        expected_std = math.sqrt(2.0)
        assert result == pytest.approx([(v - 3.0) / expected_std for v in [1.0, 2.0, 3.0, 4.0, 5.0]])

    def test_constant_input_returns_zeros_rather_than_dividing_by_zero(self):
        assert zscore_cross_sectionally([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]

    def test_empty_input_returns_empty(self):
        assert zscore_cross_sectionally([]) == []


class TestCompositeScore:
    def test_default_equal_weight(self):
        assert composite_score(momentum_z=1.0, quality_z=0.5) == pytest.approx(0.75)

    def test_momentum_only_when_quality_z_is_none(self):
        # A stock with no usable fundamentals still gets ranked, on
        # momentum alone -- see fundamentals.py's "missing data is explicit,
        # not papered over" convention; this is the scoring-side half of
        # that (excluded from the quality *blend*, not from the universe).
        assert composite_score(momentum_z=1.2, quality_z=None) == pytest.approx(1.2)

    def test_custom_quality_weight(self):
        result = composite_score(momentum_z=1.0, quality_z=0.0, quality_weight=0.25)
        # 0.75*1.0 + 0.25*0.0 = 0.75
        assert result == pytest.approx(0.75)
