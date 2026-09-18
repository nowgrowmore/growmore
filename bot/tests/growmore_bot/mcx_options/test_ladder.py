"""TDD for growmore_bot.mcx_options.ladder -- choosing the WEEK'S batch of new
short puts, as opposed to strike_selection.py's "one best strike in one chain".

The strategy now sells `weekly_new_puts_target` (2) new puts per instrument
per week, on top of whatever is already open, and those puts should not be
near-duplicates of each other or of the existing book. This module is what
turns "here are two chains and the positions I already hold" into "here are
the (expiry, strike) pairs to write, and here is why nothing else qualified".

Filter-then-rank, deliberately, rather than a weighted composite score: after
a bad trade you can point at the filter that should have caught it, which you
cannot do with opaque weights.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from growmore_bot.broker.dhan_client import OptionChainRow, OptionChainSnapshot
from growmore_bot.mcx_options.ladder import (
    annualized_yield_on_margin,
    breakeven_cushion,
    select_put_ladder,
)
from growmore_bot.mcx_options.pricing import black76_price

TODAY = date(2026, 9, 14)
NEAR_EXPIRY = TODAY + timedelta(days=11)
FAR_EXPIRY = TODAY + timedelta(days=42)
F = 6000.0
SIGMA = 0.25
R = 0.0
LOT_SIZE = 100


class _Config:
    """Only the fields ladder.py reads -- avoids a DB round-trip in a pure
    selection test (same technique test_mcx_options_engine.py uses).
    """

    def __init__(self, **overrides):
        self.lots = 1
        self.min_open_interest = 0
        self.min_volume = 0
        self.max_relative_spread = None
        self.min_credit_pct_of_strike = None
        self.min_breakeven_cushion_pct = None
        self.min_iv_minus_realised_vol = None
        self.min_strike_separation_pct = None
        self.max_positions_per_expiry = None
        self.secondary_target_delta = None
        self.margin_multiple_of_premium = 3.0
        self.use_bid_for_entry_premium = False
        self.fallback_sigma = None
        for k, v in overrides.items():
            setattr(self, k, v)


def _row(strike: float, expiry: date, *, oi=5000.0, volume=100.0, iv=SIGMA,
         bid=None, ask=None, ltp=None) -> OptionChainRow:
    T = (expiry - TODAY).days / 365.25
    price = ltp if ltp is not None else black76_price("PE", F, strike, T, SIGMA, R)
    return OptionChainRow(
        strike=strike, opt_type="PE", ltp=price, iv=iv, oi=oi, volume=volume,
        top_bid_price=bid if bid is not None else price * 0.98,
        top_ask_price=ask if ask is not None else price * 1.02 + 0.01,
    )


def _chain(expiry: date, strikes) -> OptionChainSnapshot:
    return OptionChainSnapshot(spot=F, rows=[_row(k, expiry) for k in strikes])


def _chains(near_strikes=None, far_strikes=None) -> dict:
    near = near_strikes if near_strikes is not None else [5600, 5700, 5800, 5900]
    far = far_strikes if far_strikes is not None else [5400, 5500, 5600, 5700]
    return {NEAR_EXPIRY: _chain(NEAR_EXPIRY, near), FAR_EXPIRY: _chain(FAR_EXPIRY, far)}


def _select(count=2, config=None, chains=None, existing=(), target_delta=0.30,
            realised=0.0):
    """Returns (picks, reasons) -- the third value (every candidate evaluated,
    for the dashboard's transparency column) is exercised separately below."""
    picks, reasons, _evaluated = select_put_ladder(
        chains_by_expiry=chains if chains is not None else _chains(),
        futures_price=F,
        existing_puts=list(existing),
        config=config if config is not None else _Config(),
        target_delta=target_delta,
        lot_size=LOT_SIZE,
        today=TODAY,
        realised_vol=realised,
        count=count,
    )
    return picks, reasons


# --- the two scoring primitives -------------------------------------------


def test_annualized_yield_scales_with_time_to_expiry():
    """The same rupee credit earned over half the time is twice the
    annualised return -- this is the "CAGR" ranking the ladder uses.
    """
    short = annualized_yield_on_margin(
        premium=50.0, lots=1, lot_size=100, dte_days=15, margin_multiple=3.0
    )
    long = annualized_yield_on_margin(
        premium=50.0, lots=1, lot_size=100, dte_days=30, margin_multiple=3.0
    )

    assert short == pytest.approx(2 * long)


def test_annualized_yield_is_a_return_on_margin_not_on_notional():
    """Margin is `margin_multiple_of_premium x credit`, so the yield reduces
    to (1 / multiple) x (365 / dte) -- independent of the premium itself.
    This is the column the 2026-09-15 review found nothing was reading.
    """
    y = annualized_yield_on_margin(
        premium=50.0, lots=2, lot_size=100, dte_days=36.5, margin_multiple=4.0
    )
    assert y == pytest.approx((1 / 4.0) * (365 / 36.5))


def test_annualized_yield_refuses_a_zero_or_negative_horizon():
    assert annualized_yield_on_margin(50.0, 1, 100, 0, 3.0) == 0.0
    assert annualized_yield_on_margin(50.0, 1, 100, -5, 3.0) == 0.0


def test_breakeven_cushion_is_the_distance_to_strike_minus_premium():
    # strike 5800, premium 50 -> breakeven 5750, which is 250 below F=6000.
    assert breakeven_cushion(F, 5800.0, 50.0) == pytest.approx(250 / F)


def test_breakeven_cushion_is_negative_for_an_itm_put():
    assert breakeven_cushion(F, 6200.0, 50.0) < 0


# --- picking the batch -----------------------------------------------------


def test_picks_the_requested_number_of_puts():
    picks, _ = _select(count=2)
    assert len(picks) == 2


def test_the_two_picks_are_not_the_same_contract():
    picks, _ = _select(count=2)
    assert (picks[0].expiry, picks[0].strike) != (picks[1].expiry, picks[1].strike)


def test_every_pick_is_out_of_the_money():
    picks, _ = _select(count=2)
    assert all(p.strike < F for p in picks)


def test_strike_separation_is_enforced_between_the_two_new_picks():
    """Without this the "ladder" collapses onto adjacent strikes and
    diversifies nothing.
    """
    cfg = _Config(min_strike_separation_pct=0.05)  # 5% of 6000 = 300
    picks, _ = _select(count=2, config=cfg)

    assert len(picks) == 2
    same_expiry = picks[0].expiry == picks[1].expiry
    if same_expiry:
        assert abs(picks[0].strike - picks[1].strike) >= 0.05 * F


def test_strike_separation_is_enforced_against_ALREADY_OPEN_puts():
    """The important half: with puts accumulating week on week, a new put
    landing on top of an existing one is pure concentration, not a ladder.
    """
    cfg = _Config(min_strike_separation_pct=0.02)  # 2% of 6000 = 120
    picks, _ = _select(count=1, config=cfg, existing=[5900.0])

    assert picks
    assert all(abs(p.strike - 5900.0) >= 120 for p in picks)


def test_max_positions_per_expiry_pushes_the_second_pick_to_another_expiry():
    """The brake that actually addresses simultaneous assignment: without it,
    a month of weekly rounds stacks every put on one expiry date.
    """
    cfg = _Config(max_positions_per_expiry=1)
    picks, _ = _select(count=2, config=cfg)

    assert len(picks) == 2
    assert picks[0].expiry != picks[1].expiry


def test_max_positions_per_expiry_counts_existing_positions_too():
    cfg = _Config(max_positions_per_expiry=1)
    picks, _ = _select(
        count=1, config=cfg, existing=[], chains={NEAR_EXPIRY: _chain(NEAR_EXPIRY, [5700, 5800])}
    )
    assert len(picks) == 1

    picks2, reasons, _ = select_put_ladder(
        chains_by_expiry={NEAR_EXPIRY: _chain(NEAR_EXPIRY, [5700, 5800])},
        futures_price=F, existing_puts=[], config=cfg, target_delta=0.30,
        lot_size=LOT_SIZE, today=TODAY, realised_vol=0.0, count=1,
        existing_expiry_counts={NEAR_EXPIRY: 1},
    )
    assert picks2 == []
    assert reasons


def test_secondary_target_delta_makes_the_pair_a_real_ladder():
    """One nearer the money, one further out -- rather than two puts that are
    effectively the same trade twice.
    """
    cfg = _Config(secondary_target_delta=0.10)
    picks, _ = _select(count=2, config=cfg, target_delta=0.35)

    assert len(picks) == 2
    assert abs(picks[0].delta) > abs(picks[1].delta)


def test_ranks_by_annualized_yield_when_nothing_else_separates_candidates():
    """Two expiries offering the same delta: the shorter-dated one turns its
    margin over faster and should win.
    """
    picks, _ = _select(count=1)
    assert picks[0].expiry == NEAR_EXPIRY


# --- the quality filters ---------------------------------------------------


def test_breakeven_cushion_floor_rejects_strikes_too_close_to_the_money():
    cfg = _Config(min_breakeven_cushion_pct=0.90)  # unreachable
    picks, reasons = _select(count=2, config=cfg)

    assert picks == []
    assert any("cushion" in r.lower() for r in reasons)


def test_variance_risk_premium_floor_blocks_entry_when_iv_is_below_realised():
    """Selling options whose implied vol is BELOW the underlying's realised
    vol is selling insurance too cheaply -- the one filter that speaks to
    whether there is an edge at all.
    """
    cfg = _Config(min_iv_minus_realised_vol=0.05)
    picks, reasons = _select(count=2, config=cfg, realised=SIGMA)  # iv - realised = 0

    assert picks == []
    assert any("vol" in r.lower() for r in reasons)


def test_variance_risk_premium_floor_allows_entry_when_iv_is_rich():
    cfg = _Config(min_iv_minus_realised_vol=0.05)
    picks, _ = _select(count=2, config=cfg, realised=0.10)  # 0.25 - 0.10 = 0.15

    assert len(picks) == 2


def test_volume_floor_excludes_a_strike_that_never_printed():
    chains = {NEAR_EXPIRY: OptionChainSnapshot(spot=F, rows=[
        _row(5800.0, NEAR_EXPIRY, volume=0),
        _row(5700.0, NEAR_EXPIRY, volume=250),
    ])}
    picks, _ = _select(count=1, config=_Config(min_volume=1), chains=chains)

    assert [p.strike for p in picks] == [5700.0]


def test_min_credit_floor_is_applied_per_strike():
    cfg = _Config(min_credit_pct_of_strike=0.50)  # impossible
    picks, reasons = _select(count=2, config=cfg)

    assert picks == []
    assert any("credit" in r.lower() for r in reasons)


def test_returns_fewer_picks_than_requested_rather_than_relaxing_a_filter():
    """A half-filled weekly round is a correct outcome; quietly dropping a
    constraint to reach the target is not.
    """
    chains = {NEAR_EXPIRY: _chain(NEAR_EXPIRY, [5800.0])}
    picks, reasons = _select(count=2, chains=chains)

    assert len(picks) == 1
    assert reasons  # says why the second slot went unfilled


def test_an_empty_chain_yields_no_picks_and_a_reason():
    picks, reasons = _select(count=2, chains={NEAR_EXPIRY: OptionChainSnapshot(spot=F, rows=[])})

    assert picks == []
    assert reasons


def test_uses_the_bid_for_yield_when_the_config_says_so():
    """A seller hits the bid, so the yield ranking should be computed on what
    would actually be received.
    """
    cfg = _Config(use_bid_for_entry_premium=True)
    picks, _ = _select(count=1, config=cfg)

    row_bid = next(
        r.top_bid_price
        for r in _chains()[picks[0].expiry].rows
        if r.strike == picks[0].strike
    )
    assert picks[0].premium == pytest.approx(row_bid)


def test_reports_every_candidate_it_evaluated_across_all_expiries():
    """The dashboard's "candidates evaluated" column. Spanning both expiries
    is strictly more transparency than the single-chain version this
    replaced -- each entry carries the expiry it came from.
    """
    _picks, _reasons, evaluated = select_put_ladder(
        chains_by_expiry=_chains(), futures_price=F, existing_puts=[], config=_Config(),
        target_delta=0.30, lot_size=LOT_SIZE, today=TODAY, realised_vol=0.0, count=2,
    )

    assert evaluated
    assert {e["expiry"] for e in evaluated} == {
        NEAR_EXPIRY.isoformat(), FAR_EXPIRY.isoformat()
    }
    assert all({"strike", "delta", "oi", "ltp", "expiry"} == set(e) for e in evaluated)
