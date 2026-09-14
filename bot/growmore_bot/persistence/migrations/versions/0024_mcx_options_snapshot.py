"""mcx options selection daily snapshot

Revision ID: 0024_mcx_options_snapshot
Revises: 0023_mcx_options_rollover

Turns `mcx_options_selections` into a genuine daily snapshot log, not just
an entry-decision audit trail (see growmore_bot/mcx_options/mcx_options_engine.py's
`run_cycle`, updated in this same change to populate these on every cycle,
including hold days that previously wrote no market/position context at
all). All additive/nullable -- no destructive changes:

  - `futures_price` (Numeric) -- always populated going forward, every
    cycle, entry or hold. Null on rows written before this migration.
  - `position_state` (Text) -- snapshot of MCXOptionsPosition.state at the
    time of this cycle (flat|long_futures|closed|None if no position exists
    yet), so a hold-day row is self-contained without a join back through
    position history (which only tracks CURRENT state).
  - `position_basis` (Numeric) -- snapshot of the position's basis, if any,
    at this cycle.
  - `position_unrealized_pnl` (Numeric) -- snapshot of unrealized P&L at
    this cycle (post any M2M/roll this cycle already applied).
  - `candidates_considered` (JSON/JSONB) -- a JSON array of
    {strike, delta, oi, ltp} objects for EVERY candidate strike evaluated
    this cycle (not just the winner), populated whenever the engine reached
    the strike-selection step.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_mcx_options_snapshot"
down_revision = "0023_mcx_options_rollover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcx_options_selections", sa.Column("futures_price", sa.Numeric(), nullable=True)
    )
    op.add_column(
        "mcx_options_selections", sa.Column("position_state", sa.Text(), nullable=True)
    )
    op.add_column(
        "mcx_options_selections", sa.Column("position_basis", sa.Numeric(), nullable=True)
    )
    op.add_column(
        "mcx_options_selections",
        sa.Column("position_unrealized_pnl", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "mcx_options_selections",
        sa.Column(
            "candidates_considered",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("mcx_options_selections", "candidates_considered")
    op.drop_column("mcx_options_selections", "position_unrealized_pnl")
    op.drop_column("mcx_options_selections", "position_basis")
    op.drop_column("mcx_options_selections", "position_state")
    op.drop_column("mcx_options_selections", "futures_price")
