"""Tests for the per-stock helper metrics in research.stock_options.run_strategies.

These are the inputs to the two untested combinations from
docs/stock-options-results.md: an RSI reading per day (for the basis-buffer
strategy) and an ATM implied vol sampled once per monthly cycle (for ranking
stocks by IV-richness). Both must reuse existing, already-tested machinery
rather than reimplementing an indicator or a pricer.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from research.stock_options.pricing import bs_price
from research.stock_options.run_strategies import (
    atm_iv_by_cycle,
    cycle_open_days,
    rsi_by_day,
)


def _price_chain(prices: dict, expiries: list, vol: float, strikes=(80, 90, 95, 100, 105, 110, 120)):
    """A chain priced with a KNOWN, constant Black-Scholes vol, so recovering
    it via implied_vol is a hand-checkable round trip.
    """
    rows = []
    for day, spot in sorted(prices.items()):
        for expiry in expiries:
            if expiry < day:
                continue
            years = max((expiry - day).days, 0) / 365.25
            for k in strikes:
                for opt in ("CE", "PE"):
                    price = bs_price(spot, k, years, vol, opt)
                    rows.append({
                        "trade_date": pd.Timestamp(day), "symbol": "TESTCO",
                        "expiry": pd.Timestamp(expiry), "strike": float(k),
                        "opt_type": opt, "open": 0.0, "high": 0.0, "low": 0.0,
                        "close": price, "settle": round(price, 4),
                        "open_interest": 10_000, "volume": 500,
                        "lot_size": 100, "underlying": spot,
                    })
    return pd.DataFrame(rows)


def _flat_series(start: date, n: int, price_fn):
    return {start + timedelta(days=i): price_fn(i) for i in range(n)}


def test_cycle_open_days_is_one_day_per_expiry_not_one_per_trading_day():
    start = date(2024, 1, 1)
    expiries = [start + timedelta(days=30 * (i + 1)) for i in range(4)]
    prices = _flat_series(start, 120, lambda i: 100.0)
    chain = _price_chain(prices, expiries, vol=0.3)
    opens = cycle_open_days(chain)
    assert len(opens) == len(expiries)
    assert opens == sorted(opens)


def test_atm_iv_by_cycle_recovers_the_known_vol_the_chain_was_priced_with():
    start = date(2024, 1, 1)
    expiries = [start + timedelta(days=30 * (i + 1)) for i in range(4)]
    prices = _flat_series(start, 120, lambda i: 100.0)
    true_vol = 0.35
    chain = _price_chain(prices, expiries, vol=true_vol)
    opens = cycle_open_days(chain)
    got = atm_iv_by_cycle(chain, opens)
    assert len(got) >= 3
    for iv in got.values():
        assert iv == pytest.approx(true_vol, abs=0.01)


def test_atm_iv_by_cycle_tracks_a_richer_chain_as_richer():
    start = date(2024, 1, 1)
    expiries = [start + timedelta(days=30 * (i + 1)) for i in range(4)]
    prices = _flat_series(start, 120, lambda i: 100.0)
    cheap = _price_chain(prices, expiries, vol=0.20)
    rich = _price_chain(prices, expiries, vol=0.45)
    opens = cycle_open_days(cheap)
    cheap_iv = atm_iv_by_cycle(cheap, opens)
    rich_iv = atm_iv_by_cycle(rich, opens)
    shared = set(cheap_iv) & set(rich_iv)
    assert shared
    assert all(rich_iv[d] > cheap_iv[d] for d in shared)


def test_rsi_by_day_reuses_the_shared_indicator_not_a_reimplementation():
    """A steadily rising series must eventually read overbought (>70), and a
    steadily falling one oversold (<30) -- the textbook RSI extremes, which
    only hold if this is really RsiMeanReversionStrategy's own arithmetic.
    """
    start = date(2024, 1, 1)
    up = pd.DataFrame([
        {"trade_date": pd.Timestamp(start + timedelta(days=i)),
         "underlying": 100.0 * (1.02 ** i)}
        for i in range(40)
    ])
    down = pd.DataFrame([
        {"trade_date": pd.Timestamp(start + timedelta(days=i)),
         "underlying": 100.0 * (0.98 ** i)}
        for i in range(40)
    ])
    up_rsi = rsi_by_day(up)
    down_rsi = rsi_by_day(down)
    assert max(up_rsi.values()) > 70.0
    assert min(down_rsi.values()) < 30.0
