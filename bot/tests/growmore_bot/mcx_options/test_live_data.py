"""TDD for growmore_bot.mcx_options.live_data.fetch_cycle_data -- the ONLY
module in growmore_bot/mcx_options/ allowed to call DhanClient methods
(mirrors growmore_bot/wheel_basket/live_iv_rank.py's role for wheel_basket).

`DhanClient` is entirely mocked here with a fake object -- per CLAUDE.md's
testing conventions, unit tests never make a real network call. The fake
returns canned `Bar`/`OptionChainSnapshot` data shaped like
growmore_bot/broker/dhan_client.py's own dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest

from growmore_bot.broker.dhan_client import Bar, OptionChainRow, OptionChainSnapshot
from growmore_bot.mcx_options.live_data import fetch_cycle_data

TODAY = date(2026, 9, 14)


@dataclass
class _Instrument:
    symbol: str = "GOLDM"
    security_id: str = "123"
    exchange_segment: str = "MCX_COMM"
    lot_size: int = 100


class _FakeDhanClient:
    def __init__(self, bars, expiries, chain):
        self._bars = bars
        self._expiries = expiries
        self._chain = chain
        self.historical_calls = []
        self.expiry_calls = []
        self.chain_calls = []

    def get_historical_ohlc(self, instrument, from_date, to_date, interval="day"):
        self.historical_calls.append((instrument, from_date, to_date, interval))
        return self._bars

    def get_expiry_list(self, instrument):
        self.expiry_calls.append(instrument)
        return self._expiries

    def get_option_chain(self, instrument, expiry):
        self.chain_calls.append((instrument, expiry))
        return self._chain


def _bar(d: date, close: float) -> Bar:
    return Bar(
        timestamp=datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
        open=close, high=close, low=close, close=close, volume=100,
    )


def _some_bars(n=90, start_close=6000.0):
    return [_bar(TODAY - timedelta(days=n - i), start_close + i) for i in range(n)]


def _some_chain(spot=6100.0):
    return OptionChainSnapshot(
        spot=spot,
        rows=[
            OptionChainRow(strike=6000.0, opt_type="PE", ltp=50.0, iv=0.2, oi=5000, volume=10),
            OptionChainRow(strike=6100.0, opt_type="CE", ltp=45.0, iv=0.2, oi=5000, volume=10),
        ],
    )


def test_fetches_futures_history_option_chain_and_computes_T_years():
    bars = _some_bars()
    expiry = TODAY + timedelta(days=10)
    chain = _some_chain()
    client = _FakeDhanClient(bars, [expiry.isoformat(), (expiry + timedelta(days=30)).isoformat()], chain)
    instrument = _Instrument()

    result = fetch_cycle_data(client, instrument, today=TODAY)

    assert result.futures_bars == tuple(bars)
    assert result.futures_price == bars[-1].close
    assert result.option_chain is chain
    assert result.option_expiry == expiry
    assert result.T_years == pytest.approx(10 / 365.25)
    assert result.lot_size == 100
    assert client.chain_calls == [(instrument, expiry.isoformat())]


def test_picks_the_nearest_upcoming_expiry_not_the_first_in_the_list():
    bars = _some_bars()
    near = TODAY + timedelta(days=5)
    far = TODAY + timedelta(days=35)
    chain = _some_chain()
    client = _FakeDhanClient(bars, [far.isoformat(), near.isoformat()], chain)

    result = fetch_cycle_data(client, _Instrument(), today=TODAY)

    assert result.option_expiry == near


def test_ignores_expiries_that_have_already_passed():
    bars = _some_bars()
    passed = TODAY - timedelta(days=1)
    upcoming = TODAY + timedelta(days=15)
    chain = _some_chain()
    client = _FakeDhanClient(bars, [passed.isoformat(), upcoming.isoformat()], chain)

    result = fetch_cycle_data(client, _Instrument(), today=TODAY)

    assert result.option_expiry == upcoming


def test_raises_loudly_when_no_futures_history_returned():
    client = _FakeDhanClient([], [(TODAY + timedelta(days=10)).isoformat()], _some_chain())
    with pytest.raises(ValueError, match="futures history"):
        fetch_cycle_data(client, _Instrument(), today=TODAY)


def test_raises_loudly_when_no_upcoming_expiry():
    bars = _some_bars()
    client = _FakeDhanClient(bars, [(TODAY - timedelta(days=1)).isoformat()], _some_chain())
    with pytest.raises(ValueError, match="expiry"):
        fetch_cycle_data(client, _Instrument(), today=TODAY)


def test_raises_loudly_on_unparseable_expiry_string():
    """A Dhan response-shape mismatch must raise a clear, loud error --
    matching dhan_client.py's own behaviour -- rather than silently
    producing a wrong date (see the module's unverified-response-shape
    caveat).
    """
    bars = _some_bars()
    client = _FakeDhanClient(bars, ["not-a-date"], _some_chain())
    with pytest.raises(ValueError):
        fetch_cycle_data(client, _Instrument(), today=TODAY)
