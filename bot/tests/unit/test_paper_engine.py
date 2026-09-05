"""Unit tests for growmore_bot.paper.engine.PaperTradingEngine.

Everything (Dhan client, DB session) is mocked -- no real network or DB
calls. Covers: fill simulation at fetched LTP, max_position_size guard,
daily_loss_limit tripping, and -- found while wiring up real paper trading
for the first time -- two real gaps that existed until now:

1. Quantities/P&L never accounted for the instrument's real lot size (the
   same class of bug fixed in BacktestEngine -- see docs/technical-debt.md).
   `size` (from Signal.size) stays in human-friendly LOT units everywhere
   (matching what a person configuring `max_position_size` would expect --
   "2" means 2 lots, not 2 raw grams/kg/barrels); `instrument.lot_size` only
   scales the computed rupee P&L.
2. `_handle_sell` never actually closed the PaperPosition or recorded
   realized P&L -- it only ever wrote a PaperOrder row, so a position would
   stay "open" forever with no P&L tracked. Fixed to close (or partially
   reduce) the position and compute realized_pnl using the lot-scaled P&L.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from growmore_bot.broker.dhan_client import Quote
from growmore_bot.paper.engine import PaperTradingEngine, _format_debug_state
from growmore_bot.persistence.models import BotSignalState, PaperOrder
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class _FixedSignalStrategy(Strategy):
    def __init__(self, signal: Signal):
        self._signal = signal

    def on_bar(self, bar, position_state):
        return self._signal


class _SpyStrategy(Strategy):
    """Records the bar object it's actually called with, so a test can
    assert what a live tick hands to a daily-bar strategy's on_bar --
    without this, a bug where `bar.close` is stale (yesterday's close,
    frozen all day) instead of the live LTP is invisible to every other
    test here, since they all use Quote fixtures with ltp == close.
    """

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
    return isinstance(obj, PaperOrder)


def test_buy_signal_simulates_fill_at_fetched_ltp():
    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=71234.5, open=71000, high=71500, low=70900, close=71100)
    session = MagicMock()

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert float(added_orders[0].simulated_fill_price) == pytest.approx(71234.5)


def test_signal_rejected_when_exceeding_max_position_size():
    config = _bot_config(max_position_size=1)
    instrument = _instrument(config)
    # Requesting size=5 while max_position_size=1 must be rejected (no fill).
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=5))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert added_orders == []


def test_max_position_size_rejection_writes_audit_log_the_first_time():
    from growmore_bot.persistence.models import AuditLog

    config = _bot_config(max_position_size=1)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=5))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()
    signal_state = BotSignalState(
        id=uuid.uuid4(), bot_config_id=config.id, last_signal="BUY",
        checked_at=datetime.now(timezone.utc), ltp=100, indicators={}, crossing_state={},
        last_max_position_rejection_logged_at=None,
    )
    session.query.return_value.filter_by.return_value.one_or_none.return_value = signal_state

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [
        o for o in added if isinstance(o, AuditLog) and o.event_type == "risk_guard_max_position_size_rejected"
    ]
    assert len(audit_entries) == 1
    assert signal_state.last_max_position_rejection_logged_at is not None


def test_max_position_size_rejection_is_throttled_within_30_minutes():
    from growmore_bot.persistence.models import AuditLog

    config = _bot_config(max_position_size=1)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=5))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()
    now = datetime.now(timezone.utc)
    signal_state = BotSignalState(
        id=uuid.uuid4(), bot_config_id=config.id, last_signal="BUY",
        checked_at=now, ltp=100, indicators={}, crossing_state={},
        last_max_position_rejection_logged_at=now - timedelta(minutes=5),
    )
    session.query.return_value.filter_by.return_value.one_or_none.return_value = signal_state

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [
        o for o in added if isinstance(o, AuditLog) and o.event_type == "risk_guard_max_position_size_rejected"
    ]
    assert audit_entries == []


def test_max_position_size_rejection_logs_again_after_30_minutes():
    from growmore_bot.persistence.models import AuditLog

    config = _bot_config(max_position_size=1)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=5))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()
    now = datetime.now(timezone.utc)
    signal_state = BotSignalState(
        id=uuid.uuid4(), bot_config_id=config.id, last_signal="BUY",
        checked_at=now, ltp=100, indicators={}, crossing_state={},
        last_max_position_rejection_logged_at=now - timedelta(minutes=31),
    )
    session.query.return_value.filter_by.return_value.one_or_none.return_value = signal_state

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [
        o for o in added if isinstance(o, AuditLog) and o.event_type == "risk_guard_max_position_size_rejected"
    ]
    assert len(audit_entries) == 1


def test_format_debug_state_handles_a_non_numeric_value_without_crashing():
    # Regression: found via independent code review 2026-09-04 -- RegimeSwitch
    # Strategy.debug_state() returns {"regime": "trending", ...}, a string,
    # not a float. The old formatter unconditionally did f"{v:.2f}" for any
    # non-None value, which raises ValueError for a string -- crashing the
    # WHOLE scheduler tick and silently skipping every config after it in
    # the loop (session.commit() never reached). Reproduced live once
    # regime_switch became warmed-up enough for ADX to be computable.
    class _FakeStrategy:
        def debug_state(self):
            return {"regime": "trending", "adx": 27.5, "macd": None}

    formatted = _format_debug_state(_FakeStrategy())
    assert "regime=trending" in formatted
    assert "adx=27.50" in formatted
    assert "macd=n/a" in formatted


def test_on_bar_receives_the_live_ltp_as_close_not_the_stale_quote_close():
    # Regression: found live 2026-09-04 -- Quote.close is Dhan's ohlc.close,
    # which is the PREVIOUS trading day's official close and stays fixed all
    # session (see prev_close in bot_signal_state / docs/db-schema.md).
    # Every daily-bar strategy (RSI/MACD/SMA/Donchian/Bollinger/regime_switch)
    # reads `bar.close`, so passing the raw Quote straight to on_bar meant
    # every live tick appended the SAME frozen yesterday's-close value to the
    # strategy's window regardless of how far the real LTP had moved --
    # RSI/MACD/SMA never reacted to intraday price action at all, only
    # updating the next calendar day once warm-up replayed a new bar. The
    # bot must instead hand the strategy a bar whose `close` is today's live
    # LTP, matching every daily-bar strategy's expectation that `bar.close`
    # is "today's price so far".
    config = _bot_config()
    instrument = _instrument(config)
    strategy = _SpyStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(
        ltp=153026, open=152000, high=153200, low=151800, close=152598
    )
    session = MagicMock()

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    assert len(strategy.received_bars) == 1
    bar = strategy.received_bars[0]
    assert bar.close == 153026  # the live LTP, not the stale 152598
    # high/low are today's real session values (Dhan's ohlc.high/low ARE
    # live-reactive, unlike close) -- Donchian/Bollinger/regime_switch need
    # these untouched, not clamped to the LTP.
    assert bar.high == 153200
    assert bar.low == 151800
    assert bar.ltp == 153026


def test_bot_signal_state_prev_close_still_reflects_the_real_previous_close():
    # The live-bar fix above must not corrupt bot_signal_state.prev_close --
    # that field is deliberately yesterday's close (for the dashboard's
    # "Today +/-X%" badge), sourced from the ORIGINAL quote, not the wrapped
    # live bar handed to the strategy.
    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(
        ltp=153026, open=152000, high=153200, low=151800, close=152598
    )
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one_or_none.return_value = None

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added = [c.args[0] for c in session.add.call_args_list]
    signal_states = [obj for obj in added if isinstance(obj, BotSignalState)]
    assert len(signal_states) == 1
    assert signal_states[0].prev_close == 152598
    assert signal_states[0].ltp == 153026


def test_daily_loss_limit_trip_disables_config_and_writes_audit_log():
    config = _bot_config(daily_loss_limit=1_000)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    # Simulate a day's cumulative realized loss already breaching the limit.
    engine.process_tick(
        config=config,
        instrument=instrument,
        strategy=strategy,
        cumulative_daily_pnl=-1_500,
    )

    assert config.enabled is False

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "risk_guard_daily_loss_limit_tripped"
    assert audit_entries[0].payload["auto_close_attempted"] is False


def test_daily_loss_limit_trip_with_open_position_auto_closes_it():
    config = _bot_config(daily_loss_limit=1_000)
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy,
        current_position_qty=1, avg_entry_price=150000, paper_position_id=existing_position.id,
        cumulative_daily_pnl=-1_500,
    )

    assert config.enabled is False
    assert existing_position.status == "closed"
    assert float(existing_position.realized_pnl) == pytest.approx(500_000)

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].close_reason == "daily_loss_limit"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    trip_entries = [e for e in audit_entries if e.event_type == "risk_guard_daily_loss_limit_tripped"]
    assert len(trip_entries) == 1
    assert trip_entries[0].payload["auto_close_attempted"] is True
    assert trip_entries[0].payload["auto_close_succeeded"] is True


def test_daily_loss_limit_disabled_skips_the_guard_even_when_breached():
    # The account owner can opt a config out of the P&L-based circuit
    # breaker entirely and rely purely on the strategy's own BUY/SELL
    # signals -- daily_loss_limit_enabled=False must mean the breach check
    # never even runs, regardless of how far cumulative_daily_pnl exceeds
    # daily_loss_limit.
    config = _bot_config(daily_loss_limit=1_000, daily_loss_limit_enabled=False)
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one_or_none.return_value = None
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy,
        current_position_qty=1, avg_entry_price=150000, paper_position_id=existing_position.id,
        cumulative_daily_pnl=-1_500,  # well past daily_loss_limit=1_000
    )

    assert config.enabled is True
    assert existing_position.status == "open"
    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert not any(e.event_type == "risk_guard_daily_loss_limit_tripped" for e in audit_entries)


def test_no_signal_action_hold_does_not_create_order():
    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert added_orders == []


def test_disabled_config_is_skipped_entirely():
    config = _bot_config(enabled=False)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    session = MagicMock()

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    dhan_client.get_quote.assert_not_called()
    session.add.assert_not_called()


def test_sell_closes_position_and_records_lot_scaled_realized_pnl():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)  # arbitrary lot-scaling factor for this test
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.SELL, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()

    existing_position = SimpleNamespace(
        id=uuid.uuid4(),
        quantity=1,
        avg_entry_price=150000,
        realized_pnl=0,
        unrealized_pnl=0,
        status="open",
        closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config,
        instrument=instrument,
        strategy=strategy,
        current_position_qty=1,
        avg_entry_price=150000,
        paper_position_id=existing_position.id,
    )

    # (155000 - 150000) * 1 lot * 100 (lot size) = 500,000
    assert float(existing_position.realized_pnl) == pytest.approx(500_000)
    assert existing_position.status == "closed"
    assert existing_position.closed_at is not None
    assert existing_position.quantity == 0

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].quantity == 1
    # The order itself records its own realized pnl too, distinct from the
    # position's cumulative-ever total -- this is what lets the scheduler
    # compute *today's* pnl for the daily_loss_limit guard.
    assert float(added_orders[0].pnl) == pytest.approx(500_000)
    assert added_orders[0].close_reason == "strategy_signal"


def test_partial_sell_reduces_quantity_without_closing():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.SELL, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()

    existing_position = SimpleNamespace(
        id=uuid.uuid4(),
        quantity=3,
        avg_entry_price=150000,
        realized_pnl=0,
        unrealized_pnl=0,
        status="open",
        closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config,
        instrument=instrument,
        strategy=strategy,
        current_position_qty=3,
        avg_entry_price=150000,
        paper_position_id=existing_position.id,
    )

    assert existing_position.status == "open"
    assert existing_position.quantity == 2
    assert float(existing_position.realized_pnl) == pytest.approx((155000 - 150000) * 1 * 100)


def test_buy_adding_to_existing_position_blends_avg_entry_price():
    config = _bot_config(max_position_size=10)
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=160000, open=160000, high=160000, low=160000, close=160000)
    session = MagicMock()

    existing_position = SimpleNamespace(
        id=uuid.uuid4(),
        quantity=1,
        avg_entry_price=150000,
        realized_pnl=0,
        unrealized_pnl=0,
        status="open",
        closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config,
        instrument=instrument,
        strategy=strategy,
        current_position_qty=1,
        avg_entry_price=150000,
        paper_position_id=existing_position.id,
    )

    # blended = (150000*1 + 160000*1) / 2 = 155000
    assert existing_position.quantity == 2
    assert float(existing_position.avg_entry_price) == pytest.approx(155000)


def test_hold_marks_open_position_to_market():
    # Found via the dashboard: unrealized_pnl was written once at open (as 0)
    # and never touched again until close (also reset to 0) -- so every open
    # position showed zero unrealized P&L no matter how far the real price
    # had moved. HOLD is the most common tick outcome, so this is the case
    # that matters most.
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()

    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy,
        current_position_qty=1, avg_entry_price=150000,
        paper_position_id=existing_position.id,
    )

    # (155000 - 150000) * 1 lot * 100 (lot size) = 500,000
    assert float(existing_position.unrealized_pnl) == pytest.approx(500_000)


def test_hold_with_no_open_position_does_not_touch_the_db():
    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    session.get.assert_not_called()


def test_buy_adding_to_existing_position_recomputes_unrealized_pnl():
    config = _bot_config(max_position_size=10)
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=160000, open=160000, high=160000, low=160000, close=160000)
    session = MagicMock()

    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy,
        current_position_qty=1, avg_entry_price=150000,
        paper_position_id=existing_position.id,
    )

    # blended avg = 155000, new_total = 2 -> (160000-155000)*2*100 = 1,000,000
    assert float(existing_position.unrealized_pnl) == pytest.approx(1_000_000)


def test_partial_sell_recomputes_unrealized_pnl_on_remaining_quantity():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.SELL, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()

    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=3, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy,
        current_position_qty=3, avg_entry_price=150000,
        paper_position_id=existing_position.id,
    )

    # remaining_qty=2 -> (155000-150000)*2*100 = 1,000,000
    assert float(existing_position.unrealized_pnl) == pytest.approx(1_000_000)


def test_force_close_for_expiry_closes_position_and_records_pnl():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.force_close_for_expiry(
        config=config, instrument=instrument, current_position_qty=1,
        avg_entry_price=150000, paper_position_id=existing_position.id,
    )

    # (155000 - 150000) * 1 lot * 100 (lot size) = 500,000
    assert float(existing_position.realized_pnl) == pytest.approx(500_000)
    assert existing_position.status == "closed"
    assert existing_position.closed_at is not None
    assert existing_position.quantity == 0
    assert float(existing_position.unrealized_pnl) == pytest.approx(0)

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert float(added_orders[0].pnl) == pytest.approx(500_000)
    assert added_orders[0].close_reason == "expiry"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "contract_expiry_close_out"


def test_force_close_end_of_day_closes_position_with_its_own_audit_label():
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.force_close_end_of_day(
        config=config, instrument=instrument, current_position_qty=1,
        avg_entry_price=150000, paper_position_id=existing_position.id,
    )

    assert float(existing_position.realized_pnl) == pytest.approx(500_000)
    assert existing_position.status == "closed"

    added_orders = [c.args[0] for c in session.add.call_args_list if _looks_like_order(c.args[0])]
    assert len(added_orders) == 1
    assert added_orders[0].close_reason == "end_of_day"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "position_force_closed_end_of_day"


def test_force_close_for_expiry_is_noop_with_no_open_position():
    config = _bot_config()
    instrument = _instrument(config)
    dhan_client = MagicMock()
    session = MagicMock()

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.force_close_for_expiry(
        config=config, instrument=instrument, current_position_qty=0,
        avg_entry_price=None, paper_position_id=None,
    )

    dhan_client.get_quote.assert_not_called()
    session.add.assert_not_called()


def test_force_close_for_expiry_is_logged_at_warning_level(caplog):
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    with caplog.at_level("WARNING"):
        engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
        engine.force_close_for_expiry(
            config=config, instrument=instrument, current_position_qty=1,
            avg_entry_price=150000, paper_position_id=existing_position.id,
        )

    assert any("EXPIRY CLOSE-OUT" in r.message for r in caplog.records)


def test_process_tick_records_signal_state_on_hold():
    from growmore_bot.persistence.models import BotSignalState

    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    strategy.debug_state = lambda: {"macd": -12.34, "signal": 5.67}
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one_or_none.return_value = None

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(
        config=config, instrument=instrument, strategy=strategy, cumulative_daily_pnl=-2500.0
    )

    added = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], BotSignalState)]
    assert len(added) == 1
    assert added[0].bot_config_id == config.id
    assert added[0].last_signal == "HOLD"
    assert float(added[0].ltp) == pytest.approx(155000)
    assert added[0].indicators == {"macd": -12.34, "signal": 5.67}
    assert float(added[0].daily_pnl) == pytest.approx(-2500.0)
    # quote.close is Dhan's previous-trading-day close (confirmed against a
    # real quote during live hours 2026-09-04) -- lets the dashboard compute
    # today's % change without its own live Dhan connection.
    assert float(added[0].prev_close) == pytest.approx(155000)


def test_process_tick_updates_existing_signal_state_row_in_place():
    from growmore_bot.persistence.models import BotSignalState

    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()
    existing_state = BotSignalState(
        id=uuid.uuid4(), bot_config_id=config.id, last_signal="HOLD",
        checked_at=datetime.now(timezone.utc), ltp=99, indicators={},
    )
    session.query.return_value.filter_by.return_value.one_or_none.return_value = existing_state

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    assert existing_state.last_signal == "BUY"
    assert float(existing_state.ltp) == pytest.approx(100)
    session.add.assert_any_call(existing_state)


def test_buy_fill_is_logged_at_info_level(caplog):
    # Found while running the bot for real: nothing was ever logged when a
    # trade actually happened -- you'd only find out by checking the
    # dashboard/database, not bot.log.
    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=71234.5, open=71000, high=71500, low=70900, close=71100)
    session = MagicMock()

    with caplog.at_level("INFO"):
        engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
        engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    assert any("BUY FILLED" in r.message for r in caplog.records)


def test_hold_signal_is_logged_at_info_level(caplog):
    config = _bot_config()
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.HOLD))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()

    with caplog.at_level("INFO"):
        engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
        engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    assert any("HOLD" in r.message for r in caplog.records)


def test_max_position_size_rejection_is_logged(caplog):
    config = _bot_config(max_position_size=1)
    instrument = _instrument(config)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.BUY, size=5))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)
    session = MagicMock()

    with caplog.at_level("INFO"):
        engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
        engine.process_tick(config=config, instrument=instrument, strategy=strategy)

    assert any("REJECTED" in r.message for r in caplog.records)


def test_sell_fill_is_logged_with_pnl(caplog):
    config = _bot_config()
    instrument = _instrument(config, lot_size=100)
    strategy = _FixedSignalStrategy(Signal(action=SignalAction.SELL, size=1))
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()
    existing_position = SimpleNamespace(
        id=uuid.uuid4(), quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, status="open", closed_at=None,
    )
    session.get.return_value = existing_position

    with caplog.at_level("INFO"):
        engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
        engine.process_tick(
            config=config, instrument=instrument, strategy=strategy,
            current_position_qty=1, avg_entry_price=150000,
            paper_position_id=existing_position.id,
        )

    assert any("SELL FILLED" in r.message for r in caplog.records)
