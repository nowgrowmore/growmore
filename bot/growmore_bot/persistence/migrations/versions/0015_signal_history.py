"""Add signal_history table

bot_signal_state is upsert-only by design (unique bot_config_id) -- "what did
the strategy see just now", not a log. This adds a genuine append-only
history so the dashboard can show a short recent-signal strip ("HOLD HOLD
HOLD BUY HOLD") per bot_config, one row written per tick regardless of
whether the signal was HOLD/BUY/SELL.

Revision ID: 0015_signal_history
Revises: 0014_close_reason_toggle
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_signal_history"
down_revision = "0014_close_reason_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signal_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bot_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bot_config.id"),
            nullable=False,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("ltp", sa.Numeric(), nullable=False),
    )
    op.create_index(
        "ix_signal_history_bot_config_checked_at",
        "signal_history",
        ["bot_config_id", "checked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_signal_history_bot_config_checked_at", table_name="signal_history")
    op.drop_table("signal_history")
