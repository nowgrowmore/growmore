"""Tests for growmore_bot.options.strike_selection.

This module was moved here from research/stock_options/wheel_engine.py (which
now imports and re-exports it) so the live wheel-basket engine can use the
same strike-selection logic without growmore_bot importing from research/ --
every other shared-code precedent in this repo runs research -> growmore_bot,
never the reverse. research/stock_options/test_wheel_engine.py's existing 27
tests are the proof this move didn't change behavior; these tests cover the
moved logic directly, at its new home.
"""
from __future__ import annotations

import pandas as pd
import pytest

from growmore_bot.options.strike_selection import (
    RSI_BASIS_BUFFER_TIERS,
    rsi_scaled_basis_buffer,
    select_strike,
)


def _day_chain(strikes=(80, 90, 95, 100, 105, 110, 120), volume=500):
    rows = [
        {"strike": float(k), "opt_type": opt, "settle": 1.0, "volume": volume}
        for k in strikes for opt in ("CE", "PE")
    ]
    return pd.DataFrame(rows)


def test_a_tradeable_strike_is_the_closest_one_to_the_target_otm():
    row = select_strike(_day_chain(), "PE", spot=100.0, target_otm=0.05)
    assert row["strike"] == 95.0


def test_a_strike_with_no_volume_is_never_selected():
    chain = _day_chain()
    chain.loc[chain["strike"] == 95.0, "volume"] = 0
    row = select_strike(chain, "PE", spot=100.0, target_otm=0.05)
    assert row["strike"] != 95.0


def test_the_floor_removes_every_candidate_below_it():
    row = select_strike(_day_chain(), "CE", spot=100.0, target_otm=0.05, floor_strike=110.0)
    assert row["strike"] >= 110.0


def test_the_floor_can_leave_nothing_sellable():
    chain = _day_chain(strikes=(90, 95, 100))
    assert select_strike(chain, "CE", 100.0, 0.05, floor_strike=120.0) is None


def test_rsi_below_every_tier_falls_back_to_zero_buffer():
    assert rsi_scaled_basis_buffer(20.0) == 0.0
    assert rsi_scaled_basis_buffer(None) == 0.0


def test_rsi_in_the_neutral_band_gets_the_middle_tier():
    assert rsi_scaled_basis_buffer(50.0) == pytest.approx(0.02)


def test_rsi_showing_real_momentum_gets_the_full_buffer():
    assert rsi_scaled_basis_buffer(75.0) == pytest.approx(0.05)


def test_tiers_are_ordered_descending_by_threshold():
    thresholds = [t for t, _ in RSI_BASIS_BUFFER_TIERS]
    assert thresholds == sorted(thresholds, reverse=True)
