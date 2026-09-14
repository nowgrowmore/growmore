"""TDD for growmore_bot.mcx_options.strike_selection.select_strike_by_target_delta
-- the LIVE delta-targeted strike picker, working over a live
`OptionChainSnapshot` (growmore_bot.broker.dhan_client) rather than a
historical bhavcopy row (research/mcx_options/strike_selection.py).

Mirrors tests/research/mcx_options/test_strike_selection.py's technique: the
fixture chain is built by round-tripping `black76_price` at a KNOWN flat
sigma so `black76_delta` at that same sigma reproduces the delta each strike
was constructed to have, and "closest to target delta" is established
independently via `black76_delta` directly rather than a hardcoded number.

One real difference from the research version: Dhan's live option chain
already quotes each row's own IV (`OptionChainRow.iv`) -- there is no raw
settlement price to bisect an implied vol out of the way the offline
backtest's bhavcopy rows require. So this module uses `row.iv` directly when
present, falling back to the caller-supplied `sigma` only when a row's IV is
missing (illiquid/no trades), the live analog of the research module's
per-strike IV-with-fallback discipline.
"""
from __future__ import annotations

from typing import Optional

import pytest

from growmore_bot.broker.dhan_client import OptionChainRow, OptionChainSnapshot
from growmore_bot.mcx_options.pricing import black76_delta, black76_price
from growmore_bot.mcx_options.strike_selection import (
    CandidateEvaluation,
    evaluate_candidates,
    select_strike_by_target_delta,
)

F = 100.0
T = 30 / 365
R = 0.0
SIGMA = 0.25

_PE_STRIKES = [80.0, 85.0, 90.0, 95.0, 100.0]
_DEFAULT_OI = 5_000


def _row(
    strike: float,
    opt_type: str,
    oi: float = _DEFAULT_OI,
    iv: float = SIGMA,
    top_bid_price: Optional[float] = None,
    top_ask_price: Optional[float] = None,
) -> OptionChainRow:
    price = black76_price(opt_type, F, strike, T, SIGMA, R)
    # Default to a tight, executable market straddling `price` exactly, so
    # every pre-existing test (written before the bid/ask executability gate
    # existed) keeps passing the new check without having to know about it.
    if top_bid_price is None:
        top_bid_price = price * 0.98 if price > 0 else 0.01
    if top_ask_price is None:
        top_ask_price = price * 1.02 + 0.01
    return OptionChainRow(
        strike=strike, opt_type=opt_type, ltp=price, iv=iv, oi=oi, volume=10,
        top_bid_price=top_bid_price, top_ask_price=top_ask_price,
    )


def _expected_closest(strikes: list[float], opt_type: str, target_delta: float) -> float:
    return min(
        strikes,
        key=lambda k: abs(abs(black76_delta(opt_type, F, k, T, SIGMA, R)) - target_delta),
    )


def test_selects_strike_closest_to_target_delta_030():
    chain = OptionChainSnapshot(spot=F, rows=[_row(k, "PE") for k in _PE_STRIKES])
    expected_strike = _expected_closest(_PE_STRIKES, "PE", 0.30)

    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1,
    )

    assert picked is not None
    assert picked.strike == pytest.approx(expected_strike)


def test_selects_near_atm_strike_for_target_delta_050():
    chain = OptionChainSnapshot(spot=F, rows=[_row(k, "PE") for k in _PE_STRIKES])
    expected_strike = _expected_closest(_PE_STRIKES, "PE", 0.50)

    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.50, min_open_interest=1,
    )

    assert picked is not None
    assert picked.strike == pytest.approx(expected_strike)
    assert expected_strike == 100.0


def test_thin_oi_strike_excluded_even_if_its_delta_is_closest():
    thin_strike = _expected_closest(_PE_STRIKES, "PE", 0.30)
    chain = OptionChainSnapshot(
        spot=F,
        rows=[_row(k, "PE", oi=(50 if k == thin_strike else _DEFAULT_OI)) for k in _PE_STRIKES],
    )
    survivors = [k for k in _PE_STRIKES if k != thin_strike]
    expected_strike = _expected_closest(survivors, "PE", 0.30)

    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1_000,
    )

    assert picked is not None
    assert picked.strike != thin_strike
    assert picked.strike == pytest.approx(expected_strike)


def test_none_returned_when_every_candidate_fails_oi_filter():
    chain = OptionChainSnapshot(spot=F, rows=[_row(k, "PE", oi=10) for k in _PE_STRIKES])

    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1_000,
    )

    assert picked is None


def test_ignores_rows_of_the_other_option_type():
    # A CE row placed at a strike that would otherwise be the closest-delta
    # PE candidate must never be picked when opt_type="PE" is requested.
    chain = OptionChainSnapshot(
        spot=F,
        rows=[_row(k, "PE") for k in _PE_STRIKES] + [_row(97.0, "CE")],
    )
    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1,
    )
    assert picked is not None
    assert picked.opt_type == "PE"


