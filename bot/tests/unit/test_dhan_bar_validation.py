"""Historical-bar validation in DhanClient.get_historical_ohlc.

Found 2026-09-05: Dhan returns bars for NICKEL with open=high=low=0.0 but a
real close and a real volume -- 5 of 1,252 over the 5-year window, plus a
duplicated date. Those zeros are not prices; they are missing fields.

Unfiltered they silently corrupt everything downstream: a Donchian channel
low of 0, a Bollinger band computed against a 100% "move", an ATR inflated
by a 1,873-point true range, and -- once ATR-based stops existed -- a stop
placed at a NEGATIVE price, which then "filled" and booked a Rs 485,199 loss
on a single trade. The backtest showed a 199% max drawdown on a long-only
1x-leverage position, which is impossible and was the tell.

Only NICKEL is affected today, but the guard is universal: bad prints are a
property of the feed, not of one instrument.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from growmore_bot.broker.dhan_client import DhanClient


def _client_returning(payload):
    client = DhanClient.__new__(DhanClient)
    sdk = MagicMock()
    sdk.historical_daily_data.return_value = {"status": "success", "data": payload}
    client._sdk = sdk
    return client


def _instrument():
    from types import SimpleNamespace

    return SimpleNamespace(
        security_id="571304", exchange_segment="MCX_COMM", instrument_type="FUTCOM"
    )


def _epoch(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def _payload(rows):
    return {
        "timestamp": [r[0] for r in rows],
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [r[5] for r in rows],
    }


def test_bars_with_a_non_positive_price_are_dropped():
    """A bar reporting open=high=low=0 alongside a real close is a partial
    print, not a day the instrument traded at zero. Dropping it is safer than
    reconstructing a zero-range bar, which would quietly deflate ATR."""
    client = _client_returning(_payload([
        (_epoch(2022, 10, 21), 1850.0, 1880.0, 1840.0, 1870.0, 100.0),
        (_epoch(2022, 10, 24), 0.0, 0.0, 0.0, 1873.0, 20.0),      # corrupt
        (_epoch(2022, 10, 25), 1875.0, 1890.0, 1860.0, 1885.0, 90.0),
    ]))
    bars = client.get_historical_ohlc(
        _instrument(), from_date="2022-10-01", to_date="2022-10-31", interval="day"
    )
    assert len(bars) == 2
    assert all(min(b.open, b.high, b.low, b.close) > 0 for b in bars)


def test_duplicate_timestamps_are_collapsed_to_the_first_occurrence():
    client = _client_returning(_payload([
        (_epoch(2022, 10, 24), 1850.0, 1880.0, 1840.0, 1870.0, 100.0),
        (_epoch(2022, 10, 24), 1850.0, 1880.0, 1840.0, 1870.0, 100.0),
        (_epoch(2022, 10, 25), 1875.0, 1890.0, 1860.0, 1885.0, 90.0),
    ]))
    bars = client.get_historical_ohlc(
        _instrument(), from_date="2022-10-01", to_date="2022-10-31", interval="day"
    )
    assert len(bars) == 2
    assert [b.timestamp.date().day for b in bars] == [24, 25]


def test_a_clean_series_is_returned_untouched():
    rows = [
        (_epoch(2022, 10, 21), 1850.0, 1880.0, 1840.0, 1870.0, 100.0),
        (_epoch(2022, 10, 24), 1875.0, 1890.0, 1860.0, 1885.0, 90.0),
    ]
    bars = _client_returning(_payload(rows)).get_historical_ohlc(
        _instrument(), from_date="2022-10-01", to_date="2022-10-31", interval="day"
    )
    assert len(bars) == 2
    assert bars[0].close == pytest.approx(1870.0)
    assert bars[1].high == pytest.approx(1890.0)


def test_a_high_below_a_low_is_rejected_as_incoherent():
    client = _client_returning(_payload([
        (_epoch(2022, 10, 21), 1850.0, 1800.0, 1900.0, 1870.0, 100.0),   # h < l
        (_epoch(2022, 10, 24), 1875.0, 1890.0, 1860.0, 1885.0, 90.0),
    ]))
    bars = client.get_historical_ohlc(
        _instrument(), from_date="2022-10-01", to_date="2022-10-31", interval="day"
    )
    assert len(bars) == 1


def test_a_repeated_date_keeps_the_HIGHER_VOLUME_contract():
    """Same-timestamp bars are not redundant copies. Dhan's 5-year daily
    series for one security_id overlaps two CONTRACT MONTHS around every
    roll -- 41 of 43 repeated GOLDM dates carry different OHLC and different
    volume, e.g. 2022-10-09 returns one bar on 5,603 lots and another on
    15,000, ~1% apart in price. The high-volume bar is the liquid front
    month; the other is the expiring contract nobody is trading. Keeping
    whichever arrived first picks the illiquid one roughly half the time and
    injects a fake ~1% gap at every roll.
    """
    client = _client_returning(_payload([
        (_epoch(2022, 10, 9), 51800.0, 51800.0, 50969.0, 51064.0, 5603.0),    # expiring
        (_epoch(2022, 10, 9), 51453.0, 51453.0, 50763.0, 50871.0, 15000.0),   # front month
    ]))
    bars = client.get_historical_ohlc(
        _instrument(), from_date="2022-10-01", to_date="2022-10-31", interval="day"
    )
    assert len(bars) == 1
    assert bars[0].volume == pytest.approx(15000.0)
    assert bars[0].close == pytest.approx(50871.0)


def test_order_of_arrival_does_not_decide_which_contract_wins():
    for rows in (
        [(_epoch(2022, 10, 9), 1.0, 2.0, 0.5, 1.5, 100.0),
         (_epoch(2022, 10, 9), 3.0, 4.0, 2.5, 3.5, 900.0)],
        [(_epoch(2022, 10, 9), 3.0, 4.0, 2.5, 3.5, 900.0),
         (_epoch(2022, 10, 9), 1.0, 2.0, 0.5, 1.5, 100.0)],
    ):
        bars = _client_returning(_payload(rows)).get_historical_ohlc(
            _instrument(), from_date="2022-10-01", to_date="2022-10-31", interval="day"
        )
        assert len(bars) == 1 and bars[0].volume == pytest.approx(900.0)


def test_an_unusable_bar_never_wins_a_duplicate_contest_on_volume():
    """A corrupt zero-price bar with huge volume must not displace a real
    one -- validity is checked before the volume comparison."""
    bars = _client_returning(_payload([
        (_epoch(2022, 10, 9), 51453.0, 51453.0, 50763.0, 50871.0, 100.0),
        (_epoch(2022, 10, 9), 0.0, 0.0, 0.0, 50871.0, 99999.0),
    ])).get_historical_ohlc(
        _instrument(), from_date="2022-10-01", to_date="2022-10-31", interval="day"
    )
    assert len(bars) == 1 and bars[0].open == pytest.approx(51453.0)
