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
from datetime import date, datetime, timezone
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


# ---------------------------------------------------------------------------
# Migrations 0023/0024/0025/0026 had NO integration coverage at all before the
# independent review of 2026-09-15 -- this module only ever asserted
# `mcx_options_configs`/`_positions` server defaults from 0022, and never
# touched `mcx_options_legs` or `mcx_options_selections`. Everything below
# runs against real Postgres, where the JSONB variant, the partial unique
# indexes and the CHECK constraints actually mean something (the model tests
# all run on SQLite).
# ---------------------------------------------------------------------------


def _config_row(session, symbol: str = "GOLDM"):
    from growmore_bot.persistence.models import MCXOptionsConfig, Strategy

    strategy = Strategy(id=uuid.uuid4(), name=f"mcx_{uuid.uuid4().hex[:8]}", version="1.0", params={})
    session.add(strategy)
    session.flush()
    cfg = MCXOptionsConfig(id=uuid.uuid4(), strategy_id=strategy.id, symbol=symbol, lots=1)
    session.add(cfg)
    session.flush()
    return cfg


def _position_row(session, cfg, *, flush: bool = True, **overrides):
    from growmore_bot.persistence.models import MCXOptionsPosition

    fields = dict(
        id=uuid.uuid4(), config_id=cfg.id, state="flat",
        opened_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    position = MCXOptionsPosition(**fields)
    session.add(position)
    if flush:
        session.flush()
    return position


def test_roll_leg_can_have_null_strike_and_premium(migrated_engine):
    """Migration 0023 relaxed `strike`/`premium` to nullable, and 0026's
    CHECK now ties that nullability to the one case it exists for.
    """
    from growmore_bot.persistence.models import MCXOptionsLeg

    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        position = _position_row(session, cfg, state="long_futures", basis=6000.0, futures_qty=100)
        leg = MCXOptionsLeg(
            id=uuid.uuid4(), position_id=position.id, cycle_expiry=date(2026, 10, 28),
            opt_type="ROLL", strike=None, premium=None, lots=1, action="roll",
            opened_at=datetime.now(timezone.utc), settled_at=datetime.now(timezone.utc),
            pnl=-450.0,
        )
        session.add(leg)
        session.commit()
        session.refresh(leg)

        assert leg.strike is None and leg.premium is None

        # Clean up before leaving: 0023's own `downgrade()` restores
        # `premium` to NOT NULL, which the module fixture's
        # `command.downgrade(..., "base")` teardown would trip over if this
        # row (legitimately null-premium under 0023+) were left behind.
        session.delete(leg)
        session.commit()


def test_option_leg_may_not_have_a_null_strike(migrated_engine):
    """0026's `ck_mcx_options_legs_roll_has_no_strike`: before it, a PE/CE leg
    with a null strike was schema-legal and only a `python -O`-strippable
    `assert` stood in the way.
    """
    from sqlalchemy.exc import IntegrityError

    from growmore_bot.persistence.models import MCXOptionsLeg

    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        position = _position_row(session, cfg)
        session.add(
            MCXOptionsLeg(
                id=uuid.uuid4(), position_id=position.id, cycle_expiry=date(2026, 9, 24),
                opt_type="PE", strike=None, premium=50.0, lots=1, action="sell_put",
                opened_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_selection_snapshot_columns_exist_and_candidates_round_trip_as_jsonb(migrated_engine):
    """Migrations 0024 (snapshot columns + `candidates_considered` JSONB) and
    0025 (`option_expiry`) -- neither had any integration coverage.
    """
    from growmore_bot.persistence.models import MCXOptionsSelection

    candidates = [{"strike": 6000.0, "delta": -0.3, "oi": 5000.0, "ltp": 42.5}]
    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        selection = MCXOptionsSelection(
            id=uuid.uuid4(), config_id=cfg.id, cycle_date=date(2026, 9, 14),
            regime="consolidating", target_delta=0.30, selected_strike=6000.0,
            reason="entered", futures_price=6050.0, position_state="flat",
            position_basis=None, position_unrealized_pnl=0,
            candidates_considered=candidates, option_expiry=date(2026, 9, 24),
        )
        session.add(selection)
        session.commit()
        session.refresh(selection)

        assert selection.candidates_considered == candidates
        assert selection.option_expiry == date(2026, 9, 24)
        assert float(selection.position_unrealized_pnl) == 0.0
        session.rollback()


def test_one_selection_row_per_config_and_cycle_date(migrated_engine):
    """0026's `uq_mcx_options_selections_config_cycle`. Three production
    cycles were hand-run on 2026-09-14 and each appended its own row for that
    same date -- one cycle_date is one decision.
    """
    from sqlalchemy.exc import IntegrityError

    from growmore_bot.persistence.models import MCXOptionsSelection

    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        for reason in ("first run", "second run"):
            session.add(
                MCXOptionsSelection(
                    id=uuid.uuid4(), config_id=cfg.id, cycle_date=date(2026, 9, 14),
                    reason=reason,
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_only_one_open_position_per_config(migrated_engine):
    """0026's partial unique index. A second open position would be orphaned
    forever -- `run_cycle` only ever acts on one.
    """
    from sqlalchemy.exc import IntegrityError

    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        _position_row(session, cfg, status="open", flush=False)
        _position_row(session, cfg, status="open", flush=False)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    # ...but any number of CLOSED positions alongside one open one is normal:
    # a put expiring OTM closes its position and the next cycle opens a new one.
    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        _position_row(session, cfg, status="closed", flush=False)
        _position_row(session, cfg, status="closed", flush=False)
        _position_row(session, cfg, status="open", flush=False)
        session.commit()


def test_only_one_unsettled_leg_per_position(migrated_engine):
    """0026's other partial unique index -- the state that used to raise a
    bare MultipleResultsFound and silently wedge that commodity every day.
    """
    from sqlalchemy.exc import IntegrityError

    from growmore_bot.persistence.models import MCXOptionsLeg

    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        position = _position_row(session, cfg)
        for strike in (5900.0, 5800.0):
            session.add(
                MCXOptionsLeg(
                    id=uuid.uuid4(), position_id=position.id, cycle_expiry=date(2026, 9, 24),
                    opt_type="PE", strike=strike, premium=50.0, lots=1, action="sell_put",
                    opened_at=datetime.now(timezone.utc),
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_mode_is_constrained_to_paper_or_live(migrated_engine):
    """0026's `ck_mcx_options_configs_mode`. `mode` is the live-trading gate
    and was previously unconstrained free text.
    """
    from sqlalchemy import text as sa_text
    from sqlalchemy.exc import IntegrityError

    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                sa_text("UPDATE mcx_options_configs SET mode = 'LIVE!' WHERE id = :id"),
                {"id": cfg.id},
            )
            session.commit()
        session.rollback()


def test_the_hot_query_indexes_exist(migrated_engine):
    """0026's indexes cover exactly the predicates the engine and the
    /mcx-options dashboard query on -- before them every one was a seq scan
    plus a sort, re-run on every 60s dashboard refresh.
    """
    from sqlalchemy import text as sa_text

    with migrated_engine.connect() as conn:
        names = {
            row[0]
            for row in conn.execute(
                sa_text(
                    "SELECT indexname FROM pg_indexes WHERE tablename LIKE 'mcx_options_%'"
                )
            )
        }

    assert "ix_mcx_options_selections_config_cycle" in names
    assert "ix_mcx_options_positions_config_opened" in names
    assert "ix_mcx_options_legs_position" in names


def test_stop_legs_are_permitted_and_risk_flags_default_to_off(migrated_engine):
    """Migration 0027. Every risk/selection flag must arrive NULL/false, since
    the whole point is that applying the migration changes the strategy's
    behaviour in no way at all until the owner sets one. 0027 also widens
    0026's leg CHECKs to admit the STOP leg a stop-out records.
    """
    from growmore_bot.persistence.models import MCXOptionsLeg

    with Session(migrated_engine) as session:
        cfg = _config_row(session)
        session.commit()
        session.refresh(cfg)

        assert cfg.stop_loss_premium_multiple is None
        assert cfg.min_dte_days is None
        assert cfg.max_dte_days is None
        assert cfg.min_credit_pct_of_strike is None
        assert cfg.max_relative_spread is None
        assert cfg.fallback_sigma is None
        assert cfg.use_bid_for_entry_premium is False

        position = _position_row(session, cfg, state="long_futures", basis=6000.0, futures_qty=100)
        leg = MCXOptionsLeg(
            id=uuid.uuid4(), position_id=position.id, cycle_expiry=date(2026, 9, 24),
            opt_type="STOP", strike=None, premium=None, lots=1, action="stop_loss",
            opened_at=datetime.now(timezone.utc), settled_at=datetime.now(timezone.utc),
            pnl=-20000.0,
        )
        session.add(leg)
        session.commit()
        session.refresh(leg)
        assert leg.opt_type == "STOP"

        # Same teardown care as the ROLL-leg test above: 0023's downgrade
        # restores `premium` to NOT NULL.
        session.delete(leg)
        session.commit()
