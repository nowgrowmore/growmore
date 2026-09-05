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
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from growmore_bot.broker.dhan_client import Quote
from growmore_bot.broker.dhan_order_client import PlacedOrder
from growmore_bot.live.engine import LiveTradingEngine, _format_debug_state
from growmore_bot.persistence.models import AuditLog, BotSignalState, LiveOrder
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class _FixedSignalStrategy(Strategy):
    def __init__(self, signal: Signal):
        self._signal = signal

    def on_bar(self, bar, position_state):
        return self._signal


class _SpyStrategy(Strategy):
    """Records the bar object it's actually called with -- see the identical
    class/regression test in test_paper_engine.py for the full story."""

    def __init__(self, signal: Signal):
        self._signal = signal
        self.received_bars: list = []

    def on_bar(self, bar, position_state):
        self.received_bars.append(bar)
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
        daily_loss_limit_enabled=True,
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


def test_format_debug_state_handles_a_non_numeric_value_without_crashing():
    # See the identical test/comment in test_paper_engine.py for the full story.
    class _FakeStrategy:
        def debug_state(self):
            return {"regime": "trending", "adx": 27.5, "macd": None}

    formatted = _format_debug_state(_FakeStrategy())
    assert "regime=trending" in formatted
    assert "adx=27.50" in formatted
    assert "macd=n/a" in formatted


def test_on_bar_receives_the_live_ltp_as_close_not_the_stale_quote_close():
    # Regression: found live 2026-09-04 -- see the identical test/comment in
    # test_paper_engine.py for the full story. Quote.close is Dhan's
    # ohlc.close (yesterday's frozen close), not today's live price; every
    # daily-bar strategy reads bar.close expecting "today's price so far".
    config = _bot_config()
    instrument = _instrument(config)
    strategy = _SpyStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(
        ltp=153026, open=152000, high=153200, low=151800, close=152598
    )
    order_client = MagicMock()
    session = MagicMock()

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    assert len(strategy.received_bars) == 1
    bar = strategy.received_bars[0]
    assert bar.close == 153026  # the live LTP, not the stale 152598
    assert bar.high == 153200
    assert bar.low == 151800
    assert bar.ltp == 153026


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


def test_live_max_position_size_rejection_is_throttled_within_30_minutes():
    config = _bot_config(max_position_size=1)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=5))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    order_client = MagicMock()
    session = MagicMock()
    now = datetime.now(timezone.utc)
    signal_state = BotSignalState(
        id=uuid.uuid4(), bot_config_id=config.id, last_signal="BUY",
        checked_at=now, ltp=100, indicators={}, crossing_state={},
        last_max_position_rejection_logged_at=now - timedelta(minutes=5),
    )
    session.query.return_value.filter_by.return_value.one_or_none.return_value = signal_state

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [
        o for o in added
        if isinstance(o, AuditLog) and o.event_type == "live_risk_guard_max_position_size_rejected"
    ]
    assert audit_entries == []


def test_live_max_position_size_rejection_logs_again_after_30_minutes():
    config = _bot_config(max_position_size=1)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=5))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    order_client = MagicMock()
    session = MagicMock()
    now = datetime.now(timezone.utc)
    signal_state = BotSignalState(
        id=uuid.uuid4(), bot_config_id=config.id, last_signal="BUY",
        checked_at=now, ltp=100, indicators={}, crossing_state={},
        last_max_position_rejection_logged_at=now - timedelta(minutes=31),
    )
    session.query.return_value.filter_by.return_value.one_or_none.return_value = signal_state

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [
        o for o in added
        if isinstance(o, AuditLog) and o.event_type == "live_risk_guard_max_position_size_rejected"
    ]
    assert len(audit_entries) == 1


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

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].close_reason == "daily_loss_limit"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    trip_entries = [e for e in audit_entries if e.event_type == "live_risk_guard_daily_loss_limit_tripped"]
    assert len(trip_entries) == 1
    assert trip_entries[0].payload["auto_close_attempted"] is True
    assert trip_entries[0].payload["auto_close_succeeded"] is True
    assert trip_entries[0].payload["bot_config_id"] == str(config.id)


def test_daily_loss_limit_disabled_skips_the_guard_even_when_breached():
    config = _bot_config(daily_loss_limit=1_000, daily_loss_limit_enabled=False)
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one_or_none.return_value = None
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

    assert config.enabled is True
    order_client.place_market_order.assert_not_called()
    assert existing_position.status == "open"
    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert not any(e.event_type == "live_risk_guard_daily_loss_limit_tripped" for e in audit_entries)


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

    # A failed auto-close now schedules itself for retry -- the config stays
    # disabled for fresh trades, but the still-open REAL position isn't just
    # abandoned; retry_pending_auto_close will pick it up on a later tick.
    assert config.pending_auto_close is True
    assert config.auto_close_retry_count == 1
    assert config.auto_close_next_retry_at is not None
    now_utc = datetime.now(timezone.utc)
    assert now_utc < config.auto_close_next_retry_at <= now_utc + timedelta(minutes=6)


