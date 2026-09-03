"""Tests for growmore_bot.live.engine.LiveTradingEngine.

Mirrors test_paper_engine.py's structure closely -- same risk guards
(max_position_size, daily_loss_limit), same lot-size-scaled P&L convention
-- but every BUY/SELL calls a (mocked) DhanOrderClient instead of
simulating a fill, and persists to LivePosition/LiveOrder instead of
PaperPosition/PaperOrder. Never a real network call or real DhanOrderClient
here -- both `dhan_client` (quotes) and `order_client` (real orders) are
mocked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from growmore_bot.broker.dhan_client import Quote
from growmore_bot.broker.dhan_order_client import PlacedOrder
from growmore_bot.live.engine import LiveTradingEngine
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class _FixedSignalStrategy(Strategy):
    def __init__(self, signal: Signal):
        self._signal = signal

    def on_bar(self, bar, position_state):
        return self._signal


def _bot_config(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        instrument_id=uuid.uuid4(),
        enabled=True,
        mode="live",
        virtual_capital=500_000,
        max_position_size=10,
        daily_loss_limit=5_000,
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _instrument(config, lot_size=1):
    return SimpleNamespace(
        id=config.instrument_id,
        symbol="GOLDM",
        exchange_segment="MCX_COMM",
        security_id="123",
        lot_size=lot_size,
    )


def _looks_like_order(obj) -> bool:
    return hasattr(obj, "broker_order_id")


def test_disabled_config_is_skipped_entirely():
    config = _bot_config(enabled=False)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    order_client = MagicMock()
    session = MagicMock()

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    dhan_client.get_quote.assert_not_called()
    order_client.place_market_order.assert_not_called()
    session.add.assert_not_called()


def test_buy_signal_places_a_real_order_and_persists_the_position():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.return_value = PlacedOrder(order_id="ORD1", order_status="TRANSIT")
    session = MagicMock()

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    order_client.place_market_order.assert_called_once_with(
        instrument, transaction_type="BUY", quantity=1
    )
    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].broker_order_id == "ORD1"
    assert added_orders[0].order_status == "TRANSIT"
    assert added_orders[0].side == "buy"


def test_buy_rejected_when_exceeding_max_position_size_places_no_order():
    config = _bot_config(max_position_size=1)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=5))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    order_client = MagicMock()
    session = MagicMock()

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    order_client.place_market_order.assert_not_called()


def test_daily_loss_limit_trip_disables_config_without_attempting_an_order():
    config = _bot_config(daily_loss_limit=1_000)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    order_client = MagicMock()
    session = MagicMock()

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy, cumulative_daily_pnl=-1_500
    )

    assert config.enabled is False
    order_client.place_market_order.assert_not_called()
    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_risk_guard_daily_loss_limit_tripped"


def test_sell_closes_position_places_a_real_order_and_records_realized_pnl():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.SELL, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.return_value = PlacedOrder(order_id="ORD2", order_status="TRANSIT")
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy,
        current_position_qty=1, avg_entry_price=150000, live_position_id=existing_position.id,
    )

    order_client.place_market_order.assert_called_once_with(
        instrument, transaction_type="SELL", quantity=1
    )
    assert float(existing_position.realized_pnl) == pytest.approx(500_000)
    assert existing_position.status == "closed"


def test_force_close_for_expiry_places_a_real_closing_order_and_records_pnl():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.return_value = PlacedOrder(order_id="ORD3", order_status="TRANSIT")
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.force_close_for_expiry(
        config=config, instrument=instrument, current_position_qty=1,
        avg_entry_price=150000, live_position_id=existing_position.id,
    )

    order_client.place_market_order.assert_called_once_with(
        instrument, transaction_type="SELL", quantity=1
    )
    assert float(existing_position.realized_pnl) == pytest.approx(500_000)
    assert existing_position.status == "closed"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_contract_expiry_close_out"


def test_force_close_for_expiry_is_noop_with_no_open_position():
    config = _bot_config()
    instrument = _instrument(config)
    dhan_client = MagicMock()
    order_client = MagicMock()
    session = MagicMock()

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.force_close_for_expiry(
        config=config, instrument=instrument, current_position_qty=0,
        avg_entry_price=None, live_position_id=None,
    )

    dhan_client.get_quote.assert_not_called()
    order_client.place_market_order.assert_not_called()
    session.add.assert_not_called()


def test_hold_marks_open_position_to_market():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy,
        current_position_qty=1, avg_entry_price=150000, live_position_id=existing_position.id,
    )

    assert float(existing_position.unrealized_pnl) == pytest.approx(500_000)
    order_client.place_market_order.assert_not_called()
