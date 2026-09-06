"""Does a stock's rank in one period say anything about its rank in the next?

This is the guardrail on the per-stock leaderboard. 210 stocks x 6 strategies
ranked by CAGR means the top of the list is partly luck, and each stock has
only ~84 monthly cycles behind it. A split-half rank correlation near zero
means the ordering is noise and top-picking will fail live; meaningfully
positive means per-stock selection is real and actionable.

The null matters as much as the statistic, so it is tested directly: a
deliberately random ranking must come back indistinguishable from zero.
"""
from __future__ import annotations

import random

import pytest

from research.stock_options.rank_stability import spearman


SIX = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0, "F": 6.0}


def test_an_identical_ranking_correlates_perfectly():
    assert spearman(SIX, dict(SIX)) == pytest.approx(1.0)


def test_a_reversed_ranking_correlates_perfectly_negatively():
    reversed_ = {k: 7.0 - v for k, v in SIX.items()}
    assert spearman(SIX, reversed_) == pytest.approx(-1.0)


def test_it_ranks_rather_than_correlating_the_raw_values():
    # Spearman must be invariant to any monotone transform -- squaring the
    # second period cannot change the ordering, so it cannot change rho.
    squared = {k: v * v for k, v in SIX.items()}
    assert spearman(SIX, squared) == pytest.approx(1.0)


def test_a_random_ranking_is_indistinguishable_from_zero():
    """The null the real measurement is read against."""
    random.seed(20260906)
    rhos = []
    for _ in range(200):
        keys = [f"S{i}" for i in range(60)]
        a = {k: random.random() for k in keys}
        b = {k: random.random() for k in keys}
        rhos.append(spearman(a, b))
    mean = sum(rhos) / len(rhos)
    assert abs(mean) < 0.05


def test_only_stocks_present_in_both_periods_are_compared():
    # A stock that entered F&O midway has no first-half rank; including it
    # with a fabricated one would manufacture correlation.
    first = dict(SIX)                      # A..F
    second = {k: v for k, v in SIX.items() if k != "A"}
    second["Z"] = 99.0                     # entered F&O midway; no first-half rank
    assert spearman(first, second) == pytest.approx(1.0)


def test_too_few_overlapping_stocks_yields_no_answer():
    # Below MIN_OVERLAP a correlation is arithmetic, not evidence.
    four = {k: v for k, v in SIX.items() if k in ("A", "B", "C", "D")}
    assert spearman(four, dict(four)) is None
    assert spearman({}, {}) is None


def test_ties_do_not_crash_it():
    first = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 2.0, "E": 2.0, "F": 3.0}
    second = {"A": 5.0, "B": 5.0, "C": 5.0, "D": 9.0, "E": 9.0, "F": 12.0}
    rho = spearman(first, second)
    assert rho is not None and -1.0 <= rho <= 1.0
