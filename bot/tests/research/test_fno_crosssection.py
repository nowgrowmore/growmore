"""Tests for research.fno.crosssection -- how 210 per-stock results become
ONE statement about a config.

The trap this module exists to avoid: 210 stocks x 3 configs is 630 runs,
and the luckiest of 630 posts a Sharpe near 2.5 on noise alone. Phase 3's
rule -- "a best-of-45 at +0.24 against a mean of -0.255 is what noise looks
like, and it should not be pursued" -- applies directly.

So the unit of evidence is the CONFIG, not the stock, and the headline is a
paired comparison of each config against each stock's OWN buy-and-hold over
identical bars. That is `oos_pairs.py`'s matched-pair discipline generalised
across stocks instead of across ideas: the two members of a pair differ by
exactly one decision, so the difference is attributable and nothing is
cherry-picked from a ranking.
"""
from __future__ import annotations

import pytest

from research.fno.crosssection import CrossSection, summarise_pairs


def _pairs(deltas, benchmarks=None):
    """(symbol, treatment_sharpe, benchmark_sharpe) triples from deltas."""
    benchmarks = benchmarks if benchmarks is not None else [1.0] * len(deltas)
    return [
        (f"S{i}", b + d, b) for i, (d, b) in enumerate(zip(deltas, benchmarks))
    ]


def test_the_headline_is_the_paired_difference_not_the_best_stock():
    # Nine stocks slightly worse, one spectacular. The mean must report the
    # truth (negative), not be rescued by the outlier.
    result = summarise_pairs(_pairs([-0.2] * 9 + [3.0]))
    assert isinstance(result, CrossSection)
    assert result.mean_delta == pytest.approx(0.12)
    assert result.median_delta == pytest.approx(-0.2)
    # 1 of 10 beat its own benchmark -- the number that actually matters.
    assert result.win_rate == pytest.approx(0.1)


def test_the_median_and_win_rate_resist_a_single_outlier():
    without = summarise_pairs(_pairs([-0.2] * 9 + [0.1]))
    with_outlier = summarise_pairs(_pairs([-0.2] * 9 + [30.0]))
    assert without.median_delta == with_outlier.median_delta
    assert without.win_rate == with_outlier.win_rate
    assert with_outlier.mean_delta > without.mean_delta


def test_a_sign_test_is_reported_on_the_paired_differences():
    # 9 of 10 negative is unlikely by chance; the two-sided p must be small.
    losing = summarise_pairs(_pairs([-0.2] * 9 + [0.1]))
    assert losing.n == 10
    assert losing.wins == 1
    assert losing.sign_test_p < 0.05
    # An even split is exactly what noise looks like.
    even = summarise_pairs(_pairs([0.2] * 5 + [-0.2] * 5))
    assert even.sign_test_p == pytest.approx(1.0)


def test_ties_are_excluded_from_the_sign_test_rather_than_counted_as_wins():
    # A stock the config never traded has delta exactly 0. Counting those as
    # wins would manufacture significance out of inactivity.
    result = summarise_pairs(_pairs([0.0] * 8 + [0.3, 0.4]))
    assert result.n == 10
    assert result.effective_n == 2
    assert result.wins == 2
    assert result.sign_test_p == pytest.approx(0.5)


def test_the_interquartile_range_is_reported_because_the_mean_alone_misleads():
    result = summarise_pairs(_pairs([-1.0, -0.5, 0.0, 0.5, 1.0]))
    assert result.q1_delta < result.median_delta < result.q3_delta


def test_an_empty_universe_does_not_crash_and_claims_nothing():
    result = summarise_pairs([])
    assert result.n == 0
    assert result.sign_test_p == 1.0
    assert result.mean_delta == 0.0
