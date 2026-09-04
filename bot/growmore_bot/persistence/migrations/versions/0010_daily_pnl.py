"""Add bot_signal_state.daily_pnl

Today's cumulative realized P&L for this (strategy, instrument) pair -- the
same value the daily_loss_limit risk guard checks against every tick. Lets
the dashboard show progress toward the limit before it trips, not just
after.

Revision ID: 0010_daily_pnl
Revises: 0009_bot_status
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_daily_pnl"
down_revision = "0009_bot_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_signal_state", sa.Column("daily_pnl", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_signal_state", "daily_pnl")