def test_retry_pending_auto_close_succeeds_and_clears_the_retry_state():
    config = _bot_config(enabled=False, pending_auto_close=True, auto_close_retry_count=2)
    instrument = _instrument(config, lot_size=100)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.return_value = PlacedOrder(order_id="ORD9", order_status="TRANSIT")
    session = MagicMock()
    open_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.query.return_value.filter_by.return_value.one_or_none.return_value = open_position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.retry_pending_auto_close(config, instrument, now=datetime.now(timezone.utc))

    order_client.place_market_order.assert_called_once_with(
        instrument, transaction_type="SELL", quantity=1
    )
    assert open_position.status == "closed"
    assert float(open_position.realized_pnl) == pytest.approx(500_000)
    assert config.pending_auto_close is False
    assert config.auto_close_retry_count == 0
    assert config.auto_close_next_retry_at is None
    assert config.enabled is False  # never re-enabled by a successful retry

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].close_reason == "daily_loss_limit"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert any(e.event_type == "live_auto_close_retry_succeeded" for e in audit_entries)


def test_retry_pending_auto_close_failure_backs_off_and_never_raises():
    config = _bot_config(enabled=False, pending_auto_close=True, auto_close_retry_count=1)
    instrument = _instrument(config, lot_size=100)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    from growmore_bot.broker.dhan_order_client import DhanOrderError

    order_client.place_market_order.side_effect = DhanOrderError("Insufficient margin")
    session = MagicMock()
    open_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.query.return_value.filter_by.return_value.one_or_none.return_value = open_position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    now = datetime.now(timezone.utc)
    engine.retry_pending_auto_close(config, instrument, now=now)  # must not raise

    assert open_position.status == "open"  # still not confirmed closed
    assert config.pending_auto_close is True
    assert config.auto_close_retry_count == 2
    # Backs off further on each successive failure (5 * 2**(2-1) = 10 min).
    assert config.auto_close_next_retry_at == now + timedelta(minutes=10)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert any(e.event_type == "live_auto_close_retry_failed" for e in audit_entries)


def test_retry_pending_auto_close_skips_when_not_yet_due():
    now = datetime.now(timezone.utc)
    config = _bot_config(
        enabled=False, pending_auto_close=True, auto_close_retry_count=1,
        auto_close_next_retry_at=now + timedelta(minutes=5),
    )
    instrument = _instrument(config)
    dhan_client = MagicMock()
    order_client = MagicMock()
    session = MagicMock()

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.retry_pending_auto_close(config, instrument, now=now)

    order_client.place_market_order.assert_not_called()
    dhan_client.get_quote.assert_not_called()


def test_retry_pending_auto_close_clears_flag_when_nothing_left_open():
    # The position may have already been closed some other way (e.g. a
    # manual intervention) -- don't keep retrying forever against nothing.
    config = _bot_config(enabled=False, pending_auto_close=True, auto_close_retry_count=3)
    instrument = _instrument(config)
    dhan_client = MagicMock()
    order_client = MagicMock()
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one_or_none.return_value = None

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.retry_pending_auto_close(config, instrument, now=datetime.now(timezone.utc))

    order_client.place_market_order.assert_not_called()
    assert config.pending_auto_close is False
    assert config.auto_close_retry_count == 0
    assert config.auto_close_next_retry_at is None


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

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].close_reason == "strategy_signal"


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

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].close_reason == "expiry"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "live_contract_expiry_close_out"


def test_force_close_end_of_day_places_a_real_closing_order_with_its_own_audit_label():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.return_value = PlacedOrder(order_id="ORD4", order_status="TRANSIT")
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.force_close_end_of_day(
        config=config, instrument=instrument, current_position_qty=1,
        avg_entry_price=150000, live_position_id=existing_position.id,
    )

    order_client.place_market_order.assert_called_once_with(
        instrument, transaction_type="SELL", quantity=1
    )
    assert float(existing_position.realized_pnl) == pytest.approx(500_000)
    assert existing_position.status == "closed"

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].close_reason == "end_of_day"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    # Distinct label from the expiry close-out -- an end-of-day flatten is a
    # different event, not mislabeled as a contract-expiry one.
    assert audit_entries[0].event_type == "live_position_force_closed_end_of_day"
    assert audit_entries[0].payload["reason"] == "end_of_day"


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


