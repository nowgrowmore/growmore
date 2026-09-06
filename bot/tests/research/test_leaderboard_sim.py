"""Simulating the decision a ranked leaderboard invites you to make.

The property that matters is the anti-lookahead one: selection may use only
years strictly BEFORE the year being scored. A simulation that ranks on the
same year it measures will always look brilliant and mean nothing.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from research.stock_options.leaderboard_sim import simulate, yearly_returns


def _result(days, equity):
    return SimpleNamespace(days=days, equity_curve=equity)


def test_yearly_returns_slice_the_curve_by_calendar_year():
    days, equity, value = [], [], 100.0
    start = date(2021, 1, 1)
    for i in range(600):
        days.append(start + timedelta(days=i))
        equity.append(value)
        value *= 1.001
    got = yearly_returns(_result(days, equity))
    assert set(got) == {2021, 2022}
    assert all(v > 0 for v in got.values())


def test_a_year_with_too_few_days_is_not_scored():
    days = [date(2021, 12, 1) + timedelta(days=i) for i in range(20)]
    assert yearly_returns(_result(days, [100.0] * 20)) == {}


def test_a_mismatched_curve_is_refused_rather_than_zipped_short():
    assert yearly_returns(_result([date(2021, 1, 1)], [1.0, 2.0])) == {}


def test_selection_never_sees_the_year_it_is_scored_on():
    """The anti-lookahead property, tested by making lookahead pay hugely.

    WINNER is terrible until 2023 and spectacular in 2023. A simulation that
    ranked on 2023 itself would pick it for 2023; one that ranks only on
    2021-2022 cannot, and must therefore NOT show the 2023 windfall.
    """
    universe = {f"S{i}": {2021: 0.10, 2022: 0.10, 2023: 0.10} for i in range(10)}
    universe["WINNER"] = {2021: -0.50, 2022: -0.50, 2023: 5.00}
    rows = simulate({"A": universe}, top_n=1)
    by_year = {r["year"]: r for r in rows}
    # 2023 picks on 2021-2022 trailing, where WINNER is worst -- so top-1 is
    # an ordinary stock and the return is the ordinary 10%.
    assert by_year[2023]["top_n_return_pct"] == pytest.approx(10.0, abs=0.01)


def test_a_genuinely_persistent_stock_is_found_and_shows_positive_edge():
    universe = {f"S{i}": {2021: 0.05, 2022: 0.05, 2023: 0.05} for i in range(10)}
    universe["STEADY"] = {2021: 0.50, 2022: 0.50, 2023: 0.50}
    rows = simulate({"A": universe}, top_n=1)
    assert all(r["edge_pct"] > 0 for r in rows)


def test_a_universe_too_small_to_rank_is_skipped():
    tiny = {f"S{i}": {2021: 0.1, 2022: 0.1} for i in range(3)}
    assert simulate({"A": tiny}, top_n=10) == []


def test_the_first_year_is_never_scored_because_nothing_precedes_it():
    universe = {f"S{i}": {2021: 0.1, 2022: 0.2, 2023: 0.3} for i in range(20)}
    years = {r["year"] for r in simulate({"A": universe}, top_n=5)}
    assert 2021 not in years
    assert years == {2022, 2023}