def test_falls_back_to_passed_sigma_when_row_iv_missing():
    # A row with no quoted IV (illiquid/no trades) must still be usable --
    # falls back to the supplied sigma rather than being dropped or raising.
    bad_row = _row(90.0, "PE", iv=None)
    good_row = _row(95.0, "PE")
    chain = OptionChainSnapshot(spot=F, rows=[bad_row, good_row])

    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1,
    )

    assert picked is not None  # did not raise, did not drop the no-IV row


def test_empty_chain_returns_none():
    chain = OptionChainSnapshot(spot=F, rows=[])
    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=0,
    )
    assert picked is None


def test_evaluate_candidates_returns_every_oi_surviving_row_sorted_by_strike():
    chain = OptionChainSnapshot(spot=F, rows=[_row(k, "PE") for k in _PE_STRIKES])

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1,
    )

    assert [c.strike for c in candidates] == sorted(_PE_STRIKES)
    for c in candidates:
        assert isinstance(c, CandidateEvaluation)
        expected_delta = black76_delta("PE", F, c.strike, T, SIGMA, R)
        assert c.delta == pytest.approx(expected_delta)
        assert c.oi == _DEFAULT_OI


def test_evaluate_candidates_excludes_other_opt_type_and_thin_oi():
    thin_strike = _PE_STRIKES[0]
    chain = OptionChainSnapshot(
        spot=F,
        rows=[_row(k, "PE", oi=(50 if k == thin_strike else _DEFAULT_OI)) for k in _PE_STRIKES]
        + [_row(97.0, "CE")],
    )

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1_000,
    )

    strikes = [c.strike for c in candidates]
    assert thin_strike not in strikes
    assert 97.0 not in strikes  # CE row, wrong opt_type


def test_evaluate_candidates_empty_when_nothing_survives():
    chain = OptionChainSnapshot(spot=F, rows=[_row(k, "PE", oi=10) for k in _PE_STRIKES])

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1_000,
    )

    assert candidates == []


def test_select_strike_agrees_with_evaluate_candidates_winner():
    chain = OptionChainSnapshot(spot=F, rows=[_row(k, "PE") for k in _PE_STRIKES])
    target_delta = 0.30

    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=target_delta, min_open_interest=1,
    )
    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1,
    )
    best = min(candidates, key=lambda c: abs(abs(c.delta) - target_delta))

    assert picked is not None
    assert picked.strike == pytest.approx(best.strike)


# --- Executability gate (`top_bid_price`/`top_ask_price`) -----------------
#
# Confirmed 2026-09-14 against a REAL production pick: a live SILVERM PE
# option chain returned a strike (207000) whose `last_price` (27409.5) sat
# completely outside its own real bid-ask market (38-2044.5), oi=0,
# volume=0, implied_volatility=259.33% -- a stale/phantom quote, not
# anything actually tradeable. The bug this section guards: `oi=0` there
# would ALSO have been caught by the pre-existing OI floor, so these tests
# deliberately run with `min_open_interest=0` to prove the bid-ask
# executability check -- not the OI floor -- is what excludes the phantom
# row.

_SILVERM_F = 206_000.0
_SILVERM_T = 10 / 365
_SILVERM_PHANTOM_IV = 2.5933  # Dhan's real implied_volatility=259.33%, as a fraction
_SILVERM_SANE_IV = 0.18


def _silverm_phantom_row() -> OptionChainRow:
    # Real raw Dhan row, SILVERM 207000 PE, 2026-09-14.
    return OptionChainRow(
        strike=207_000.0, opt_type="PE", ltp=27_409.5, iv=_SILVERM_PHANTOM_IV,
        oi=0, volume=0, top_bid_price=38, top_ask_price=2_044.5,
    )


def _silverm_sane_row() -> OptionChainRow:
    # Real raw Dhan row, SILVERM 205000 PE, 2026-09-14 (oi/volume/bid/ask as
    # reported). The reported `last_price` (480) landed a hair outside its
    # own reported `top_ask_price` (479) -- a one-tick snapshot-timing lag
    # between the two fields, not a phantom quote like the 207000 row. Since
    # this row exists specifically to prove a genuinely-executable row
    # SURVIVES the gate, its `ltp` here (478) is nudged to sit inside that
    # same real spread rather than asserting inclusion on a value the
    # precise `bid <= ltp <= ask` rule would (correctly, if narrowly) still
    # reject.
    return OptionChainRow(
        strike=205_000.0, opt_type="PE", ltp=478.0, iv=_SILVERM_SANE_IV,
        oi=903, volume=250, top_bid_price=476, top_ask_price=479,
    )


