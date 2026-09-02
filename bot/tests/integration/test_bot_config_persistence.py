"""Regression test for bot_config.updated_at: must default on insert and bump
on update without every caller having to set it explicitly.

Found while manually seeding data against a real Postgres for a dashboard
smoke test -- creating a BotConfig without setting updated_at raised
NotNullViolation, and growmore_bot/paper/engine.py's daily-loss-limit trip
flips `enabled = False` without touching updated_at, which would silently
break the "when was this last changed" guarantee documented in
docs/db-schema.md. Fixed via server_default=func.now()/onupdate=func.now()
on the column (growmore_bot/persistence/models.py) plus the matching Alembic
migration. Skips (like test_backtest_integration.py) if Postgres isn't
reachable.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.persistence.db import normalize_database_url


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_test = Path(__file__).parents[2] / ".env.test"
    if env_test.exists():
        for line in env_test.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return "postgresql+psycopg://postgres:postgres@localhost:5432/growmore_test"


DATABASE_URL = _database_url()


def _postgres_reachable(url: str) -> bool:
    try:
        engine = create_engine(normalize_database_url(url), connect_args={"connect_timeout": 3})
        with engine.connect():
            return True
    except Exception:
        return False
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(DATABASE_URL),
    reason=f"Postgres not reachable at {DATABASE_URL!r} -- see bot/README.md",
)


@pytest.fixture(scope="module")
def migrated_engine():
    from alembic import command
    from alembic.config import Config

    bot_root = Path(__file__).parents[2]
    alembic_cfg = Config(str(bot_root / "alembic.ini"))
    alembic_cfg.set_main_option(
        "script_location", str(bot_root / "growmore_bot" / "persistence" / "migrations")
    )
    os.environ["DATABASE_URL"] = DATABASE_URL

    command.upgrade(alembic_cfg, "head")
    engine = create_engine(normalize_database_url(DATABASE_URL), future=True)
    try:
        yield engine
    finally:
        command.downgrade(alembic_cfg, "base")
        engine.dispose()


def test_bot_config_updated_at_defaults_and_bumps_on_update(migrated_engine):
    from growmore_bot.persistence.models import BotConfig, Instrument, Strategy

    with Session(migrated_engine) as session:
        instrument = Instrument(
            id=uuid.uuid4(),
            symbol="GOLDM",
            exchange_segment="MCX_COMM",
            security_id="TEST-SECURITY-ID",
            name="Gold Mini (test fixture)",
        )
        strategy_row = Strategy(id=uuid.uuid4(), name="test_strategy", version="1.0", params={})
        session.add_all([instrument, strategy_row])
        session.flush()

        # No updated_at supplied -- must not raise NotNullViolation.
        config = BotConfig(
            id=uuid.uuid4(),
            strategy_id=strategy_row.id,
            instrument_id=instrument.id,
            enabled=True,
            virtual_capital=500_000,
            max_position_size=10,
            daily_loss_limit=5_000,
        )
        session.add(config)
        session.commit()

        assert config.updated_at is not None
        first_updated_at = config.updated_at

        time.sleep(1.1)  # Postgres now() has second resolution here; ensure a visible delta.
        config.enabled = False
        session.commit()
        session.refresh(config)

        assert config.updated_at > first_updated_at
