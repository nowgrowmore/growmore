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
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from growmore_bot.broker.dhan_client import Quote
from growmore_bot.paper.engine import PaperTradingEngine
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
    return hasattr(obj, "simulated_fill_price")


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
    instrument = _instrument(config, lot_size=100)  # Gold Mini: 100g/lot
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

    assert any("BUY signal filled" in r.message for r in caplog.records)


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

    assert any("SELL signal filled" in r.message for r in caplog.records)
