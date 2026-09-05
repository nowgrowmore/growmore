"""Add risk_state to paper/live positions, and a real stop-order tracker

1. `risk_state` (paper_positions, live_positions) -- RiskManagedStrategy's
   per-trade stop/trail state is designed to round-trip through
   position_state["risk"] every tick (BacktestEngine already does this
   correctly), but neither paper/engine.py nor live/engine.py ever
   constructed a "risk" key at all -- any risk_managed config running in
   paper or live trading had its computed stop silently reset every single
   tick. This column is the fix: the engines now persist and restore it.
2. `stop_order_id` / `stop_order_trigger_price` (live_positions only) --
   tracks a real resting STOP_LOSS_MARKET order placed at Dhan for a
   risk-managed live position, so the exchange enforces the stop instantly
   instead of the bot only detecting a breach on its next 5-minute poll.

Revision ID: 0017_risk_state
Revises: 0016_costs_and_capital
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_risk_state"
down_revision = "0016_costs_and_capital"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_positions",
        sa.Column("risk_state", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "live_positions",
        sa.Column("risk_state", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column("live_positions", sa.Column("stop_order_id", sa.Text(), nullable=True))
    op.add_column(
        "live_positions", sa.Column("stop_order_trigger_price", sa.Numeric(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("live_positions", "stop_order_trigger_price")
    op.drop_column("live_positions", "stop_order_id")
    op.drop_column("live_positions", "risk_state")
    op.drop_column("paper_positions", "risk_state")
