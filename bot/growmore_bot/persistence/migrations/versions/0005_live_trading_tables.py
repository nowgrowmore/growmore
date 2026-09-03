"""Add bot_config.mode and live_positions/live_orders tables

Prerequisite schema for the (still off-by-default -- see
growmore_bot/broker/dhan_order_client.py and CLAUDE.md non-negotiables) real
order-placement path. `bot_config.mode` defaults to "paper" for every
existing row -- nothing starts trading for real just from this migration.
live_positions/live_orders mirror paper_positions/paper_orders exactly (plus
broker_order_id/order_status on live_orders) but are kept as fully separate
tables so real and simulated data can never be confused.

Revision ID: 0005_live_trading_tables
Revises: 0004_instrument_contract_expiry
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_live_trading_tables"
down_revision = "0004_instrument_contract_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_config", sa.Column("mode", sa.Text(), nullable=False, server_default="paper")
    )

    op.create_table(
        "live_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("strategies.id"),
            nullable=False,
        ),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("avg_entry_price", sa.Numeric(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "live_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "live_position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("live_positions.id"),
            nullable=False,
        ),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("broker_order_id", sa.Text(), nullable=False),
        sa.Column("order_status", sa.Text(), nullable=False),
        sa.Column("fill_price", sa.Numeric(), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pnl", sa.Numeric(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("live_orders")
    op.drop_table("live_positions")
    op.drop_column("bot_config", "mode")
