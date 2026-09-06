"""NSE stock-option transaction costs.

Options are taxed on a different base from everything else this repo models,
and getting the base wrong is the error most likely to go unnoticed:

  * **STT is charged on the PREMIUM, sell side only** -- 0.1% of premium, not
    of strike x lot. `leg_cost` takes one `notional` argument and the caller
    decides what it means; passing strike-notional where premium is meant
    overstates the charge by 50-100x on an out-of-the-money monthly.
  * **Physical settlement is an equity delivery trade.** Since October 2019
    an ITM stock option delivers real shares, and NSE charges that leg at
    equity delivery rates on `strike x qty` -- which is exactly what
    `NSE_EQUITY_DELIVERY_COST_MODEL` already computes. So assignment reuses
    the equity model rather than inventing an exercise-STT field.

The control that matters: adding a sell-only STT field must leave every
published MCX and equity number unchanged to the decimal.
"""
from __future__ import annotations

import pytest

from growmore_bot.costs import (
    DEFAULT_COST_MODEL,
    NSE_EQUITY_DELIVERY_COST_MODEL,
    NSE_OPTION_COST_MODEL,
    CostModel,
    leg_cost,
)


def test_option_stt_is_charged_on_the_sell_and_not_on_the_buy():
    # The asymmetry is the point: you are taxed for writing the option, not
    # for buying it back.
    premium_turnover = 100_000.0
    m = NSE_OPTION_COST_MODEL
    sell = leg_cost(premium_turnover, "sell", m)
    buy = leg_cost(premium_turnover, "buy", m)
    # The sell pays STT; the buy pays stamp duty. Both are taxes and neither
    # carries GST, so the gap is exactly the difference of the two rates.
    stt = premium_turnover * m.stt_sell_pct
    stamp = premium_turnover * m.stamp_buy_pct
    assert sell - buy == pytest.approx(stt - stamp, abs=0.01)
    assert stt > 30 * stamp


def test_the_new_field_leaves_mcx_and_equity_unchanged_to_the_decimal():
    # The Phase 0 control discipline: a new capability must not move a single
    # published number.
    assert DEFAULT_COST_MODEL.stt_sell_pct == 0.0
    assert NSE_EQUITY_DELIVERY_COST_MODEL.stt_sell_pct == 0.0

    notional = 100_000.0
    brokerage = min(20.0, notional * 0.0003)
    exchange = notional * 0.000026
    sebi = notional * 0.000002
    gst = 0.18 * (brokerage + exchange + sebi)
    expected_buy = brokerage + exchange + sebi + gst + notional * 0.00002
    assert leg_cost(notional, "buy", DEFAULT_COST_MODEL) == pytest.approx(expected_buy)


def test_brokerage_on_options_is_a_flat_fee_not_a_percentage():
    # Dhan charges Rs 20 per option order regardless of size, so a 10x larger
    # premium must not cost 10x more in brokerage.
    m = NSE_OPTION_COST_MODEL
    small = leg_cost(20_000.0, "buy", m)
    large = leg_cost(200_000.0, "buy", m)
    delta = 180_000.0
    # Brokerage is identical at both sizes, so the whole difference is the
    # percentage limbs: exchange + SEBI, the GST on those, and stamp duty.
    pct = delta * (m.exchange_txn_pct + m.sebi_pct)
    expected = pct + m.gst_pct * pct + delta * m.stamp_buy_pct
    assert large - small == pytest.approx(expected, abs=0.01)
    # 10x the premium costs ~4.1x, not 10x -- that is the flat fee working.
    # (It is not 1x either: the 0.05% exchange charge does scale.)
    assert large < 5 * small


def test_a_hand_computed_option_sale():
    # One lot of 500 written at a premium of 22.50 = Rs 11,250 of premium.
    premium = 500 * 22.50
    m = NSE_OPTION_COST_MODEL
    stt = premium * 0.001            # 11.25, sell side only
    exchange = premium * 0.0005      # 5.625
    sebi = premium * 0.000001
    gst = 0.18 * (20.0 + exchange + sebi)
    expected = 20.0 + exchange + sebi + gst + stt
    assert leg_cost(premium, "sell", m) == pytest.approx(expected, abs=0.01)


def test_assignment_is_charged_as_an_equity_delivery_not_as_an_option():
    # Physical settlement delivers shares, so the delivery leg is taxed on
    # strike x qty at equity rates -- an order of magnitude more than the
    # option leg it came from, and the charge a never-close strategy pays most.
    delivery_value = 500 * 1400.0        # Rs 7,00,000 of stock
    premium_value = 500 * 22.50          # Rs 11,250 of premium
    delivery = leg_cost(delivery_value, "buy", NSE_EQUITY_DELIVERY_COST_MODEL)
    option = leg_cost(premium_value, "sell", NSE_OPTION_COST_MODEL)
    assert delivery > 10 * option


def test_the_premium_versus_strike_base_is_not_interchangeable():
    """The single likeliest error in the whole study, pinned as a test.

    Charging option costs against strike x lot instead of the premium
    inflates them by the ratio of the two, which for a 5% OTM monthly is
    roughly 60x. Nothing in the signature of `leg_cost` prevents it.
    """
    premium_turnover = 500 * 22.50
    strike_turnover = 500 * 1400.0
    on_premium = leg_cost(premium_turnover, "sell", NSE_OPTION_COST_MODEL)
    on_strike = leg_cost(strike_turnover, "sell", NSE_OPTION_COST_MODEL)
    assert on_strike / on_premium > 20


def test_the_flat_fee_holds_across_every_realistic_premium():
    # Rs 20 flat is expressed through `min(brokerage_per_order, notional *
    # brokerage_pct)` with brokerage_pct = 1.0, so it selects the flat fee for
    # any turnover above Rs 20 -- which is every real option order.
    m = NSE_OPTION_COST_MODEL
    for turnover in (25.0, 1_000.0, 11_250.0, 5_000_000.0):
        brokerage_component = min(m.brokerage_per_order, turnover * m.brokerage_pct)
        assert brokerage_component == pytest.approx(20.0)


def test_below_twenty_rupees_of_turnover_the_flat_fee_degrades_and_that_is_known():
    """A documented corner, asserted rather than left to be discovered.

    The `min()` form cannot express a true flat fee below Rs 20 of turnover,
    so a notional of Rs 5 is charged Rs 5 rather than Rs 20. It never binds
    here: every strategy in this study holds to expiry rather than buying
    back, and one lot of the cheapest tradeable strike still clears Rs 20 of
    premium. Pinned so that if some future caller does transact down there,
    this test says what it will get.
    """
    m = NSE_OPTION_COST_MODEL
    assert min(m.brokerage_per_order, 5.0 * m.brokerage_pct) == 5.0
