"""Tests for growmore_bot.wheel_basket.live_iv_rank.

The Dhan-facing adapter that turns a real-time option chain + historical
bars into one CandidateData -- the engine tests (test_wheel_basket_engine.py)
cover the decision logic against hand-built CandidateData directly, so these
tests only need to prove the adapter's own arithmetic (nearest-to-spot IV
averaging, chain filtering, indicator warm-up), with the Dhan client
entirely mocked (unittest.mock, same convention as test_paper_engine.py --
never a real network call).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from growmore_bot.broker.dhan_client import Bar, OptionChainRow, OptionChainSnapshot, Quote
from growmore_bot.wheel_basket.live_iv_rank import average_atm_iv, build_candidate, tradeable_chain


def _snapshot(spot, rows):
    return OptionChainSnapshot(spot=spot, rows=rows)


def test_average_atm_iv_uses_only_the_nearest_to_spot_strike():
    chain = _snapshot(100.0, [
        OptionChainRow(strike=95.0, opt_type="CE", ltp=6.0, iv=0.50, oi=100, volume=10),
        OptionChainRow(strike=100.0, opt_type="CE", ltp=3.0, iv=0.30, oi=100, volume=10),
        OptionChainRow(strike=100.0, opt_type="PE", ltp=3.1, iv=0.32, oi=100, volume=10),
        OptionChainRow(strike=105.0, opt_type="CE", ltp=1.0, iv=0.80, oi=100, volume=10),
    ])
    # Only the two 100-strike legs are nearest to spot=100 -- their IVs average.
    assert average_atm_iv(chain, spot=100.0) == pytest_approx(0.31)


def pytest_approx(x):
    import pytest
    return pytest.approx(x)


def test_average_atm_iv_ignores_legs_with_no_iv():
    chain = _snapshot(100.0, [
        OptionChainRow(strike=100.0, opt_type="CE", ltp=3.0, iv=None, oi=0, volume=0),
        OptionChainRow(strike=100.0, opt_type="PE", ltp=3.1, iv=0.40, oi=100, volume=10),
    ])
    assert average_atm_iv(chain, spot=100.0) == pytest_approx(0.40)


def test_average_atm_iv_none_when_chain_is_empty():
    assert average_atm_iv(_snapshot(100.0, []), spot=100.0) is None


def test_tradeable_chain_filters_by_option_type_and_liquidity():
    chain = _snapshot(100.0, [
        OptionChainRow(strike=95.0, opt_type="PE", ltp=1.0, iv=0.4, oi=10, volume=5),
        OptionChainRow(strike=90.0, opt_type="PE", ltp=0.5, iv=0.4, oi=0, volume=0),  # illiquid
        OptionChainRow(strike=100.0, opt_type="CE", ltp=3.0, iv=0.3, oi=10, volume=5),
    ])
    puts = tradeable_chain(chain, "PE")
    assert puts == ((95.0, 1.0),)


def _stub_dhan_client(quote, chain, bars):
    client = MagicMock()
    client.get_quote.return_value = quote
    client.get_option_chain.return_value = chain
    client.get_historical_ohlc.return_value = bars
    return client


def _rising_bars(n=40):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    from datetime import timedelta
    return [
        Bar(timestamp=start + timedelta(days=i), open=100 * (1.02 ** i), high=100 * (1.02 ** i),
            low=100 * (1.02 ** i), close=100 * (1.02 ** i), volume=1000)
        for i in range(n)
    ]


def test_build_candidate_assembles_spot_iv_and_indicators():
    instrument = SimpleNamespace(symbol="RELIANCE", security_id="1333", exchange_segment="NSE_FNO",
                                  lot_size=250)
    quote = Quote(ltp=100.0, open=99, high=101, low=98, close=99)
    chain = _snapshot(100.0, [
        OptionChainRow(strike=100.0, opt_type="CE", ltp=3.0, iv=0.30, oi=100, volume=10),
        OptionChainRow(strike=100.0, opt_type="PE", ltp=3.1, iv=0.32, oi=100, volume=10),
        OptionChainRow(strike=95.0, opt_type="PE", ltp=1.0, iv=0.40, oi=100, volume=10),
    ])
    client = _stub_dhan_client(quote, chain, _rising_bars())

    from datetime import date
    candidate = build_candidate(client, instrument, expiry="2026-09-24",
                                 cycle_expiry=date(2026, 9, 24))

    assert candidate is not None
    assert candidate.symbol == "RELIANCE"
    assert candidate.spot == 100.0
    assert candidate.avg_iv == pytest_approx(0.31)
    assert candidate.lot_size == 250
    # A steadily rising series should read RSI solidly overbought.
    assert candidate.rsi is not None and candidate.rsi > 70
    assert candidate.macd_bullish is True
    assert (95.0, 1.0) in candidate.put_chain
    assert (100.0, 3.0) in candidate.call_chain


def test_build_candidate_returns_none_when_no_iv_is_available():
    instrument = SimpleNamespace(symbol="ILLIQUID", security_id="1", exchange_segment="NSE_FNO",
                                  lot_size=1)
    quote = Quote(ltp=100.0, open=99, high=101, low=98, close=99)
    chain = _snapshot(100.0, [])
    client = _stub_dhan_client(quote, chain, _rising_bars())

    from datetime import date
    candidate = build_candidate(client, instrument, expiry="2026-09-24",
                                 cycle_expiry=date(2026, 9, 24))
    assert candidate is None
