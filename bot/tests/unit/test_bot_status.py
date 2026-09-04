"""Tests for growmore_bot.scheduler.run._update_bot_status.

Singleton row upserted every tick regardless of market hours, so the
dashboard can show process health ("last tick N minutes ago") and whether
the real-money kill switch is armed, without SSHing into the host.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.broker.dhan_client import FundLimits
from growmore_bot.persistence.models import Base, BotStatus
from growmore_bot.scheduler.run import _update_bot_status


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_creates_the_singleton_row_when_none_exists(session):
    dhan_client = MagicMock()
    dhan_client.get_fund_limits.return_value = FundLimits(
        available_balance=217009.94, utilized_amount=32401.0, withdrawable_balance=217008.94
    )
    now = datetime.now(timezone.utc)

    _update_bot_status(session, live_trading_enabled=True, dhan_client=dhan_client, now=now)
    session.commit()

    rows = session.query(BotStatus).all()
    assert len(rows) == 1
    assert rows[0].live_trading_enabled is True
    assert rows[0].last_tick_at.replace(tzinfo=None) == now.replace(tzinfo=None)
    assert float(rows[0].available_balance) == pytest.approx(217009.94)
    assert float(rows[0].utilized_margin) == pytest.approx(32401.0)


def test_updates_the_existing_singleton_row_in_place(session):
    dhan_client = MagicMock()
    dhan_client.get_fund_limits.return_value = FundLimits(
        available_balance=100.0, utilized_amount=10.0, withdrawable_balance=90.0
    )
    first_tick = datetime.now(timezone.utc)
    _update_bot_status(session, live_trading_enabled=False, dhan_client=dhan_client, now=first_tick)
    session.commit()

    dhan_client.get_fund_limits.return_value = FundLimits(
        available_balance=200.0, utilized_amount=20.0, withdrawable_balance=180.0
    )
    second_tick = datetime.now(timezone.utc)
    _update_bot_status(session, live_trading_enabled=True, dhan_client=dhan_client, now=second_tick)
    session.commit()

    rows = session.query(BotStatus).all()
    assert len(rows) == 1  # still exactly one row -- upserted, not appended
    assert rows[0].live_trading_enabled is True
    assert rows[0].last_tick_at.replace(tzinfo=None) == second_tick.replace(tzinfo=None)
    assert float(rows[0].available_balance) == pytest.approx(200.0)


def test_swallows_a_failed_fund_limits_fetch_and_keeps_last_known_balance(session):
    dhan_client = MagicMock()
    dhan_client.get_fund_limits.return_value = FundLimits(
        available_balance=100.0, utilized_amount=10.0, withdrawable_balance=90.0
    )
    _update_bot_status(
        session, live_trading_enabled=False, dhan_client=dhan_client, now=datetime.now(timezone.utc)
    )
    session.commit()

    dhan_client.get_fund_limits.side_effect = RuntimeError("boom")
    second_tick = datetime.now(timezone.utc)
    # Must not raise -- a failed balance fetch shouldn't hide that the
    # process is alive.
    _update_bot_status(session, live_trading_enabled=True, dhan_client=dhan_client, now=second_tick)
    session.commit()

    rows = session.query(BotStatus).all()
    assert len(rows) == 1
    assert rows[0].last_tick_at.replace(tzinfo=None) == second_tick.replace(tzinfo=None)  # tick still recorded
    assert rows[0].live_trading_enabled is True  # flag still recorded
    assert float(rows[0].available_balance) == pytest.approx(100.0)  # kept last known
