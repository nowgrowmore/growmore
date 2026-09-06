"""Tests for research.stock_options.iv_rank.

The pure per-cycle sampling (`atm_iv_by_cycle`) is already tested against a
known Black-Scholes vol in test_run_strategies.py. What's specific to this
module is the year-grouping aggregation on top of it, which needs the chain
loading and corporate-action adjustment plumbing `run_strategies.py` already
owns -- so this test fakes only the cache/bars boundary, not the pricing.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd

from research.stock_options import iv_rank
from research.stock_options.pricing import bs_price


def _fake_chain(vol: float, n_days: int = 400, strikes=(80, 90, 95, 100, 105, 110, 120)):
    start = date(2023, 1, 1)
    expiries = [start + timedelta(days=30 * (i + 1)) for i in range((n_days // 30) + 1)]
    rows = []
    for i in range(n_days):
        day = start + timedelta(days=i)
        spot = 100.0
        for expiry in expiries:
            if expiry < day:
                continue
            years = max((expiry - day).days, 0) / 365.25
            for k in strikes:
                for opt in ("CE", "PE"):
                    price = bs_price(spot, k, years, vol, opt)
                    rows.append({
                        "trade_date": day, "symbol": "TESTCO", "expiry": expiry,
                        "strike": float(k), "opt_type": opt,
                        "open": 0.0, "high": 0.0, "low": 0.0,
                        "close": price, "settle": round(price, 4),
                        "open_interest": 10_000, "volume": 500,
                        "lot_size": 100, "underlying": spot,
                    })
    return pd.DataFrame(rows)


def _fake_bars(chain: pd.DataFrame):
    """Adjustment factor 1.0 everywhere: adjusted close == unadjusted spot."""
    spot_by_day = chain.groupby("trade_date")["underlying"].first()
    return [
        SimpleNamespace(timestamp=pd.Timestamp(day), close=spot)
        for day, spot in spot_by_day.items()
    ]


def test_iv_by_year_groups_the_per_cycle_samples_by_calendar_year(monkeypatch):
    chain = _fake_chain(vol=0.30, n_days=400)
    monkeypatch.setattr(iv_rank.chain_cache, "load_symbol", lambda s: chain.copy())
    monkeypatch.setattr(
        "research.stock_options.run_strategies.cash_bars.load",
        lambda s: _fake_bars(chain),
    )
    got = iv_rank.iv_by_year("TESTCO")
    assert set(got) == {2023, 2024}
    for iv in got.values():
        assert abs(iv - 0.30) < 0.02


def test_iv_by_year_is_empty_when_the_stock_has_no_cash_bars(monkeypatch):
    chain = _fake_chain(vol=0.30, n_days=60)
    monkeypatch.setattr(iv_rank.chain_cache, "load_symbol", lambda s: chain.copy())

    def _missing(_symbol):
        raise FileNotFoundError

    monkeypatch.setattr(
        "research.stock_options.run_strategies.cash_bars.load", _missing
    )
    assert iv_rank.iv_by_year("TESTCO") == {}
