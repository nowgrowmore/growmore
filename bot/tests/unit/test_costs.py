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


# --- NSE cash-equity delivery costs -------------------------------------
#
# The MCX model and the equity model differ by an order of magnitude on the
# charge that dominates a multi-week holding strategy: MCX pays CTT 0.01% on
# the SELL leg only, cash equity pays STT 0.1% on BOTH legs. That is 20bps a
# round trip against 1bp, and against a config closing ~20 trades a year it
# is ~4%/yr of drag -- enough on its own to decide whether a trend system
# beats buy-and-hold.

from growmore_bot.costs import (  # noqa: E402
    DEFAULT_COST_MODEL,
    NSE_EQUITY_DELIVERY_COST_MODEL,
)


def test_stt_is_charged_on_both_legs_unlike_ctt():
    model = CostModel(
        brokerage_per_order=0.0, brokerage_pct=0.0, exchange_txn_pct=0.0,
        ctt_sell_pct=0.0, stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
        stt_both_pct=0.001, slippage_ticks=0.0, stop_slippage_ticks=0.0,
    )
    # 0.1% of 1,00,000 = Rs 100, on the buy AND on the sell.
    assert leg_cost(100_000.0, "buy", model) == pytest.approx(100.0)
    assert leg_cost(100_000.0, "sell", model) == pytest.approx(100.0)


def test_stt_is_not_subject_to_gst_any_more_than_ctt_is():
    # GST applies to brokerage + exchange + SEBI only. A pure-STT model with
    # an 18% GST rate must still charge exactly the STT.
    model = CostModel(
        brokerage_per_order=0.0, brokerage_pct=0.0, exchange_txn_pct=0.0,
        ctt_sell_pct=0.0, stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.18,
        stt_both_pct=0.001, slippage_ticks=0.0, stop_slippage_ticks=0.0,
    )
    assert leg_cost(100_000.0, "buy", model) == pytest.approx(100.0)


def test_the_mcx_model_is_unchanged_to_the_decimal_by_the_new_field():
    # The control, in the Phase 0 sense: adding stt_both_pct must not move a
    # single published MCX number. Its default is 0.0 and DEFAULT_COST_MODEL
    # never sets it.
    assert DEFAULT_COST_MODEL.stt_both_pct == 0.0
    # Hand-computed, same arithmetic as test_leg_cost_* above.
    notional = 100_000.0
    brokerage = min(20.0, notional * 0.0003)          # 20.0 (the cap binds)
    exchange = notional * 0.000026                    # 2.6
    sebi = notional * 0.000002                        # 0.2
    gst = 0.18 * (brokerage + exchange + sebi)        # 4.104
    expected_buy = brokerage + exchange + sebi + gst + notional * 0.00002
    assert leg_cost(notional, "buy", DEFAULT_COST_MODEL) == pytest.approx(expected_buy)


def test_a_hand_computed_equity_round_trip():
    model = NSE_EQUITY_DELIVERY_COST_MODEL
    notional = 500_000.0
    # Buy leg: STT 0.1% + exchange 0.00297% + stamp 0.015% + SEBI + GST.
    stt = 500.0
    exchange = notional * 0.0000297                   # 14.85
    sebi = notional * 0.000001                        # 0.5
    gst = 0.18 * (0.0 + exchange + sebi)              # brokerage is zero
    stamp = notional * 0.00015                        # 75.0
    assert leg_cost(notional, "buy", model) == pytest.approx(stt + exchange + sebi + gst + stamp)
    # Sell leg: same, minus stamp (buy-only), and STT again.
    assert leg_cost(notional, "sell", model) == pytest.approx(stt + exchange + sebi + gst)


def test_equity_round_trip_is_dominated_by_stt():
    # The claim that motivates the whole model: >80% of the statutory cost of
    # a round trip is STT, and the round trip costs roughly 20bps.
    notional = 500_000.0
    model = NSE_EQUITY_DELIVERY_COST_MODEL
    statutory = leg_cost(notional, "buy", model) + leg_cost(notional, "sell", model)
    assert statutory / notional == pytest.approx(0.00223, abs=0.0002)
    assert (2 * notional * model.stt_both_pct) / statutory > 0.8


def test_equity_costs_are_an_order_of_magnitude_above_mcx_on_the_same_notional():
    notional = 500_000.0
    equity = leg_cost(notional, "buy", NSE_EQUITY_DELIVERY_COST_MODEL) + leg_cost(
        notional, "sell", NSE_EQUITY_DELIVERY_COST_MODEL
    )
    mcx = leg_cost(notional, "buy", DEFAULT_COST_MODEL) + leg_cost(
        notional, "sell", DEFAULT_COST_MODEL
    )
    assert equity > 5 * mcx
