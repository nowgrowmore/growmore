"""mcx options tables

Revision ID: 0022_mcx_options
Revises: 0021_wheel_basket

Adds the schema for the MCX Goldmini/Silvermini options-selling paper-trading
strategy (bot/research/mcx_options/engine.py): sell OTM puts for premium,
take assignment into a futures position if the put finishes ITM, then sell
covered calls against that futures position until it is either called away
or the option cycle ends. The direct MCX analog of 0021_wheel_basket.py, but
adapted for commodity-options mechanics: assignment settles into a FUTURES
position (not shares) with its own contract expiry independent of the
option's expiry, and there is no cross-sectional universe/rotation concept
-- one config row per commodity (GOLDM/SILVERM), not a rotating basket.

No stop-loss anywhere in this state machine, by deliberate design: a short
leg is only ever closed by expiry (OTM), assignment (ITM put), or being
called away (ITM call). This is a schema-only migration; the engine that
enforces that constraint is a later phase.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_mcx_options"
down_revision = "0021_wheel_basket"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcx_options_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("strategies.id"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mode", sa.Text(), nullable=False, server_default="paper"),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("lots", sa.Integer(), nullable=False),
        sa.Column(
            "consolidating_target_delta", sa.Numeric(), nullable=False, server_default="0.30"
        ),
        sa.Column(
            "trend_favorable_target_delta", sa.Numeric(), nullable=False, server_default="0.50"
        ),
        sa.Column("min_open_interest", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "margin_multiple_of_premium", sa.Numeric(), nullable=False, server_default="3.0"
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "mcx_options_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcx_options_configs.id"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("basis", sa.Numeric(), nullable=True),
        sa.Column("futures_qty", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("futures_contract_expiry", sa.Date(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(), nullable=False, server_default="0"),
    )

    op.create_table(
        "mcx_options_legs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcx_options_positions.id"),
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
        "mcx_options_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcx_options_configs.id"),
            nullable=False,
        ),
        sa.Column("cycle_date", sa.Date(), nullable=False),
        sa.Column("regime", sa.Text(), nullable=True),
        sa.Column("target_delta", sa.Numeric(), nullable=True),
        sa.Column("selected_strike", sa.Numeric(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("mcx_options_selections")
    op.drop_table("mcx_options_legs")
    op.drop_table("mcx_options_positions")
    op.drop_table("mcx_options_configs")
