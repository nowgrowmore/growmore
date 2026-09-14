"""mcx options selection expiry

Revision ID: 0025_mcx_options_expiry
Revises: 0024_mcx_options_snapshot

Adds `option_expiry` (Date, nullable) to `mcx_options_selections` -- the
expiry the engine was actually considering that cycle (from
`MCXCycleData.option_expiry`, already computed every cycle by
`live_data.fetch_cycle_data` regardless of whether an entry happens).
Without this, a cycle where the engine SKIPPED entry (unfavorable regime, no
strike cleared the OI/executability filter, etc.) left no record anywhere
of what expiry was even being evaluated -- only a cycle that actually wrote
an `MCXOptionsLeg` carried an expiry (`MCXOptionsLeg.cycle_expiry`), and
that leg-level field only ever exists for entries, not skips.

Additive/nullable -- no destructive change. Null on rows written before
this migration and this change to `mcx_options_engine.py`.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_mcx_options_expiry"
down_revision = "0024_mcx_options_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcx_options_selections", sa.Column("option_expiry", sa.Date(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("mcx_options_selections", "option_expiry")
