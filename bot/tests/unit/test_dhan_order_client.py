"""Tests for growmore_bot.broker.dhan_order_client.DhanOrderClient --
the ONLY module in this codebase allowed to call Dhan's real Order API.

No real network calls: HTTP is mocked with `responses` against the real
dhanhq base URL and the real /orders endpoint (schema confirmed against the
installed dhanhq==2.2.0 SDK source, 2026-09-04: POST /orders, exchangeSegment
"MCX_COMM", productType "MARGIN" for carry-forward commodity positions
-- NOT "INTRADAY", which would auto-square-off same day and silently break
every multi-day-holding strategy this bot runs).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import responses

API_BASE = "https://api.dhan.co/v2"


@pytest.fixture
def instrument():
    return SimpleNamespace(
        id="instrument-uuid",
        symbol="GOLDM",
        exchange_segment="MCX_COMM",
        security_id="569003",
    )


def _make_client(live_trading_enabled: bool = True, session=None):
    from growmore_bot.broker.dhan_order_client import DhanOrderClient

    return DhanOrderClient(
        client_id="test-client",
        access_token="test-token",
        live_trading_enabled=live_trading_enabled,
        session=session or MagicMock(),
    )


def test_refuses_to_place_order_when_live_trading_disabled(instrument):
    from growmore_bot.broker.dhan_order_client import LiveTradingDisabledError

    session = MagicMock()
    client = _make_client(live_trading_enabled=False, session=session)

    with pytest.raises(LiveTradingDisabledError):
        client.place_market_order(instrument, transaction_type="BUY", quantity=1)

    session.add.assert_not_called()


@responses.activate
def test_places_a_real_market_order_and_writes_audit_log(instrument):
    responses.add(
        responses.POST,
        f"{API_BASE}/orders",
        json={"orderId": "112111182198", "orderStatus": "TRANSIT"},
        status=200,
    )
    session = MagicMock()
    client = _make_client(session=session)

    placed = client.place_market_order(instrument, transaction_type="BUY", quantity=1)

    assert placed.order_id == "112111182198"
    assert placed.order_status == "TRANSIT"

    sent = json.loads(responses.calls[0].request.body)
    assert sent["transactionType"] == "BUY"
    assert sent["exchangeSegment"] == "MCX_COMM"
    assert sent["securityId"] == "569003"
    assert sent["orderType"] == "MARKET"
    # Carry-forward, not INTRADAY -- our strategies hold across days.
    assert sent["productType"] == "MARGIN"
    assert sent["quantity"] == 1

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_order_placed"
    assert audit_entries[0].payload["broker_order_id"] == "112111182198"
    assert audit_entries[0].payload["transaction_type"] == "BUY"


@responses.activate
def test_rejects_invalid_transaction_type(instrument):
    session = MagicMock()
    client = _make_client(session=session)

    with pytest.raises(ValueError):
        client.place_market_order(instrument, transaction_type="HOLD", quantity=1)

    session.add.assert_not_called()


def test_stop_loss_order_refuses_when_live_trading_disabled(instrument):
    from growmore_bot.broker.dhan_order_client import LiveTradingDisabledError

    session = MagicMock()
    client = _make_client(live_trading_enabled=False, session=session)

    with pytest.raises(LiveTradingDisabledError):
        client.place_stop_loss_market_order(
            instrument, transaction_type="SELL", quantity=1, trigger_price=146760
        )

    session.add.assert_not_called()


@responses.activate
def test_places_a_real_stop_loss_market_order_and_writes_audit_log(instrument):
    responses.add(
        responses.POST,
        f"{API_BASE}/orders",
        json={"orderId": "112111182199", "orderStatus": "TRANSIT"},
        status=200,
    )
    session = MagicMock()
    client = _make_client(session=session)

    placed = client.place_stop_loss_market_order(
        instrument, transaction_type="SELL", quantity=1, trigger_price=146760
    )

    assert placed.order_id == "112111182199"
    assert placed.order_status == "TRANSIT"

    sent = json.loads(responses.calls[0].request.body)
    assert sent["transactionType"] == "SELL"
    assert sent["orderType"] == "STOP_LOSS_MARKET"
    assert sent["triggerPrice"] == 146760
    assert sent["productType"] == "MARGIN"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_stop_order_placed"
    assert audit_entries[0].payload["broker_order_id"] == "112111182199"
    assert audit_entries[0].payload["trigger_price"] == 146760


@responses.activate
def test_stop_loss_order_failure_raises_and_writes_audit_log(instrument):
    from growmore_bot.broker.dhan_order_client import DhanOrderError

    responses.add(
        responses.POST,
        f"{API_BASE}/orders",
        json={"errorCode": "DH-901", "errorMessage": "Insufficient balance"},
        status=400,
    )
    session = MagicMock()
    client = _make_client(session=session)

    with pytest.raises(DhanOrderError):
        client.place_stop_loss_market_order(
            instrument, transaction_type="SELL", quantity=1, trigger_price=146760
        )

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_stop_order_failed"


def test_modify_stop_loss_trigger_refuses_when_live_trading_disabled():
    from growmore_bot.broker.dhan_order_client import LiveTradingDisabledError

    session = MagicMock()
    client = _make_client(live_trading_enabled=False, session=session)

    with pytest.raises(LiveTradingDisabledError):
        client.modify_stop_loss_trigger("112111182199", quantity=1, new_trigger_price=148000)


@responses.activate
def test_modify_stop_loss_trigger_writes_audit_log_on_success():
    responses.add(
        responses.PUT,
        f"{API_BASE}/orders/112111182199",
        json={"orderId": "112111182199", "orderStatus": "PENDING"},
        status=200,
    )
    session = MagicMock()
    client = _make_client(session=session)

    client.modify_stop_loss_trigger("112111182199", quantity=1, new_trigger_price=148000)

    sent = json.loads(responses.calls[0].request.body)
    assert sent["triggerPrice"] == 148000

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_stop_order_modified"


@responses.activate
def test_modify_stop_loss_trigger_does_not_raise_on_failure_only_audits():
    responses.add(
        responses.PUT,
        f"{API_BASE}/orders/112111182199",
        json={"errorCode": "DH-904", "errorMessage": "Order already executed"},
        status=400,
    )
    session = MagicMock()
    client = _make_client(session=session)

    client.modify_stop_loss_trigger("112111182199", quantity=1, new_trigger_price=148000)  # must not raise

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_stop_order_modify_failed"


def test_cancel_stop_loss_order_refuses_when_live_trading_disabled():
    from growmore_bot.broker.dhan_order_client import LiveTradingDisabledError

    session = MagicMock()
    client = _make_client(live_trading_enabled=False, session=session)

    with pytest.raises(LiveTradingDisabledError):
        client.cancel_stop_loss_order("112111182199")


@responses.activate
def test_cancel_stop_loss_order_writes_audit_log_on_success():
    responses.add(
        responses.DELETE,
        f"{API_BASE}/orders/112111182199",
        json={"orderId": "112111182199", "orderStatus": "CANCELLED"},
        status=200,
    )
    session = MagicMock()
    client = _make_client(session=session)

    client.cancel_stop_loss_order("112111182199")

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_stop_order_cancelled"


@responses.activate
def test_cancel_stop_loss_order_does_not_raise_when_already_filled():
    # A real race: the stop may have triggered moments before the bot
    # decided to exit for its own reason -- cancelling an already-filled
    # order is expected, not fatal.
    responses.add(
        responses.DELETE,
        f"{API_BASE}/orders/112111182199",
        json={"errorCode": "DH-907", "errorMessage": "Order already executed/cancelled"},
        status=400,
    )
    session = MagicMock()
    client = _make_client(session=session)

    client.cancel_stop_loss_order("112111182199")  # must not raise

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_stop_order_cancel_failed"


def test_get_order_status_refuses_when_live_trading_disabled():
    from growmore_bot.broker.dhan_order_client import LiveTradingDisabledError

    client = _make_client(live_trading_enabled=False)

    with pytest.raises(LiveTradingDisabledError):
        client.get_order_status("112111182198")


@responses.activate
def test_get_order_status_returns_the_raw_response():
    responses.add(
        responses.GET,
        f"{API_BASE}/orders/112111182198",
        json={
            "orderId": "112111182198",
            "orderStatus": "TRADED",
            "averageTradedPrice": 155123.5,
            "filledQty": 1,
        },
        status=200,
    )
    client = _make_client()

    response = client.get_order_status("112111182198")

    assert response["data"]["orderStatus"] == "TRADED"
    assert response["data"]["averageTradedPrice"] == 155123.5


@responses.activate
def test_order_api_failure_raises_and_writes_audit_log(instrument):
    from growmore_bot.broker.dhan_order_client import DhanOrderError

    responses.add(
        responses.POST,
        f"{API_BASE}/orders",
        json={"errorCode": "DH-901", "errorMessage": "Insufficient balance"},
        status=400,
    )
    session = MagicMock()
    client = _make_client(session=session)

    with pytest.raises(DhanOrderError):
        client.place_market_order(instrument, transaction_type="SELL", quantity=1)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_order_failed"
