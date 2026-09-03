"""Add instruments.lot_size

Real contract trading unit (e.g. Gold Mini=100, Copper=2500), looked up from
MCX's official contract specs. Without this, BacktestEngine treated every
instrument as "1 raw unit of the price series," making Sharpe/Max Drawdown
incomparable across commodities at very different price levels (confirmed
2026-09-03: base metals showed implausibly tiny drawdowns purely from this
scaling bug, not real safety).

0001_initial_schema is already applied against the real Neon database (which
now holds real persisted backtest data), so this ships as its own migration
rather than editing 0001 in place.

Revision ID: 0002_instrument_lot_size
Revises: 0001_initial_schema
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_instrument_lot_size"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "instruments",
        sa.Column("lot_size", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("instruments", "lot_size")
