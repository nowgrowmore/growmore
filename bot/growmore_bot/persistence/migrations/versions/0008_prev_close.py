"""Add bot_signal_state.prev_close

Previous trading day's close (Dhan's quote "close" field -- during live
hours this can only be yesterday's close, confirmed against a real quote
2026-09-04). Lets the dashboard show today's % change for an instrument
without needing its own live Dhan connection.

Revision ID: 0008_prev_close
Revises: 0007_crossing_state
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_prev_close"
down_revision = "0007_crossing_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_signal_state", sa.Column("prev_close", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_signal_state", "prev_close")
