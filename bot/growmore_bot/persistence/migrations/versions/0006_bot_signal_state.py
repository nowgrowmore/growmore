"""Add bot_signal_state table

One row per bot_config, upserted every tick with the strategy's most recent
signal (HOLD/BUY/SELL) and computed indicator values -- lets the dashboard
show live strategy status without grepping bot.log. Purely additive/
informational; no trading logic reads this table.

Revision ID: 0006_bot_signal_state
Revises: 0005_live_trading_tables
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_bot_signal_state"
down_revision = "0005_live_trading_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_signal_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bot_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bot_config.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("last_signal", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ltp", sa.Numeric(), nullable=False),
        sa.Column("indicators", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("bot_signal_state")
