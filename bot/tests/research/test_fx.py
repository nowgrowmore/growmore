"""Tests for research.currency.fx -- parsing and the forward fill.

The forward fill is the only place this module can silently lie, and it can
lie in the one direction that matters: filling a gap from a LATER observation
would hand the backtest a rate the trading day did not have.
"""
from __future__ import annotations

from datetime import date

from research.currency import fx

CSV = """observation_date,DEXINUS
2021-08-02,74.3500
2021-08-03,.
2021-08-04,74.2500
"""


def test_parse_skips_the_dot_rows_fred_uses_for_non_business_days():
    rates = fx.parse(CSV)
    assert rates == {date(2021, 8, 2): 74.35, date(2021, 8, 4): 74.25}


def test_parse_tolerates_an_empty_series():
    assert fx.parse("observation_date,DEXINUS\n") == {}


def test_forward_fill_carries_the_last_known_rate_over_a_gap():
    rates = {date(2021, 8, 2): 74.35, date(2021, 8, 6): 74.50}
    filled = fx.forward_fill(rates, days=[date(2021, 8, d) for d in (2, 3, 4, 5, 6)])
    assert filled[date(2021, 8, 3)] == 74.35
    assert filled[date(2021, 8, 5)] == 74.35
    assert filled[date(2021, 8, 6)] == 74.50


def test_forward_fill_never_reaches_backward_for_a_rate():
    """A day before the first observation has no honest value, so it gets none."""
    rates = {date(2021, 8, 10): 74.35}
    filled = fx.forward_fill(rates, days=[date(2021, 8, 1), date(2021, 8, 10)])
    assert date(2021, 8, 1) not in filled
    assert filled[date(2021, 8, 10)] == 74.35


def test_a_gap_longer_than_the_limit_is_left_unfilled():
    rates = {date(2021, 1, 1): 74.0}
    filled = fx.forward_fill(rates, days=[date(2021, 3, 1)], max_gap=10)
    assert date(2021, 3, 1) not in filled


def test_annualised_depreciation_matches_a_hand_computed_case():
    rates = {date(2021, 1, 1): 100.0, date(2022, 1, 1): 110.0}
    assert round(fx.annualised_depreciation_pct(rates), 2) == 10.01


def test_annualised_depreciation_is_zero_for_a_degenerate_series():
    assert fx.annualised_depreciation_pct({}) == 0.0
    assert fx.annualised_depreciation_pct({date(2021, 1, 1): 74.0}) == 0.0
