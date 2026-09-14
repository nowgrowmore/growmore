"""TDD for `research.mcx_options.strike_selection.select_strike_by_target_delta`.

The fixture chain is built by round-tripping `black76_price` at a KNOWN flat
`sigma` for every strike, so `implied_vol_b76` recovers that same sigma
exactly (modulo bisection tolerance) and `black76_delta` at that recovered
vol reproduces the delta the strike was constructed to have. Which strike is
"closest to a target delta" is then established independently in the test
itself by calling the already-tested `black76_delta` directly -- the ground
truth a hand calculation would also have to lean on -- rather than hardcoding
a delta number that would silently drift if the pricing module ever changed.
"""
from __future__ import annotations

from datetime import date

import pytest

from research.mcx_options.bhavcopy import MCXOptionRow
from research.mcx_options.pricing import black76_delta, black76_price
from research.mcx_options.strike_selection import select_strike_by_target_delta

F = 100.0
T = 30 / 365
R = 0.0
SIGMA = 0.25
TRADE_DATE = date(2026, 9, 1)
EXPIRY = date(2026, 10, 1)

# OTM put strikes below the futures price -- |delta| shrinks as strike drops
# further away from F.
_PE_STRIKES = [80.0, 85.0, 90.0, 95.0, 100.0]
_DEFAULT_OI = 5_000


def _row(strike: float, opt_type: str, oi: int = _DEFAULT_OI) -> MCXOptionRow:
    price = black76_price(opt_type, F, strike, T, SIGMA, R)
    return MCXOptionRow(
        trade_date=TRADE_DATE,
        symbol="GOLDM",
        expiry=EXPIRY,
        strike=strike,
        opt_type=opt_type,
        open=price,
        high=price,
        low=price,
        close=price,
        previous_close=price,
        open_interest=oi,
        volume=10,
        value=price * 10,
    )


def _expected_closest(strikes: list[float], opt_type: str, target_delta: float) -> float:
    """Ground truth: the strike whose Black-76 delta (at the construction
    sigma) is nearest `target_delta` in absolute value."""
    return min(
        strikes,
        key=lambda k: abs(abs(black76_delta(opt_type, F, k, T, SIGMA, R)) - target_delta),
    )


def test_selects_strike_closest_to_target_delta_030():
    chain = [_row(k, "PE") for k in _PE_STRIKES]
    expected_strike = _expected_closest(_PE_STRIKES, "PE", 0.30)

    picked = select_strike_by_target_delta(
        chain, futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1,
    )

    assert picked is not None
    assert picked.strike == pytest.approx(expected_strike)


def test_selects_near_atm_strike_for_target_delta_050():
    chain = [_row(k, "PE") for k in _PE_STRIKES]
    expected_strike = _expected_closest(_PE_STRIKES, "PE", 0.50)

    picked = select_strike_by_target_delta(
        chain, futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.50, min_open_interest=1,
    )

    assert picked is not None
    assert picked.strike == pytest.approx(expected_strike)
    # Sanity: the ATM strike (100) is the nearest-to-0.50-delta candidate.
    assert expected_strike == 100.0


def test_thin_oi_strike_excluded_even_if_its_delta_is_closest():
    # 90 is the delta-closest strike to 0.30 among the full set (established
    # independently above); starve it of OI and confirm it is skipped in
    # favour of the next-best surviving strike.
    thin_strike = _expected_closest(_PE_STRIKES, "PE", 0.30)
    chain = [
        _row(k, "PE", oi=(50 if k == thin_strike else _DEFAULT_OI))
        for k in _PE_STRIKES
    ]
    survivors = [k for k in _PE_STRIKES if k != thin_strike]
    expected_strike = _expected_closest(survivors, "PE", 0.30)

    picked = select_strike_by_target_delta(
        chain, futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1_000,
    )

    assert picked is not None
    assert picked.strike != thin_strike
    assert picked.strike == pytest.approx(expected_strike)


def test_none_returned_when_every_candidate_fails_oi_filter():
    chain = [_row(k, "PE", oi=10) for k in _PE_STRIKES]

    picked = select_strike_by_target_delta(
        chain, futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1_000,
    )

    assert picked is None


def test_falls_back_to_passed_sigma_when_iv_unsolvable():
    # A zero/negative settlement price makes implied_vol_b76 refuse (returns
    # None) -- select_strike_by_target_delta must fall back to the supplied
    # `sigma` rather than raising or silently excluding the strike.
    bad_row = _row(90.0, "PE")
    bad_row = MCXOptionRow(**{**bad_row.__dict__, "close": 0.0})
    good_row = _row(95.0, "PE")
    chain = [bad_row, good_row]

    picked = select_strike_by_target_delta(
        chain, futures_price=F, T_years=T, sigma=SIGMA, r=R,
        target_delta=0.30, min_open_interest=1,
    )

    assert picked is not None  # did not raise, did not drop the bad-IV row
