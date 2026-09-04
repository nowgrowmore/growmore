"""Add bot_config auto-close retry columns

A tripped daily_loss_limit guard disables the config immediately -- but the
scheduler only ever ticks `enabled=True` configs, so a failed auto-close
order previously had no way to ever be retried; the real position was left
open with no automatic path back to flat. These columns let
`LiveTradingEngine.retry_pending_auto_close` keep retrying (geometric
backoff, never gives up) on a disabled config's still-open real position
without re-enabling it for fresh trades.

Revision ID: 0011_auto_close_retry
Revises: 0010_daily_pnl
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_auto_close_retry"
down_revision = "0010_daily_pnl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_config",
        sa.Column("pending_auto_close", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "bot_config",
        sa.Column("auto_close_retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "bot_config",
        sa.Column("auto_close_next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_config", "auto_close_next_retry_at")
    op.drop_column("bot_config", "auto_close_retry_count")
    op.drop_column("bot_config", "pending_auto_close")
