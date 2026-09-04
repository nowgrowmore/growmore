"""Add bot_signal_state.last_max_position_rejection_logged_at

Found live 2026-09-04: a strategy's indicator can genuinely chop back and
forth across its crossing threshold during a volatile session (e.g. market
open), producing a fresh, real BUY signal each time -- correctly rejected
by the max_position_size guard every time since a position is already open,
but that meant an audit_log entry every ~5 minutes for the same "already at
max, can't add more" fact for nearly an hour. Real signal, but low marginal
value once it's already been recorded once recently -- this column lets the
engines throttle repeat entries for the same bot_config to once per 30
minutes without needing to touch the strategy's own crossing detection
(which is working correctly) or silently drop the underlying bot.log
warning (still logged every single time).

Revision ID: 0012_rejection_throttle

Note: revision ids must be <=32 chars -- alembic_version.version_num is
varchar(32). A too-long id (originally
"0012_max_position_rejection_throttle", 37 chars) fails atomically at the
version-stamp step, after the migration's own DDL has already run in the
same transaction -- Postgres rolls the whole thing back cleanly (confirmed
2026-09-04), but it's a wasted round trip. Keep ids short.

Revises: 0011_auto_close_retry
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_rejection_throttle"
down_revision = "0011_auto_close_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_signal_state",
        sa.Column("last_max_position_rejection_logged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_signal_state", "last_max_position_rejection_logged_at")
