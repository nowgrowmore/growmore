"""Add paper_orders.pnl

Realized P&L a sell fill locked in (lot-size-scaled), NULL for buy fills.
Needed to compute a strategy/instrument's cumulative *daily* P&L for the
daily_loss_limit risk guard -- PaperPosition.realized_pnl only tracks
cumulative-ever P&L, not a per-day breakdown, so the guard had nothing to
check against and never actually tripped in the real scheduler wiring
(growmore_bot/scheduler/run.py hardcoded cumulative_daily_pnl=0.0).

Revision ID: 0003_paper_order_pnl
Revises: 0002_instrument_lot_size
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_paper_order_pnl"
down_revision = "0002_instrument_lot_size"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_orders", sa.Column("pnl", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_orders", "pnl")
