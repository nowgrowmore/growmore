"""Unit tests for growmore_bot.paper.engine.PaperTradingEngine.

Everything (Dhan client, DB session) is mocked -- no real network or DB
calls. Covers: fill simulation at fetched LTP, max_position_size guard,
and daily_loss_limit tripping (which must disable the config row and write
an audit_log entry).
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


def _instrument(config):
    return SimpleNamespace(
        id=config.instrument_id,
        symbol="GOLDM",
        exchange_segment="MCX_COMM",
        security_id="123",
    )


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


def _looks_like_order(obj) -> bool:
    return hasattr(obj, "simulated_fill_price")


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
