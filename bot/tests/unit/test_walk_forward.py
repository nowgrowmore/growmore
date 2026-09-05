"""Tests for growmore_bot.backtest.walk_forward.

The whole value of this module is that a test bar is never visible to the
selection that decides what to trade in it. Almost every bug a walk-forward
harness can have is a leak, so that is what these tests are about.
"""
from __future__ import annotations

import pytest

from growmore_bot.backtest.walk_forward import Fold, grid_hash, make_folds


def test_folds_tile_the_series_without_gaps_or_overlaps():
    folds = make_folds(n_bars=1000, train=504, test=126, step=126)
    assert folds[0] == Fold(index=0, train_start=0, train_end=504, test_start=504, test_end=630)
    for a, b in zip(folds, folds[1:]):
        assert b.test_start == a.test_end, "out-of-sample segments must be contiguous"
        assert b.train_end == b.test_start, "training must stop exactly where testing starts"


def test_no_test_bar_is_ever_inside_any_training_window_of_its_own_fold():
    for fold in make_folds(n_bars=1300, train=504, test=126, step=126):
        train = range(fold.train_start, fold.train_end)
        test = range(fold.test_start, fold.test_end)
        assert not set(train) & set(test)
        assert min(test) >= max(train) + 1


def test_the_last_partial_window_is_dropped_rather_than_shortened():
    """A half-length final fold is not comparable to the others and would
    quietly weight the most recent regime differently."""
    folds = make_folds(n_bars=700, train=504, test=126, step=126)
    assert len(folds) == 1
    assert folds[-1].test_end == 630
    assert folds[-1].test_end <= 700


def test_a_series_too_short_for_even_one_fold_yields_nothing():
    assert make_folds(n_bars=600, train=504, test=126, step=126) == []
    assert make_folds(n_bars=0, train=504, test=126, step=126) == []


def test_gold_and_silver_have_enough_history_for_five_folds():
    """Guards the claim made in the writeup: ~1,210 daily bars is five
    six-month out-of-sample segments, not the six the plan assumed."""
    assert len(make_folds(n_bars=1207, train=504, test=126, step=126)) == 5
    assert len(make_folds(n_bars=1214, train=504, test=126, step=126)) == 5


def test_the_expanding_window_keeps_every_bar_from_the_start():
    folds = make_folds(n_bars=1000, train=504, test=126, step=126, anchored=True)
    assert all(f.train_start == 0 for f in folds)
    assert folds[1].train_end > folds[0].train_end


def test_a_rolling_window_is_the_default_and_drops_old_bars():
    folds = make_folds(n_bars=1000, train=504, test=126, step=126)
    assert folds[1].train_start == 126
    assert all(f.train_end - f.train_start == 504 for f in folds)


def test_bad_geometry_is_rejected_rather_than_silently_producing_a_leak():
    with pytest.raises(ValueError):
        make_folds(n_bars=1000, train=0, test=126, step=126)
    with pytest.raises(ValueError):
        make_folds(n_bars=1000, train=504, test=0, step=126)
    with pytest.raises(ValueError):
        make_folds(n_bars=1000, train=504, test=126, step=0)


def test_grid_hash_is_stable_across_key_order_but_not_across_content():
    a = [("macd_trend", {"fast_period": 5, "slow_period": 13})]
    reordered = [("macd_trend", {"slow_period": 13, "fast_period": 5})]
    changed = [("macd_trend", {"fast_period": 8, "slow_period": 13})]

    assert grid_hash(a) == grid_hash(reordered)
    assert grid_hash(a) != grid_hash(changed)
    assert len(grid_hash(a)) == 12


def test_grid_hash_notices_a_variant_being_added_or_removed():
    base = [("macd_trend", {"fast_period": 5})]
    assert grid_hash(base) != grid_hash(base + [("sma_crossover", {"fast_period": 5})])
