"""Protects the per-cycle panel the basket engine replays.

The panel is built once and reused by every config, so an error here is an
error in all 14 results at once rather than in one of them. Two properties
matter most: strikes and premiums must arrive in ADJUSTED space (bhavcopy
strikes are unadjusted, cached cash bars are adjusted -- the trap both
fetch.py and run_strategies.py warn about), and the tradeable filter must
match the one the live engine applies, or the backtest will trade contracts
the bot could not.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.wheel_basket.panel import build_symbol_panel

EXPIRIES = [pd.Timestamp("2020-01-30"), pd.Timestamp("2020-02-27"),
            pd.Timestamp("2020-03-26"), pd.Timestamp("2020-04-30"),
            pd.Timestamp("2020-05-28"), pd.Timestamp("2020-06-25"),
            pd.Timestamp("2020-07-30")]


def _chain(spot: float = 1000.0, lot_size: int = 100) -> pd.DataFrame:
    """A synthetic chain with real time value, not a flat premium.

    A pricer that gave every strike the same premium would make ATM and OTM
    selection indistinguishable and hide the very differences these tests
    exist to detect -- the same argument test_wheel_engine._chain makes.
    """
    rows = []
    days = pd.bdate_range("2019-12-02", "2020-07-30")
    for day in days:
        future = [e for e in EXPIRIES if e >= day]
        if not future:
            continue
        # NSE lists several expiries at once, and that matters here: a cycle
        # is decided ON the previous expiry day, so next-month rows have to
        # exist that day or nothing could ever be opened.
        for expiry in future[:2]:
            tau = max((expiry - day).days, 1) / 365.0
            for strike in np.arange(spot * 0.85, spot * 1.15, 20.0):
                for opt_type in ("PE", "CE"):
                    intrinsic = max(strike - spot, 0.0) if opt_type == "PE" else max(spot - strike, 0.0)
                    time_value = 40.0 * np.sqrt(tau) * np.exp(-((strike - spot) / (0.15 * spot)) ** 2)
                    rows.append({
                        "trade_date": day, "symbol": "TEST", "expiry": expiry,
                        "strike": float(strike), "opt_type": opt_type,
                        "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0,
                        "settle": float(intrinsic + time_value),
                        "open_interest": 100.0, "volume": 50.0,
                        "lot_size": float(lot_size), "underlying": spot,
                    })
    return pd.DataFrame(rows)


def test_a_panel_has_one_cycle_per_expiry_it_can_actually_trade():
    panel = build_symbol_panel("TEST", chain=_chain(), lot_size=100)
    assert panel is not None
    # The first expiry has no prior expiry to decide on, so it cannot open a
    # cycle; every later one can.
    assert len(panel.cycles) == len(EXPIRIES) - 1


def test_each_cycle_decides_on_the_previous_expiry_never_later():
    """Opening a leg on any day after the decision day would be lookahead."""
    panel = build_symbol_panel("TEST", chain=_chain(), lot_size=100)
    for cycle in panel.cycles:
        assert cycle.decision_day < cycle.expiry
        assert cycle.days[0] >= cycle.decision_day
        assert cycle.days[-1] <= cycle.expiry


def test_tradeable_filter_matches_the_live_engines_rule():
    """MIN_STRIKE_VOLUME and settle > 0 -- the same gate live_iv_rank and
    strike_selection apply, so the backtest cannot trade what the bot cannot.
    """
    chain = _chain()
    chain.loc[chain["strike"] == chain["strike"].min(), "volume"] = 0.0
    panel = build_symbol_panel("TEST", chain=chain, lot_size=100)
    cycle = panel.cycles[0]
    puts = cycle.tradeable(is_call=False, day_index=0)
    assert all(v > 0 for _, v in puts)
    assert chain["strike"].min() not in [k for k, _ in puts]


def test_premiums_and_strikes_are_rescaled_into_adjusted_space():
    """A 2:1 split halves the cash close; strikes and premiums must follow, or
    a leg opened before the action is compared against a chain that no longer
    contains its strike.
    """
    chain = _chain()
    factors = pd.Series(0.5, index=sorted(chain["trade_date"].unique()))
    panel = build_symbol_panel("TEST", chain=chain, lot_size=100, factors=factors)
    unadjusted = build_symbol_panel("TEST", chain=chain, lot_size=100)
    assert panel.cycles[0].strikes.max() == pytest.approx(
        unadjusted.cycles[0].strikes.max() * 0.5, rel=1e-5
    )
    assert panel.cycles[0].spot[0] == pytest.approx(unadjusted.cycles[0].spot[0] * 0.5, rel=1e-5)


def test_settle_lookup_returns_the_quoted_price_for_a_held_leg():
    panel = build_symbol_panel("TEST", chain=_chain(), lot_size=100)
    cycle = panel.cycles[0]
    strike = cycle.strikes[len(cycle.strikes) // 2]
    value = cycle.settle_of(is_call=False, day_index=1, strike=float(strike))
    assert value is not None and value > 0


def test_an_unquoted_strike_falls_back_to_intrinsic_not_to_a_stale_price():
    """A stale entry premium would hold the liability frozen at last month's
    value and hide exactly the move that matters. wheel_engine._mark makes
    this same choice and says so.
    """
    panel = build_symbol_panel("TEST", chain=_chain(), lot_size=100)
    cycle = panel.cycles[0]
    far_strike = float(cycle.strikes.max() * 5)
    spot = float(cycle.spot[1])
    value = cycle.settle_of(is_call=False, day_index=1, strike=far_strike)
    assert value == pytest.approx(max(far_strike - spot, 0.0))


def test_a_symbol_with_too_little_history_is_unmeasured_not_weak():
    """The treatment docs/walk-forward-results.md gave short-history names."""
    short = _chain()
    short = short[short["expiry"] <= EXPIRIES[1]]
    assert build_symbol_panel("TEST", chain=short, lot_size=100) is None


def test_indicators_are_present_for_the_decision_day():
    panel = build_symbol_panel("TEST", chain=_chain(), lot_size=100)
    late = panel.cycles[-1]
    assert late.rsi is None or 0.0 <= late.rsi <= 100.0
    assert isinstance(late.macd_bullish, bool)
    assert late.atm_iv is None or late.atm_iv > 0
