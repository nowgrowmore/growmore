"""Integration test for the 0022_mcx_options Alembic migration.

Mirrors tests/integration/test_bot_config_persistence.py's skip-if-unreachable
pattern exactly: runs `alembic upgrade head` against DATABASE_URL (local/
dockerized Postgres only, per bot/README.md and .env.test's safe default of
localhost:5432/growmore_test -- NEVER a real/production database), then
downgrades back to base on teardown. Skips gracefully if nothing is
listening, so this is safe to run anywhere including CI without a live DB.

IMPORTANT: this test (like the migration it exercises) must never be pointed
at the real Neon production database. DATABASE_URL here comes only from the
environment or the checked-in .env.test safe default.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
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


def test_mcx_options_tables_created_with_expected_defaults(migrated_engine):
    from growmore_bot.persistence.models import MCXOptionsConfig, MCXOptionsPosition, Strategy

    with Session(migrated_engine) as session:
        strategy = Strategy(
            id=uuid.uuid4(), name="mcx_options_migration_test", version="1.0", params={}
        )
        session.add(strategy)
        session.flush()

        cfg = MCXOptionsConfig(
            id=uuid.uuid4(),
            strategy_id=strategy.id,
            symbol="GOLDM",
            lots=1,
        )
        session.add(cfg)
        session.commit()
        session.refresh(cfg)

        assert cfg.enabled is False
        assert cfg.mode == "paper"
        assert cfg.updated_at is not None

        position = MCXOptionsPosition(
            id=uuid.uuid4(),
            config_id=cfg.id,
            state="flat",
            opened_at=datetime.now(timezone.utc),
        )
        session.add(position)
        session.commit()
        session.refresh(position)

        assert position.status == "open"
        assert float(position.futures_qty) == 0
        assert float(position.realized_pnl) == 0
