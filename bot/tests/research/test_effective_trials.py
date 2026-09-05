"""Tests for research.validation.deflate_sweep.effective_trials.

This number is the whole deflated-Sharpe calculation: it decides how high the
"could this just be the luckiest of N tries?" bar sits. Getting it wrong in
either direction makes the answer worthless -- too low and everything looks
significant, too high and nothing does. It earned its own tests after a first
version returned 1 for a 192-run sweep (a column filter dropped every series
whose date range didn't span the full union), which set the bar to zero and
declared all 192 results significant.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.validation.effective_trials import effective_trials


def _series(n=400, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.01, n)


def test_independent_series_count_as_roughly_that_many_trials():
    df = pd.DataFrame({f"r{i}": _series(seed=i) for i in range(8)})
    participation, clusters = effective_trials(df)
    assert participation == pytest.approx(8.0, abs=1.5)
    assert clusters == 8


def test_perfectly_duplicated_series_count_as_one_trial():
    """Re-running the same sweep leaves identical copies behind. Counting
    them would inflate the trial count while collapsing the correlation
    structure -- wrong in both directions at once."""
    base = _series(seed=1)
    df = pd.DataFrame({f"r{i}": base for i in range(6)})
    participation, clusters = effective_trials(df)
    assert participation == pytest.approx(1.0, abs=0.01)
    assert clusters == 1


def test_two_correlated_blocks_count_as_about_two_trials():
    """The realistic shape here: parameter variants within a family, and
    instruments within a correlated block, are not independent bets."""
    a, b = _series(seed=2), _series(seed=3)
    df = pd.DataFrame(
        {**{f"a{i}": a + _series(seed=100 + i) * 0.05 for i in range(4)},
         **{f"b{i}": b + _series(seed=200 + i) * 0.05 for i in range(4)}}
    )
    participation, clusters = effective_trials(df)
    assert 1.5 <= participation <= 3.5
    assert clusters == 2


def test_series_covering_different_date_ranges_are_still_counted():
    """Regression: instruments have genuinely different listing histories
    (Crude Oil Mini has ~898 bars against Gold Mini's ~1,260), so requiring
    every column to span the full union threw away nearly the whole sweep."""
    df = pd.DataFrame({f"r{i}": _series(seed=i) for i in range(6)})
    df.loc[:150, "r0"] = np.nan      # a late-listing instrument
    df.loc[300:, "r1"] = np.nan      # one that stopped early
    participation, clusters = effective_trials(df)
    assert participation > 3.0
    assert clusters == 6


def test_a_constant_series_does_not_produce_a_nan_correlation():
    """A strategy that never traded has a flat equity curve and zero
    variance; correlating it yields NaN, which would poison the eigenvalues."""
    df = pd.DataFrame({"flat": np.zeros(400), **{f"r{i}": _series(seed=i) for i in range(3)}})
    participation, clusters = effective_trials(df)
    assert not np.isnan(participation)
    assert clusters >= 1


def test_a_single_series_is_a_single_trial():
    assert effective_trials(pd.DataFrame({"r0": _series()})) == (1.0, 1)


def test_an_empty_frame_is_handled():
    assert effective_trials(pd.DataFrame()) == (1.0, 1)
