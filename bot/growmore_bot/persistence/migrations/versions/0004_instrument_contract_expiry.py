"""Add instruments.contract_expiry

Purely informational (dashboard display -- trade log/positions show the
current front-month contract's last trading day so the user doesn't need to
check Dhan's own UI for it). Not read by any trading/backtest logic. Nullable
since existing rows won't have it until backfilled from
config.DEFAULT_COMMODITY_UNIVERSE.

Revision ID: 0004_instrument_contract_expiry
Revises: 0003_paper_order_pnl
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_instrument_contract_expiry"
down_revision = "0003_paper_order_pnl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("instruments", sa.Column("contract_expiry", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("instruments", "contract_expiry")
