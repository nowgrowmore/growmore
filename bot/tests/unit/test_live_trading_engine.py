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
from growmore_bot.persistence.models import LiveOrder
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
    return isinstance(obj, LiveOrder)


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


def test_buy_order_failure_is_caught_not_raised_and_persists_no_position():
    # Regression: found live 2026-09-04 -- a real DH-905 "Invalid IP" order
    # rejection propagated all the way out of process_tick, which meant
    # session_scope()'s except-block rolled back the ENTIRE tick's session,
    # including the audit_log entry DhanOrderClient had already written for
    # the failure -- so a failed real order attempt left no trace at all.
    from growmore_bot.broker.dhan_order_client import DhanOrderError

    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    order_client = MagicMock()
    order_client.place_market_order.side_effect = DhanOrderError("DH-905: Invalid IP")
    session = MagicMock()

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    # Must not raise -- the whole point of the fix.
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list]
    assert not any(_looks_like_order(obj) for obj in added)
    assert not any(isinstance(obj, type(config)) for obj in added)  # no LivePosition mock artifacts


def test_sell_order_failure_is_caught_not_raised():
    from growmore_bot.broker.dhan_order_client import DhanOrderError

    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.SELL, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.side_effect = DhanOrderError("DH-905: Invalid IP")
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

    # Position must be left exactly as it was -- no phantom close recorded.
    assert existing_position.status == "open"
    assert existing_position.quantity == 1


def test_force_close_for_expiry_order_failure_is_caught_not_raised():
    from growmore_bot.broker.dhan_order_client import DhanOrderError

    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.side_effect = DhanOrderError("DH-905: Invalid IP")
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

    assert existing_position.status == "open"
    assert existing_position.quantity == 1


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


def test_daily_loss_limit_trip_with_open_position_auto_closes_it():
    config = _bot_config(daily_loss_limit=1_000)
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.return_value = PlacedOrder(order_id="ORD9", order_status="TRANSIT")
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
        cumulative_daily_pnl=-1_500,
    )

    assert config.enabled is False
    order_client.place_market_order.assert_called_once_with(
        instrument, transaction_type="SELL", quantity=1
    )
    assert existing_position.status == "closed"
    assert float(existing_position.realized_pnl) == pytest.approx(500_000)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    trip_entries = [e for e in audit_entries if e.event_type == "live_risk_guard_daily_loss_limit_tripped"]
    assert len(trip_entries) == 1
    assert trip_entries[0].payload["auto_close_attempted"] is True
    assert trip_entries[0].payload["auto_close_succeeded"] is True


def test_daily_loss_limit_trip_auto_close_failure_is_logged_not_raised():
    config = _bot_config(daily_loss_limit=1_000)
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    from growmore_bot.broker.dhan_order_client import DhanOrderError

    order_client.place_market_order.side_effect = DhanOrderError("Insufficient margin")
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    # Must not raise -- a failed auto-close is a "needs human attention" event,
    # not a crash.
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy,
        current_position_qty=1, avg_entry_price=150000, live_position_id=existing_position.id,
        cumulative_daily_pnl=-1_500,
    )

    assert config.enabled is False
    # Position is left open/untouched -- we could not confirm a real close.
    assert existing_position.status == "open"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    trip_entries = [e for e in audit_entries if e.event_type == "live_risk_guard_daily_loss_limit_tripped"]
    assert len(trip_entries) == 1
    assert trip_entries[0].payload["auto_close_attempted"] is True
    assert trip_entries[0].payload["auto_close_succeeded"] is False


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


def test_reconcile_pending_orders_updates_status_and_fill_price():
    # Regression: found live 2026-09-04 -- Dhan's real GET /orders/{id}
    # response wraps the order in a LIST under "data" (confirmed against a
    # real response), not a bare dict as the (unverified) docs suggested.
    dhan_client = MagicMock()
    order_client = MagicMock()
    order_client.get_order_status.return_value = {
        "status": "success",
        "data": [{"orderId": "ORD1", "orderStatus": "TRADED", "averageTradedPrice": 155123.5, "filledQty": 1}],
    }
    session = MagicMock()
    pending_order = SimpleNamespace(
        id=uuid.uuid4(), broker_order_id="ORD1", order_status="TRANSIT", fill_price=155000,
    )
    session.query.return_value.filter.return_value.all.return_value = [pending_order]

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.reconcile_pending_orders()

    order_client.get_order_status.assert_called_once_with("ORD1")
    assert pending_order.order_status == "TRADED"
    assert float(pending_order.fill_price) == pytest.approx(155123.5)
    session.add.assert_any_call(pending_order)


def test_reconcile_pending_orders_also_handles_a_bare_dict_data_shape():
    # Defensive: Dhan's public docs describe "data" as a bare object, not a
    # list -- support both shapes rather than assuming either is permanent.
    dhan_client = MagicMock()
    order_client = MagicMock()
    order_client.get_order_status.return_value = {
        "data": {"orderId": "ORD1", "orderStatus": "TRADED", "averageTradedPrice": 155123.5}
    }
    session = MagicMock()
    pending_order = SimpleNamespace(
        id=uuid.uuid4(), broker_order_id="ORD1", order_status="TRANSIT", fill_price=155000,
    )
    session.query.return_value.filter.return_value.all.return_value = [pending_order]

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.reconcile_pending_orders()

    assert pending_order.order_status == "TRADED"


def test_reconcile_pending_orders_handles_missing_fields_without_raising():
    dhan_client = MagicMock()
    order_client = MagicMock()
    # Defensive: response missing the fields we'd normally expect.
    order_client.get_order_status.return_value = {"data": {"orderId": "ORD1"}}
    session = MagicMock()
    pending_order = SimpleNamespace(
        id=uuid.uuid4(), broker_order_id="ORD1", order_status="TRANSIT", fill_price=155000,
    )
    session.query.return_value.filter.return_value.all.return_value = [pending_order]

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.reconcile_pending_orders()  # must not raise

    # Nothing to update -- left exactly as it was.
    assert pending_order.order_status == "TRANSIT"
    assert float(pending_order.fill_price) == pytest.approx(155000)


def test_reconcile_pending_orders_swallows_a_failed_lookup_and_continues():
    dhan_client = MagicMock()
    order_client = MagicMock()
    order_client.get_order_status.side_effect = RuntimeError("boom")
    session = MagicMock()
    pending_order = SimpleNamespace(
        id=uuid.uuid4(), broker_order_id="ORD1", order_status="TRANSIT", fill_price=155000,
    )
    session.query.return_value.filter.return_value.all.return_value = [pending_order]

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.reconcile_pending_orders()  # must not raise

    assert pending_order.order_status == "TRANSIT"


def test_process_tick_records_signal_state_on_hold():
    from growmore_bot.persistence.models import BotSignalState

    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    strategy.debug_state = lambda: {"macd": -12.34, "signal": 5.67}
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one_or_none.return_value = None

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], BotSignalState)]
    assert len(added) == 1
    assert added[0].bot_config_id == config.id
    assert added[0].last_signal == "HOLD"
    assert added[0].indicators == {"macd": -12.34, "signal": 5.67}


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
