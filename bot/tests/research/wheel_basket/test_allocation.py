"""Protects the sector-diversification rule the whole study exists to test.

The property under protection is that no sector can quietly take over the
basket. Financial Services is 55 of the 210 F&O names, so an ordering that
merely sorts by IV rank will hand it the majority of a cycle's capital far
more often than chance -- which is precisely the concentration the owner
asked to remove.
"""
from __future__ import annotations

import pytest

from research.wheel_basket.allocation import (
    flat_order,
    max_sector_share,
    round_robin_order,
)

# Three financials outranking everything else -- the exact shape that makes a
# naive IV sort concentrate.
PCTILES = {
    "HDFCBANK": 0.99, "ICICIBANK": 0.98, "SBIN": 0.97,
    "TCS": 0.96, "INFY": 0.80,
    "SUNPHARMA": 0.95,
}
SECTORS = {
    "HDFCBANK": "Financial Services", "ICICIBANK": "Financial Services",
    "SBIN": "Financial Services",
    "TCS": "Information Technology", "INFY": "Information Technology",
    "SUNPHARMA": "Healthcare",
}


def test_flat_order_is_todays_behaviour_pure_iv_rank():
    """B0's ordering, kept as the benchmark every variant is measured against."""
    assert flat_order(PCTILES.keys(), PCTILES) == [
        "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "SUNPHARMA", "INFY",
    ]


def test_round_robin_takes_the_best_of_each_sector_before_any_sector_seconds():
    """The whole point: one name per sector, then second names, and so on."""
    order = round_robin_order(PCTILES.keys(), PCTILES, SECTORS)
    # Pass 1: best of Financials (0.99), Healthcare (0.95), IT (0.96),
    # sectors ordered by their own best candidate.
    assert order[:3] == ["HDFCBANK", "TCS", "SUNPHARMA"]
    # Only then may a sector get a second name.
    assert set(order[3:]) == {"ICICIBANK", "INFY"} | {"SBIN"}
    assert order[3] == "ICICIBANK"  # 0.98, best of the remaining seconds


def test_round_robin_is_a_permutation_never_a_filter():
    """Ordering changes who gets capital FIRST, not who is eligible at all.

    An uncapped round robin that dropped names would silently shrink the
    universe and confound the sector result with a selectivity result.
    """
    order = round_robin_order(PCTILES.keys(), PCTILES, SECTORS)
    assert sorted(order) == sorted(PCTILES)


def test_max_per_sector_truncates_and_is_the_only_thing_that_drops_names():
    order = round_robin_order(PCTILES.keys(), PCTILES, SECTORS, max_per_sector=1)
    assert order == ["HDFCBANK", "TCS", "SUNPHARMA"]
    assert "ICICIBANK" not in order and "SBIN" not in order

    order3 = round_robin_order(PCTILES.keys(), PCTILES, SECTORS, max_per_sector=3)
    assert sorted(order3) == sorted(PCTILES)  # nothing to truncate


def test_a_symbol_with_no_sector_label_gets_its_own_bucket_not_a_shared_one():
    """Unknown sectors must not be pooled into one giant bucket -- that would
    make two unrelated unlabelled names compete as if they were correlated.
    """
    pctiles = {"A": 0.9, "B": 0.8}
    order = round_robin_order(pctiles.keys(), pctiles, {}, max_per_sector=1)
    assert sorted(order) == ["A", "B"]


def test_tiebreak_orders_within_a_sector_but_never_across_sectors():
    """T3's relative-strength tiebreak changes queue order inside a sector
    only. If it could reorder sectors it would be a selection signal, which
    is a different (and untested) claim.
    """
    pctiles = {"X": 0.9, "Y": 0.9, "Z": 0.5}
    sectors = {"X": "Auto", "Y": "Auto", "Z": "Power"}
    order = round_robin_order(pctiles.keys(), pctiles, sectors, tiebreak={"Y": 1.0, "X": 0.0})
    assert order[0] == "Y"          # beats X inside Auto on the tiebreak
    assert order[1] == "Z"          # Power still gets its turn before Auto's second
    assert order[2] == "X"


def test_ordering_is_deterministic_under_dict_reordering():
    """Two runs of the same cycle must allocate identically, or a result is
    not reproducible.
    """
    a = round_robin_order(PCTILES.keys(), PCTILES, SECTORS)
    shuffled = dict(reversed(list(PCTILES.items())))
    b = round_robin_order(shuffled.keys(), shuffled, SECTORS)
    assert a == b


def test_max_sector_share_reports_capital_not_position_count():
    """Positions are unequally sized, so a count-based concentration number
    would understate the real exposure.
    """
    deployed = {"HDFCBANK": 600_000.0, "TCS": 300_000.0, "SUNPHARMA": 100_000.0}
    assert max_sector_share(deployed, SECTORS) == pytest.approx(0.6)


def test_max_sector_share_of_an_empty_book_is_zero_not_an_error():
    assert max_sector_share({}, SECTORS) == 0.0
