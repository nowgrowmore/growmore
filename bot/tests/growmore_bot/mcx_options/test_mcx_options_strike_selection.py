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

import pytest

from growmore_bot.broker.dhan_client import OptionChainRow, OptionChainSnapshot
from growmore_bot.mcx_options.pricing import black76_delta, black76_price
from growmore_bot.mcx_options.strike_selection import select_strike_by_target_delta

F = 100.0
T = 30 / 365
R = 0.0
SIGMA = 0.25

_PE_STRIKES = [80.0, 85.0, 90.0, 95.0, 100.0]
_DEFAULT_OI = 5_000


def _row(strike: float, opt_type: str, oi: float = _DEFAULT_OI, iv: float = SIGMA) -> OptionChainRow:
    price = black76_price(opt_type, F, strike, T, SIGMA, R)
    return OptionChainRow(strike=strike, opt_type=opt_type, ltp=price, iv=iv, oi=oi, volume=10)


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
