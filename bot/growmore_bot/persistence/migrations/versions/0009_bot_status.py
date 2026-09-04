"""Add bot_status table

Singleton row upserted by the scheduler every tick (regardless of market
hours) so the dashboard can show whether the process is alive and whether
the real-money kill switch is armed, without SSHing into the host. Also
carries the real account balance (read-only GET /fundlimit).

Revision ID: 0009_bot_status
Revises: 0008_prev_close
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_bot_status"
down_revision = "0008_prev_close"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_status",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("live_trading_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_balance", sa.Numeric(), nullable=True),
        sa.Column("utilized_margin", sa.Numeric(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("bot_status")
