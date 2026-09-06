"""Tests for growmore_bot.wheel_basket.scoring.

The score is exactly the validated selection metric from
docs/stock-options-results.md Sec 7.1: cross-sectional IV percentile rank.
RSI/MACD are NOT blended into the score -- only the IV-rank restriction was
backtested and shown to work (the cross-experiment showed a D-trailing-return
proxy actually LOSES to the field), so inventing a multi-factor formula here
would not be traceable to any result.
"""
from __future__ import annotations

from growmore_bot.wheel_basket.scoring import (
    eligible_candidates,
    iv_percentile_ranks,
    reason_for,
    should_rotate,
)


def test_iv_percentile_ranks_orders_lowest_to_highest():
    ivs = {"A": 0.20, "B": 0.40, "C": 0.60, "D": 0.80}
    ranks = iv_percentile_ranks(ivs)
    assert ranks["A"] < ranks["B"] < ranks["C"] < ranks["D"]
    assert ranks["A"] == 0.0  # nothing below the lowest


def test_iv_percentile_ranks_ties_share_the_same_rank():
    ivs = {"A": 0.30, "B": 0.30, "C": 0.60}
    ranks = iv_percentile_ranks(ivs)
    assert ranks["A"] == ranks["B"]
    assert ranks["C"] > ranks["A"]


def test_iv_percentile_ranks_empty_input_is_empty_output():
    assert iv_percentile_ranks({}) == {}


def test_eligible_candidates_top_fraction_of_the_ranking():
    # 10 symbols, evenly spread IV -> top 20% should be the 2 highest.
    ivs = {f"S{i}": float(i) for i in range(10)}
    ranks = iv_percentile_ranks(ivs)
    eligible = eligible_candidates(ranks, top_frac=0.2)
    assert eligible == {"S8", "S9"}


def test_eligible_candidates_top_half():
    ivs = {f"S{i}": float(i) for i in range(10)}
    ranks = iv_percentile_ranks(ivs)
    eligible = eligible_candidates(ranks, top_frac=0.5)
    assert len(eligible) >= 5
    assert "S9" in eligible and "S0" not in eligible


def test_eligible_candidates_empty_when_no_percentiles():
    assert eligible_candidates({}, top_frac=0.33) == set()


def test_reason_for_includes_the_selection_metric_and_verdict():
    reason = reason_for("RELIANCE", iv_percentile=0.85, rsi=62.0, macd_bullish=True, selected=True)
    assert "RELIANCE" in reason
    assert "85%" in reason
    assert "62" in reason
    assert "selected" in reason and "not selected" not in reason


def test_reason_for_rejected_candidate_says_so():
    reason = reason_for("XYZ", iv_percentile=0.10, rsi=None, macd_bullish=None, selected=False)
    assert "not selected" in reason


def test_should_rotate_only_when_the_challenger_clears_the_hysteresis_margin():
    # 10% hysteresis: a challenger scoring 1.05x the incumbent is not enough.
    assert should_rotate(current_score=0.50, best_candidate_score=0.525, hysteresis_pct=0.10) is False
    # But clearing it by more than 10% does trigger a rotation.
    assert should_rotate(current_score=0.50, best_candidate_score=0.60, hysteresis_pct=0.10) is True


def test_should_rotate_false_when_candidate_is_worse():
    assert should_rotate(current_score=0.60, best_candidate_score=0.40, hysteresis_pct=0.10) is False
