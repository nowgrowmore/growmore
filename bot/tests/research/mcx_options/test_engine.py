"""TDD for `research.mcx_options.engine.run`, the put-sell/covered-call cycle
state machine. Every scenario below is a hand-computed arithmetic check, not
a "does it not crash" smoke test -- each test independently re-derives the
expected `total_pnl` from the same primitives (`leg_cost`, flat premiums and
strikes) the engine itself uses, so the assertion is a real cross-check
rather than a tautology against the implementation.

Fixtures use single-candidate option chains (one strike per day/expiry/side)
so which strike gets selected is not in question here -- that is
`test_strike_selection.py`'s job. What's under test is the STATE MACHINE:
assignment, covered-call writing, mark-to-market, rollover, called-away
close-out, and regime gating.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from growmore_bot.costs import CostModel
from research.mcx_options.bhavcopy import MCXOptionRow
from research.mcx_options.engine import EngineConfig, run
from research.mcx_options.regime import Regime, RegimeLabel

LOT_SIZE = 100
TICK_SIZE = 0.05

# Simple, hand-arithmetic-friendly cost models: a single flat percentage on
# turnover and nothing else (brokerage_per_order set huge so the "whichever
# is lower" flat cap never binds, letting brokerage_pct do the one thing
# under test).
OPTION_COST_MODEL = CostModel(
    brokerage_per_order=1e9, brokerage_pct=0.01,
    exchange_txn_pct=0.0, ctt_sell_pct=0.0, stt_both_pct=0.0, stt_sell_pct=0.0,
    stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
    slippage_ticks=0.0, stop_slippage_ticks=0.0, reviewed=True,
)
FUTURES_COST_MODEL = CostModel(
    brokerage_per_order=1e9, brokerage_pct=0.002,
    exchange_txn_pct=0.0, ctt_sell_pct=0.0, stt_both_pct=0.0, stt_sell_pct=0.0,
    stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
    slippage_ticks=0.0, stop_slippage_ticks=0.0, reviewed=True,
)


def _config(**overrides) -> EngineConfig:
    defaults = dict(
        lot_size=LOT_SIZE, tick_size=TICK_SIZE, lots=1,
        option_cost_model=OPTION_COST_MODEL, futures_cost_model=FUTURES_COST_MODEL,
        premium_slippage_pct=0.0, min_open_interest=0,
    )
    defaults.update(overrides)
    return EngineConfig(**defaults)


def _row(trade_date, expiry, strike, opt_type, close, oi=1000) -> MCXOptionRow:
    return MCXOptionRow(
        trade_date=trade_date, symbol="GOLDM", expiry=expiry, strike=strike,
        opt_type=opt_type, open=close, high=close, low=close, close=close,
        previous_close=close, open_interest=oi, volume=10, value=close * 10,
    )


def _bars(prices: dict) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": list(prices.values())}, index=list(prices.keys())
    )


FAVORABLE_PE = RegimeLabel(raw="uptrend", put=Regime.TREND_FAVORABLE, call=Regime.TREND_UNFAVORABLE)
FAVORABLE_CE = RegimeLabel(raw="downtrend", put=Regime.TREND_UNFAVORABLE, call=Regime.TREND_FAVORABLE)
UNFAVORABLE_PE = RegimeLabel(raw="downtrend", put=Regime.TREND_UNFAVORABLE, call=Regime.TREND_FAVORABLE)


def test_a_short_put_expires_otm_keeps_premium_no_futures_ever():
    d0, d1 = date(2026, 1, 5), date(2026, 2, 4)
    chain = [_row(d0, d1, strike=90.0, opt_type="PE", close=5.0)]
    bars = _bars({d0: 100.0, d1: 95.0})  # F stays >= strike -> OTM
    regime = {d0: FAVORABLE_PE}

    result = run(_config(), chain, bars, regime)

    qty = LOT_SIZE
    credit = 5.0 * qty
    cost = credit * 0.01
    expected = credit - cost

    assert result.total_pnl == pytest.approx(expected)
    assert len(result.cycles) == 1
    cycle = result.cycles[0]
    assert cycle.outcome == "put_expired_otm"
    assert cycle.basis is None  # never assigned -> no futures position ever
    assert all(leg.action != "assigned" for leg in cycle.legs)


def test_a2_accepts_a_real_pandas_datetimeindex_not_just_plain_dates():
    """Regression test: `_to_date`'s `isinstance(value, date)` check is a
    false-positive trap for `pd.Timestamp`, which subclasses
    `datetime.datetime` which subclasses `datetime.date` -- so a real
    `DatetimeIndex` (what every actual data pipeline produces: parquet ->
    `pd.to_datetime` -> `set_index`, never a plain Python `date` by hand)
    used to sail through `_to_date` UNCONVERTED, leaving `bars.index` full of
    `Timestamp`s despite the explicit `[_to_date(i) for i in bars.index]`
    normalisation pass. That silently broke every `date`-vs-`Timestamp`
    comparison downstream (e.g. `sorted_expiries` built from plain-`date`
    `MCXOptionRow.expiry` values compared against a `Timestamp` `day`),
    raising `TypeError: Cannot compare Timestamp with datetime.date` --
    invisible to every other test in this file because `_bars()` builds its
    index from plain Python `date` dict keys, never a real `DatetimeIndex`.
    """
    d0, d1 = date(2026, 1, 5), date(2026, 2, 4)
    chain = [_row(d0, d1, strike=90.0, opt_type="PE", close=5.0)]
    bars = pd.DataFrame(
        {"close": [100.0, 95.0]},
        index=pd.to_datetime([d0.isoformat(), d1.isoformat()]),
    )
    assert isinstance(bars.index, pd.DatetimeIndex)  # the real-world shape
    regime = {d0: FAVORABLE_PE}

    result = run(_config(), chain, bars, regime)

    qty = LOT_SIZE
    credit = 5.0 * qty
    cost = credit * 0.01
    expected = credit - cost

    assert result.total_pnl == pytest.approx(expected)
    assert len(result.cycles) == 1
    assert result.cycles[0].outcome == "put_expired_otm"


def test_b_short_put_expires_itm_assigns_at_strike_as_basis():
    d0, d1 = date(2026, 1, 5), date(2026, 2, 4)
    chain = [_row(d0, d1, strike=90.0, opt_type="PE", close=5.0)]
    bars = _bars({d0: 100.0, d1: 80.0})  # F < strike -> ITM -> assigned
    regime = {d0: FAVORABLE_PE}

    result = run(_config(), chain, bars, regime)

    qty = LOT_SIZE
    put_credit = 5.0 * qty
    put_cost = put_credit * 0.01
    assign_cost = 90.0 * qty * 0.002
    expected = (put_credit - put_cost) - assign_cost

    assert result.total_pnl == pytest.approx(expected)
    assert len(result.cycles) == 1
    cycle = result.cycles[0]
    assert cycle.outcome == "still_open"
    assert cycle.basis == pytest.approx(90.0)
    assert any(leg.action == "assigned" and leg.strike == 90.0 for leg in cycle.legs)


def test_c_covered_call_expires_itm_closes_cycle_with_hand_checked_total():
    d0 = date(2026, 1, 5)   # sell put
    d1 = date(2026, 2, 4)   # put expires ITM -> assigned
    d2 = date(2026, 2, 5)   # M2M day + sell covered call
    d3 = date(2026, 3, 6)   # call expires ITM -> called away

    chain = [
        _row(d0, d1, strike=90.0, opt_type="PE", close=5.0),
        _row(d2, d3, strike=100.0, opt_type="CE", close=4.0),
    ]
    bars = _bars({d0: 100.0, d1: 80.0, d2: 95.0, d3: 110.0})
    regime = {d0: FAVORABLE_PE, d2: FAVORABLE_CE}

    result = run(_config(), chain, bars, regime)

    qty = LOT_SIZE
    put_credit = 5.0 * qty
    put_cost = put_credit * 0.01
    assign_cost = 90.0 * qty * 0.002
    d2_mtm = (95.0 - 90.0) * qty
    call_credit = 4.0 * qty
    call_cost = call_credit * 0.01
    exit_mtm = (100.0 - 95.0) * qty
    exit_cost = 100.0 * qty * 0.002

    expected = (
        (put_credit - put_cost) - assign_cost
        + d2_mtm + (call_credit - call_cost)
        + exit_mtm - exit_cost
    )

    assert result.total_pnl == pytest.approx(expected)
    assert len(result.cycles) == 1
    cycle = result.cycles[0]
    assert cycle.outcome == "called_away"
    assert cycle.basis == pytest.approx(90.0)
    actions = [leg.action for leg in cycle.legs]
    assert actions == [
        "sell_put", "assigned", "mtm", "sell_call", "called_away",
    ]


def test_d_futures_contract_rollover_mid_cycle_survives_and_is_charged():
    d0 = date(2026, 1, 5)
    d1 = date(2026, 2, 4)   # assigned @ 90
    d2 = date(2026, 2, 5)   # sell covered call (expiry d4)
    d3 = date(2026, 2, 20)  # assigned futures CONTRACT's own expiry -> roll
    d4 = date(2026, 3, 6)   # call expires ITM -> called away

    chain = [
        _row(d0, d1, strike=90.0, opt_type="PE", close=5.0),
        _row(d2, d4, strike=100.0, opt_type="CE", close=4.0),
    ]
    bars = _bars({d0: 100.0, d1: 80.0, d2: 95.0, d3: 97.0, d4: 110.0})
    regime = {d0: FAVORABLE_PE, d2: FAVORABLE_CE}

    result = run(
        _config(futures_roll_cost_per_lot=50.0),
        chain, bars, regime,
        futures_contract_expiries=[d3, date(2026, 4, 5)],
    )

    qty = LOT_SIZE
    put_credit = 5.0 * qty
    put_cost = put_credit * 0.01
    assign_cost = 90.0 * qty * 0.002
    d2_mtm = (95.0 - 90.0) * qty
    call_credit = 4.0 * qty
    call_cost = call_credit * 0.01
    roll_mtm = (97.0 - 95.0) * qty
    roll_notional = 97.0 * qty
    roll_trade_cost = 2 * (roll_notional * 0.002)
    roll_flat_cost = 50.0 * 1
    roll_cost = roll_trade_cost + roll_flat_cost
    exit_mtm = (100.0 - 97.0) * qty
    exit_cost = 100.0 * qty * 0.002

    expected = (
        (put_credit - put_cost) - assign_cost
        + d2_mtm + (call_credit - call_cost)
        + roll_mtm - roll_cost
        + exit_mtm - exit_cost
    )

    assert result.total_pnl == pytest.approx(expected)
    cycle = result.cycles[0]
    assert cycle.outcome == "called_away"
    actions = [leg.action for leg in cycle.legs]
    assert "roll" in actions
    # the position survived the roll rather than being force-closed by it
    assert actions == [
        "sell_put", "assigned", "mtm", "sell_call", "roll", "called_away",
    ]


def test_e_unfavorable_or_missing_regime_skips_entry_entirely():
    d0, d1 = date(2026, 1, 5), date(2026, 2, 4)
    chain = [_row(d0, d1, strike=90.0, opt_type="PE", close=5.0)]
    bars = _bars({d0: 100.0, d1: 95.0})

    # (i) explicit TREND_UNFAVORABLE
    result_unfavorable = run(_config(), chain, bars, {d0: UNFAVORABLE_PE})
    assert result_unfavorable.total_pnl == 0.0
    assert result_unfavorable.cycles == []
    assert result_unfavorable.skipped_entries == 1

    # (ii) no regime label for the day at all -- "no opinion", never permissive
    result_missing = run(_config(), chain, bars, {})
    assert result_missing.total_pnl == 0.0
    assert result_missing.cycles == []
