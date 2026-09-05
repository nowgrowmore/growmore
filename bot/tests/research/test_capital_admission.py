"""Tests for research.capital.admission -- the capital/leverage table.

The Dhan calls are mocked entirely (no network, per the unit-test convention
in CLAUDE.md); what's under test is the derivation and the fallback, not the
transport.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from research.capital.admission import build_admission, render


class _FakeClient:
    """Minimal DhanClient stand-in. `quote_ltp=None` simulates a closed
    market / failed quote so the daily-bar fallback is exercised."""

    def __init__(self, quote_ltp, bars):
        self._quote_ltp = quote_ltp
        self._bars = bars

    def get_quote(self, instrument):
        if self._quote_ltp is None:
            raise RuntimeError("market closed")
        return SimpleNamespace(ltp=self._quote_ltp)

    def get_historical_ohlc(self, instrument, from_date, to_date, interval):
        return self._bars


def _flat_bars(close, n=30, spread=10.0):
    """n bars with a constant high-low range, so ATR converges to `spread`."""
    return [
        SimpleNamespace(high=close + spread / 2, low=close - spread / 2, close=close)
        for _ in range(n)
    ]


GOLDM = SimpleNamespace(symbol="GOLDM", lot_size=10, exchange_segment="MCX_COMM", security_id="1")
COPPER = SimpleNamespace(symbol="COPPER", lot_size=2500, exchange_segment="MCX_COMM", security_id="2")


def test_notional_uses_lot_size_as_quote_units_not_raw_weight():
    client = _FakeClient(quote_ltp=15_767.0, bars=_flat_bars(15_767.0))
    row = build_admission(client, GOLDM)
    # 100g contract quoted per 10g -> lot_size 10, NOT 100.
    assert row.notional == pytest.approx(157_670.0)
    assert row.price_source == "quote"


def test_atr_is_scaled_to_rupees_per_lot_for_the_risk_figure():
    client = _FakeClient(quote_ltp=1_381.35, bars=_flat_bars(1_381.35, spread=20.0))
    row = build_admission(client, COPPER)
    assert row.atr == pytest.approx(20.0)
    # A 2x ATR stop on 2500 quote units is 2 * 20 * 2500 = Rs 100,000 at risk.
    assert row.risk_per_lot == pytest.approx(100_000.0)


def test_falls_back_to_the_last_daily_close_when_the_quote_fails():
    """Has to be runnable outside MCX hours -- a stale close is fine for a
    notional that only needs to be right to a few percent."""
    client = _FakeClient(quote_ltp=None, bars=_flat_bars(15_500.0))
    row = build_admission(client, GOLDM)
    assert row.price == pytest.approx(15_500.0)
    assert row.price_source == "last_daily_bar"


def test_returns_none_when_neither_a_quote_nor_any_bar_is_available():
    assert build_admission(_FakeClient(quote_ltp=None, bars=[]), GOLDM) is None


def test_render_shows_the_leverage_spread_that_makes_cagrs_incomparable():
    client_gold = _FakeClient(quote_ltp=15_767.0, bars=_flat_bars(15_767.0))
    client_copper = _FakeClient(quote_ltp=1_381.35, bars=_flat_bars(1_381.35))
    rows = [build_admission(client_gold, GOLDM), build_admission(client_copper, COPPER)]

    out = render(rows, capitals=[500_000.0])

    # Sorted by notional, so the cheap contract leads and the spread is visible.
    assert out.index("GOLDM") < out.index("COPPER")
    assert "0.32x" in out   # GOLDM at Rs 5L
    assert "6.91x" in out   # COPPER at Rs 5L -- ~22x more leverage off the same account
