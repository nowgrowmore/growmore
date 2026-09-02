"""Tests for growmore_bot.broker.dhan_client.DhanClient.

No real network calls: HTTP is mocked with `responses` against the real
dhanhq base URL (https://api.dhan.co/v2) and real endpoints, so these tests
double as documentation of the exact wire format used.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
import responses

API_BASE = "https://api.dhan.co/v2"


def _fake_jwt(exp: datetime) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(exp.timestamp())}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesignature"


@pytest.fixture
def instrument():
    from types import SimpleNamespace

    return SimpleNamespace(
        symbol="GOLDM",
        exchange_segment="MCX_COMM",
        security_id="999999",
        instrument_type="FUTCOM",
    )


def _make_client(token: str | None = None):
    from growmore_bot.broker.dhan_client import DhanClient

    token = token or _fake_jwt(datetime.now(timezone.utc) + timedelta(hours=1))
    return DhanClient(client_id="test-client", access_token=token)


def test_refresh_access_token_if_needed_ok_when_not_expired():
    client = _make_client(_fake_jwt(datetime.now(timezone.utc) + timedelta(hours=2)))
    # Should not raise.
    client.refresh_access_token_if_needed()


def test_refresh_access_token_if_needed_raises_clear_error_when_expired():
    from growmore_bot.broker.dhan_client import DhanTokenExpiredError

    client = _make_client(_fake_jwt(datetime.now(timezone.utc) - timedelta(minutes=5)))
    with pytest.raises(DhanTokenExpiredError, match="regenerate"):
        client.refresh_access_token_if_needed()


@responses.activate
def test_get_quote_calls_data_api_and_parses_ltp(instrument):
    responses.add(
        responses.POST,
        f"{API_BASE}/marketfeed/quote",
        json={
            "data": {
                "MCX_COMM": {
                    "999999": {
                        "last_price": 71234.5,
                        "ohlc": {"open": 71000, "high": 71500, "low": 70900, "close": 71100},
                    }
                }
            }
        },
        status=200,
    )
    client = _make_client()
    quote = client.get_quote(instrument)

    assert quote.ltp == pytest.approx(71234.5)
    assert quote.open == pytest.approx(71000)
    assert quote.high == pytest.approx(71500)
    assert quote.low == pytest.approx(70900)
    assert quote.close == pytest.approx(71100)

    sent = json.loads(responses.calls[0].request.body)
    assert sent["MCX_COMM"] == ["999999"]


@responses.activate
def test_get_quote_raises_on_api_failure_status(instrument):
    from growmore_bot.broker.dhan_client import DhanApiError

    responses.add(
        responses.POST,
        f"{API_BASE}/marketfeed/quote",
        json={"errorCode": "DH-905", "errorMessage": "Invalid token"},
        status=401,
    )
    client = _make_client()
    with pytest.raises(DhanApiError):
        client.get_quote(instrument)


@responses.activate
def test_get_historical_ohlc_calls_historical_daily_endpoint(instrument):
    responses.add(
        responses.POST,
        f"{API_BASE}/charts/historical",
        json={
            "open": [71000, 71200],
            "high": [71500, 71600],
            "low": [70900, 71100],
            "close": [71100, 71300],
            "volume": [100, 150],
            "timestamp": [1700000000, 1700086400],
        },
        status=200,
    )
    client = _make_client()
    bars = client.get_historical_ohlc(
        instrument, from_date="2023-11-14", to_date="2023-11-16", interval="day"
    )

    assert len(bars) == 2
    assert bars[0].open == pytest.approx(71000)
    assert bars[1].close == pytest.approx(71300)

    sent = json.loads(responses.calls[0].request.body)
    assert sent["securityId"] == "999999"
    assert sent["exchangeSegment"] == "MCX_COMM"


@responses.activate
def test_get_historical_ohlc_intraday_interval_calls_intraday_endpoint(instrument):
    responses.add(
        responses.POST,
        f"{API_BASE}/charts/intraday",
        json={
            "open": [71000],
            "high": [71050],
            "low": [70950],
            "close": [71010],
            "volume": [10],
            "timestamp": [1700000000],
        },
        status=200,
    )
    client = _make_client()
    bars = client.get_historical_ohlc(
        instrument, from_date="2023-11-14", to_date="2023-11-14", interval=5
    )
    assert len(bars) == 1

    sent = json.loads(responses.calls[0].request.body)
    assert sent["interval"] == 5


def test_dhan_client_never_exposes_order_placement_methods():
    client = _make_client()
    for forbidden in ("place_order", "place_slice_order", "modify_order", "cancel_order"):
        assert not hasattr(client, forbidden)


def test_dhan_client_blocks_access_to_underlying_order_methods():
    """Even reaching into the wrapped SDK client for an order method must fail."""
    client = _make_client()
    with pytest.raises(AttributeError):
        client._sdk.place_order
