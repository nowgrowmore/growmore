"""Add bot_signal_state.crossing_state

Persists a strategy's crossing/threshold-recovery reference state (e.g.
MACD's "was macd above signal") across ticks, distinct from the
display-only `indicators` column. Without this, a fresh strategy instance
rebuilt each tick and warmed up from history ending yesterday always
compares against yesterday's close, so a signal meant to fire once
re-fires every tick for the rest of the day the live value stays past
threshold. Found live 2026-09-04 -- see Strategy.get_state_snapshot's
docstring in bot/growmore_bot/strategies/base.py.

Revision ID: 0007_crossing_state
Revises: 0006_bot_signal_state

Note: revision ids must stay <=32 chars -- alembic_version.version_num is
varchar(32), and a too-long id fails (atomically, no partial damage) only
when Alembic tries to stamp it, i.e. after any DDL in the same migration
already ran -- found live 2026-09-04 applying this exact migration.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_crossing_state"
down_revision = "0006_bot_signal_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_signal_state",
        sa.Column("crossing_state", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("bot_signal_state", "crossing_state")
