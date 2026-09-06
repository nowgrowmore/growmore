"""wheel basket tables

Revision ID: 0021_wheel_basket
Revises: 0020_drop_virtual_capital

Adds the schema for the NSE stock-options wheel-basket paper-trading
strategy (docs/stock-options-results.md): a rotating basket of high-IV
stocks, ATM put wheel with an RSI-scaled basis buffer on the covered call.
Deliberately not built on bot_config/instruments -- that pair models exactly
one (strategy_id, instrument_id) with at most one open position, but this
strategy manages many symbols under one config, rotating which are held.
wheel_basket_configs plays bot_config's gating role instead.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_wheel_basket"
down_revision = "0020_drop_virtual_capital"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wheel_basket_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("strategies.id"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mode", sa.Text(), nullable=False, server_default="paper"),
        sa.Column("total_virtual_capital", sa.Numeric(), nullable=False),
        sa.Column("top_iv_frac", sa.Numeric(), nullable=False, server_default="0.33"),
        sa.Column("rotation_hysteresis_pct", sa.Numeric(), nullable=False, server_default="0.10"),
        sa.Column(
            "call_basis_buffer_tiers", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "wheel_basket_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wheel_basket_configs.id"),
            nullable=False,
        ),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("basis", sa.Numeric(), nullable=True),
        sa.Column("shares", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("lots", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(), nullable=False, server_default="0"),
    )

    op.create_table(
        "wheel_basket_legs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wheel_basket_positions.id"),
            nullable=False,
        ),
        sa.Column("cycle_expiry", sa.Date(), nullable=False),
        sa.Column("opt_type", sa.Text(), nullable=False),
        sa.Column("strike", sa.Numeric(), nullable=False),
        sa.Column("premium", sa.Numeric(), nullable=False),
        sa.Column("lots", sa.Numeric(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("called_away", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pnl", sa.Numeric(), nullable=True),
    )

    op.create_table(
        "wheel_basket_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wheel_basket_configs.id"),
            nullable=False,
        ),
        sa.Column("cycle_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("avg_iv", sa.Numeric(), nullable=True),
        sa.Column("iv_percentile", sa.Numeric(), nullable=True),
        sa.Column("rsi", sa.Numeric(), nullable=True),
        sa.Column("macd_bullish", sa.Boolean(), nullable=True),
        sa.Column("score", sa.Numeric(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("wheel_basket_selections")
    op.drop_table("wheel_basket_legs")
    op.drop_table("wheel_basket_positions")
    op.drop_table("wheel_basket_configs")
