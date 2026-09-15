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

from growmore_bot.broker.dhan_client import Bar, OptionChainRow, OptionChainSnapshot, Quote
from growmore_bot.mcx_options.live_data import fetch_cycle_data

TODAY = date(2026, 9, 14)

#: Mirrors the real confirmed 2026-09-15 production numbers: the historical
#: daily-bar endpoint's last available close (stale, several days behind)
#: versus a genuinely live quote's `ltp` (231946.0 vs 234477.0 in the real
#: incident) -- deliberately different so a test asserting `futures_price`
#: comes from the quote (not the bar) can't pass by accident.
STALE_BAR_CLOSE = 234477.0
LIVE_QUOTE_LTP = 231946.0


@dataclass
class _Instrument:
    symbol: str = "GOLDM"
    security_id: str = "123"
    exchange_segment: str = "MCX_COMM"
    lot_size: int = 100
    contract_expiry: date | None = None


def _quote(ltp: float = LIVE_QUOTE_LTP, close: float = STALE_BAR_CLOSE) -> Quote:
    return Quote(ltp=ltp, open=close, high=close, low=close, close=close)


class _FakeDhanClient:
    def __init__(self, bars, expiries, chain, quote=None):
        self._bars = bars
        self._expiries = expiries
        self._chain = chain
        self._quote = quote if quote is not None else _quote()
        self.historical_calls = []
        self.expiry_calls = []
        self.chain_calls = []
        self.quote_calls = []

    def get_historical_ohlc(self, instrument, from_date, to_date, interval="day"):
        self.historical_calls.append((instrument, from_date, to_date, interval))
        return self._bars

    def get_expiry_list(self, instrument):
        self.expiry_calls.append(instrument)
        return self._expiries

    def get_option_chain(self, instrument, expiry):
        self.chain_calls.append((instrument, expiry))
        return self._chain

    def get_quote(self, instrument):
        self.quote_calls.append(instrument)
        return self._quote


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
    # futures_price must come from the live quote, not the (potentially
    # stale-by-days) last historical bar -- see the dedicated tests below
    # for the confirmed-real-incident regression coverage.
    assert result.futures_price == LIVE_QUOTE_LTP
    assert result.option_chain is chain
    assert result.option_expiry == expiry
    assert result.T_years == pytest.approx(10 / 365.25)
    assert result.lot_size == 100
    assert client.chain_calls == [(instrument, expiry.isoformat())]
    assert client.quote_calls == [instrument]


def test_futures_price_comes_from_the_live_quote_not_the_stale_historical_bar():
    """Regression test for the confirmed 2026-09-15 production incident:
    Dhan's historical daily-bar endpoint lagged real-time by multiple days
    (querying `to_date=today` did not include the last 1-2 sessions), so
    `bars[-1].close` was several days stale at decision time -- including
    at `_settle_leg`'s ITM/OTM determination, which could produce a WRONG
    assignment/expiry decision for a real (paper) position. `futures_price`
    must instead come from `dhan_client.get_quote(instrument).ltp`, a
    genuinely real-time number, independent of how stale the historical
    bars are.
    """
    bars = _some_bars(start_close=STALE_BAR_CLOSE - 89)  # last bar close == STALE_BAR_CLOSE
    assert bars[-1].close == STALE_BAR_CLOSE
    expiry = TODAY + timedelta(days=10)
    client = _FakeDhanClient(
        bars, [expiry.isoformat()], _some_chain(), quote=_quote(ltp=LIVE_QUOTE_LTP, close=STALE_BAR_CLOSE)
    )

    result = fetch_cycle_data(client, _Instrument(), today=TODAY)

    assert result.futures_price == LIVE_QUOTE_LTP
    assert result.futures_price != bars[-1].close
    # The historical bars are still fetched and carried through unchanged --
    # they remain the regime classifier's trailing window (see
    # live_data.py's module/MCXCycleData docstrings for why that staleness
    # is comparatively tolerable, unlike using a stale price for settlement).
    assert result.futures_bars == tuple(bars)


def test_quote_is_fetched_for_the_same_instrument_passed_in():
    bars = _some_bars()
    expiry = TODAY + timedelta(days=10)
    instrument = _Instrument()
    client = _FakeDhanClient(bars, [expiry.isoformat()], _some_chain())

    fetch_cycle_data(client, instrument, today=TODAY)

    assert client.quote_calls == [instrument]


def test_carries_the_instrument_contract_expiry_for_rollover_detection():
    """`mcx_options_engine.run_cycle` needs to compare a `long_futures`
    position's own `futures_contract_expiry` against the Instrument's
    CURRENT `contract_expiry` every cycle (see mcx_options_engine's
    rollover logic) -- MCXCycleData must carry it through.
    """
    bars = _some_bars()
    expiry = TODAY + timedelta(days=10)
    chain = _some_chain()
    instrument = _Instrument(contract_expiry=date(2026, 10, 20))
    client = _FakeDhanClient(bars, [expiry.isoformat()], chain)

    result = fetch_cycle_data(client, instrument, today=TODAY)

    assert result.instrument_contract_expiry == date(2026, 10, 20)


def test_instrument_contract_expiry_defaults_to_none_when_instrument_lacks_it():
    bars = _some_bars()
    expiry = TODAY + timedelta(days=10)
    chain = _some_chain()
    client = _FakeDhanClient(bars, [expiry.isoformat()], chain)

    result = fetch_cycle_data(client, _Instrument(contract_expiry=None), today=TODAY)

    assert result.instrument_contract_expiry is None


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


def test_skips_an_expiry_that_is_today_and_picks_the_next_one():
    """B2 (independent review, 2026-09-15): an expiry dated TODAY gives
    T_years == 0, which collapses every Black-76 delta to its 0/+-1 boundary
    and makes the target-delta strike pick meaningless -- and the leg it
    would write carries `cycle_expiry == today`, which the settlement path
    can never see again. Only a strictly future expiry is tradeable.
    """
    today = date(2026, 9, 14)
    client = _FakeDhanClient(
        bars=_some_bars(),
        expiries=[today.isoformat(), (today + timedelta(days=10)).isoformat()],
        chain=_some_chain(),
    )

    cycle = fetch_cycle_data(client, _Instrument(), today)

    assert cycle.option_expiry == today + timedelta(days=10)
    assert cycle.T_years > 0


def test_raises_loudly_when_the_only_expiry_left_is_today():
    """Rather than silently entering at T_years == 0 -- see the test above."""
    today = date(2026, 9, 14)
    client = _FakeDhanClient(
        bars=_some_bars(), expiries=[today.isoformat()], chain=_some_chain()
    )

    with pytest.raises(ValueError, match="expiry"):
        fetch_cycle_data(client, _Instrument(), today)
