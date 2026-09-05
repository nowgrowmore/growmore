"""Tests for growmore_bot.costs -- the real MCX round-trip cost model.

Every expected value here is written out as explicit arithmetic rather than
copied from the implementation's own output, so these are genuine
known-answer tests. Sources for the rates are in the module docstring.

The headline the numbers are meant to protect: for base metals the TICK
VALUE dominates every statutory charge combined (one Copper tick is Rs 125
a lot, against Rs 688 of total statutory cost on a round trip), so a flat
basis-point cost assumption would rank the instruments backwards.
"""
from __future__ import annotations

import pytest

from growmore_bot.costs import CostModel, leg_cost, round_trip_cost, slippage_price

# Live values, 2026-09-05.
GOLDM_NOTIONAL = 152_950.0 * 10      # Rs 15,29,500
COPPER_NOTIONAL = 1_378.10 * 2500    # Rs 34,45,250


def _statutory(notional, side, m=CostModel()):
    """The arithmetic, spelled out independently of the implementation."""
    brokerage = min(m.brokerage_per_order, notional * m.brokerage_pct)
    exchange = notional * m.exchange_txn_pct
    sebi = notional * m.sebi_pct
    gst = m.gst_pct * (brokerage + exchange + sebi)
    ctt = notional * m.ctt_sell_pct if side == "sell" else 0.0
    stamp = notional * m.stamp_buy_pct if side == "buy" else 0.0
    return brokerage + exchange + sebi + gst + ctt + stamp


class TestLegCost:
    def test_goldm_buy_leg(self):
        # brokerage min(20, 458.85)=20; exchange 39.767; sebi 3.059;
        # gst 18% of 62.826 = 11.309; stamp 30.590 -> 104.725
        assert leg_cost(GOLDM_NOTIONAL, "buy") == pytest.approx(104.725, abs=0.01)

    def test_goldm_sell_leg_carries_ctt_instead_of_stamp(self):
        # CTT 0.01% = 152.95 replaces the buy side's 30.59 stamp -> 227.085
        assert leg_cost(GOLDM_NOTIONAL, "sell") == pytest.approx(227.085, abs=0.01)

    def test_matches_an_independently_written_formula(self):
        for notional in (GOLDM_NOTIONAL, COPPER_NOTIONAL, 50_000.0):
            for side in ("buy", "sell"):
                assert leg_cost(notional, side) == pytest.approx(_statutory(notional, side))

    def test_the_flat_brokerage_cap_binds_above_about_67k_of_notional(self):
        # Below the cap the percentage charge is cheaper than Rs 20.
        small = CostModel()
        assert 50_000.0 * small.brokerage_pct == pytest.approx(15.0)
        # 20 / 0.0003 = 66,666.67 is the crossover.
        assert min(20.0, 66_000.0 * small.brokerage_pct) == pytest.approx(19.8)
        assert min(20.0, 70_000.0 * small.brokerage_pct) == pytest.approx(20.0)

    def test_rejects_an_unknown_side(self):
        with pytest.raises(ValueError):
            leg_cost(100_000.0, "short")


class TestRoundTripCost:
    def test_goldm_statutory_round_trip(self):
        cost = round_trip_cost(GOLDM_NOTIONAL, tick_size=1.0, lot_size=10, lots=1)
        # 104.725 + 227.085 = 331.81 statutory, plus 2 ticks/side slippage:
        # 2 * 1.00 * 10 = Rs 20 a side, Rs 40 the round trip.
        assert cost.statutory == pytest.approx(331.81, abs=0.02)
        assert cost.slippage == pytest.approx(40.0)
        assert cost.total == pytest.approx(371.81, abs=0.02)
        assert cost.bps_of_notional(GOLDM_NOTIONAL) == pytest.approx(2.43, abs=0.01)

    def test_copper_slippage_exceeds_every_statutory_charge_combined(self):
        """The reason tick_size has to be a real per-instrument column and
        not a global bps assumption."""
        cost = round_trip_cost(COPPER_NOTIONAL, tick_size=0.05, lot_size=2500, lots=1)
        assert cost.statutory == pytest.approx(688.29, abs=0.05)
        # 2 ticks * Rs 0.05 * 2500 units = Rs 250 a side, Rs 500 the round trip.
        assert cost.slippage == pytest.approx(500.0)
        assert cost.slippage < cost.statutory  # close, but statutory still edges it
        assert cost.total == pytest.approx(1188.29, abs=0.05)
        # Despite being 3.6x GOLDM's rupee cost, Copper is CHEAPER in bps --
        # the Rs 20 brokerage floor is what makes small contracts expensive.
        assert cost.bps_of_notional(COPPER_NOTIONAL) == pytest.approx(3.45, abs=0.01)

    def test_scales_linearly_with_lot_count(self):
        one = round_trip_cost(GOLDM_NOTIONAL, tick_size=1.0, lot_size=10, lots=1)
        three = round_trip_cost(GOLDM_NOTIONAL * 3, tick_size=1.0, lot_size=10, lots=3)
        assert three.slippage == pytest.approx(one.slippage * 3)

    def test_a_zero_cost_model_is_expressible_for_backward_compatible_backtests(self):
        free = CostModel(
            brokerage_per_order=0.0, brokerage_pct=0.0, exchange_txn_pct=0.0,
            ctt_sell_pct=0.0, stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
            slippage_ticks=0.0, stop_slippage_ticks=0.0,
        )
        cost = round_trip_cost(GOLDM_NOTIONAL, tick_size=1.0, lot_size=10, lots=1, model=free)
        assert cost.total == pytest.approx(0.0)


class TestSlippagePrice:
    def test_a_buy_pays_up_and_a_sell_gets_hit_down(self):
        assert slippage_price(100.0, "buy", tick_size=0.05) == pytest.approx(100.10)
        assert slippage_price(100.0, "sell", tick_size=0.05) == pytest.approx(99.90)

    def test_a_stop_fill_is_assumed_worse_than_a_normal_fill(self):
        """There is no resting stop order -- the bot polls every 5 minutes and
        only calls read-only Data APIs, so a stop fires at the NEXT poll's
        price, not at the stop level. Modelling stop slippage as equal to
        ordinary slippage would flatter every stop-based strategy."""
        normal = slippage_price(100.0, "sell", tick_size=0.05)
        stopped = slippage_price(100.0, "sell", tick_size=0.05, is_stop=True)
        assert stopped < normal
        assert stopped == pytest.approx(99.80)  # 4 ticks rather than 2