def test_reconcile_corrects_sell_pnl_when_real_fill_price_differs():
    # Regression for docs/technical-debt.md's "reconciliation doesn't
    # retroactively recompute position-level P&L" gap. The old (approximate)
    # fill price and pnl are already stored on the order -- back-solve the
    # avg_entry_price that pnl implies, then recompute pnl from the real
    # fill price and apply the delta to the position's realized_pnl.
    dhan_client = MagicMock()
    order_client = MagicMock()
    order_client.get_order_status.return_value = {
        "data": [{"orderId": "ORD1", "orderStatus": "TRADED", "averageTradedPrice": 160000.0}],
    }
    position_id = uuid.uuid4()
    # avg_entry_price was 150000; approx fill was 155000, qty=1, lot_size=100
    # -> old pnl = (155000-150000)*1*100 = 500000
    position = SimpleNamespace(
        id=position_id,
        realized_pnl=500000.0,
        instrument=SimpleNamespace(lot_size=100),
    )
    pending_order = SimpleNamespace(
        id=uuid.uuid4(),
        broker_order_id="ORD1",
        order_status="TRANSIT",
        fill_price=155000.0,
        side="sell",
        quantity=1,
        live_position_id=position_id,
        pnl=500000.0,
    )
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [pending_order]
    session.get.return_value = position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.reconcile_pending_orders()

    # real fill 160000 instead of 155000 -> corrected pnl = (160000-150000)*100 = 1,000,000
    assert float(pending_order.pnl) == pytest.approx(1_000_000.0)
    # delta = 1,000,000 - 500,000 = 500,000 applied on top of the existing realized_pnl
    assert float(position.realized_pnl) == pytest.approx(1_000_000.0)


def test_reconcile_recomputes_buy_avg_entry_price_when_position_still_open_with_no_sells():
    dhan_client = MagicMock()
    order_client = MagicMock()
    order_client.get_order_status.return_value = {
        "data": [{"orderId": "ORD1", "orderStatus": "TRADED", "averageTradedPrice": 160000.0}],
    }
    position_id = uuid.uuid4()
    pending_order = SimpleNamespace(
        id=uuid.uuid4(),
        broker_order_id="ORD1",
        order_status="TRANSIT",
        fill_price=155000.0,
        side="buy",
        quantity=1,
        live_position_id=position_id,
        pnl=None,
    )
    position = SimpleNamespace(
        id=position_id,
        status="open",
        avg_entry_price=155000.0,
        instrument=SimpleNamespace(lot_size=100),
        orders=[pending_order],
    )
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [pending_order]
    session.get.return_value = position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.reconcile_pending_orders()

    assert float(position.avg_entry_price) == pytest.approx(160000.0)


def test_reconcile_skips_buy_correction_when_position_already_has_a_sell():
    dhan_client = MagicMock()
    order_client = MagicMock()
    order_client.get_order_status.return_value = {
        "data": [{"orderId": "ORD1", "orderStatus": "TRADED", "averageTradedPrice": 160000.0}],
    }
    position_id = uuid.uuid4()
    buy_order = SimpleNamespace(
        id=uuid.uuid4(),
        broker_order_id="ORD1",
        order_status="TRANSIT",
        fill_price=155000.0,
        side="buy",
        quantity=1,
        live_position_id=position_id,
        pnl=None,
    )
    sell_order = SimpleNamespace(
        id=uuid.uuid4(), side="sell", quantity=1, fill_price=158000.0, pnl=300000.0,
    )
    position = SimpleNamespace(
        id=position_id,
        status="closed",
        avg_entry_price=155000.0,
        instrument=SimpleNamespace(lot_size=100),
        orders=[buy_order, sell_order],
    )
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [buy_order]
    session.get.return_value = position

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.reconcile_pending_orders()  # must not raise

    # Left untouched -- correcting a closed/reduced position's cost basis
    # needs lot-level tracking this engine doesn't have; flagged for manual
    # review via a log line instead of silently guessing.
    assert float(position.avg_entry_price) == pytest.approx(155000.0)


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
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy, cumulative_daily_pnl=-2500.0
    )

    added = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], BotSignalState)]
    assert len(added) == 1
    assert added[0].bot_config_id == config.id
    assert added[0].last_signal == "HOLD"
    assert added[0].indicators == {"macd": -12.34, "signal": 5.67}
    assert float(added[0].prev_close) == pytest.approx(155000)
    assert float(added[0].daily_pnl) == pytest.approx(-2500.0)


def test_process_tick_appends_a_signal_history_row_every_tick_including_hold():
    from growmore_bot.persistence.models import SignalHistory

    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one_or_none.return_value = None

    engine = LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], SignalHistory)]
    assert len(added) == 1
    assert added[0].bot_config_id == config.id
    assert added[0].action == "HOLD"
    assert float(added[0].ltp) == pytest.approx(155000)


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