def test_evaluate_candidates_excludes_the_real_phantom_row_and_keeps_the_sane_one():
    chain = OptionChainSnapshot(spot=_SILVERM_F, rows=[_silverm_phantom_row(), _silverm_sane_row()])

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=_SILVERM_F, T_years=_SILVERM_T,
        sigma=_SILVERM_SANE_IV, r=R, min_open_interest=0,
    )

    strikes = [c.strike for c in candidates]
    assert 207_000.0 not in strikes  # phantom: last_price way outside its own bid-ask
    assert 205_000.0 in strikes


def test_select_strike_never_picks_the_phantom_row_even_when_its_delta_is_closer():
    phantom = _silverm_phantom_row()
    sane = _silverm_sane_row()
    chain = OptionChainSnapshot(spot=_SILVERM_F, rows=[phantom, sane])

    phantom_delta = abs(
        black76_delta("PE", _SILVERM_F, phantom.strike, _SILVERM_T, _SILVERM_PHANTOM_IV, R)
    )
    sane_delta = abs(
        black76_delta("PE", _SILVERM_F, sane.strike, _SILVERM_T, _SILVERM_SANE_IV, R)
    )
    # Pick a target_delta right next to the phantom's (garbage) delta so a
    # naive closest-match-on-delta selection -- i.e. the old, un-gated
    # behaviour -- would choose the phantom row over the sane one.
    target_delta = phantom_delta + 0.001
    assert abs(phantom_delta - target_delta) < abs(sane_delta - target_delta), (
        "test setup invalid: phantom's delta must be the naive closest match"
    )

    picked = select_strike_by_target_delta(
        chain, opt_type="PE", futures_price=_SILVERM_F, T_years=_SILVERM_T,
        sigma=_SILVERM_SANE_IV, r=R, target_delta=target_delta, min_open_interest=0,
    )

    assert picked is not None
    assert picked.strike == pytest.approx(205_000.0)


def test_evaluate_candidates_excludes_row_with_oi_above_floor_but_no_bid_ask_data():
    # OI alone is not sufficient -- a row that clears the OI floor but has no
    # bid/ask at all can't be verified as executable, so it must still be
    # excluded (fail closed, not open).
    row = OptionChainRow(
        strike=100.0, opt_type="PE", ltp=5.0, iv=SIGMA, oi=5_000, volume=10,
        top_bid_price=None, top_ask_price=None,
    )
    chain = OptionChainSnapshot(spot=F, rows=[row])

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1,
    )

    assert candidates == []


def test_evaluate_candidates_excludes_row_with_nonzero_oi_but_ltp_below_bid():
    # Real raw Dhan row, 2026-09-14: oi=1 (nonzero -- clears any sane OI
    # floor) yet last_price (19) sits BELOW top_bid_price (29). An OI floor
    # alone would NOT catch this; only the bid-ask check does.
    row = OptionChainRow(
        strike=199_000.0, opt_type="PE", ltp=19.0, iv=SIGMA, oi=1, volume=1,
        top_bid_price=29.0, top_ask_price=120.0,
    )
    chain = OptionChainSnapshot(spot=F, rows=[row])

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1,
    )

    assert candidates == []


def test_evaluate_candidates_includes_row_whose_ltp_sits_exactly_on_the_bid():
    row = OptionChainRow(
        strike=100.0, opt_type="PE", ltp=5.0, iv=SIGMA, oi=5_000, volume=10,
        top_bid_price=5.0, top_ask_price=6.0,
    )
    chain = OptionChainSnapshot(spot=F, rows=[row])

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1,
    )

    assert [c.strike for c in candidates] == [100.0]


def test_evaluate_candidates_includes_row_whose_ltp_sits_exactly_on_the_ask():
    row = OptionChainRow(
        strike=100.0, opt_type="PE", ltp=6.0, iv=SIGMA, oi=5_000, volume=10,
        top_bid_price=5.0, top_ask_price=6.0,
    )
    chain = OptionChainSnapshot(spot=F, rows=[row])

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1,
    )

    assert [c.strike for c in candidates] == [100.0]


def test_evaluate_candidates_excludes_row_with_zero_bid_or_ask():
    # bid/ask of exactly 0 is not a real two-sided market either.
    zero_bid = OptionChainRow(
        strike=100.0, opt_type="PE", ltp=0.0, iv=SIGMA, oi=5_000, volume=10,
        top_bid_price=0.0, top_ask_price=1.0,
    )
    chain = OptionChainSnapshot(spot=F, rows=[zero_bid])

    candidates = evaluate_candidates(
        chain, opt_type="PE", futures_price=F, T_years=T, sigma=SIGMA, r=R, min_open_interest=1,
    )

    assert candidates == []
