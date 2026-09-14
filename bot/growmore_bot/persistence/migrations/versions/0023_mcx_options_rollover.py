"""mcx options futures rollover

Revision ID: 0023_mcx_options_rollover
Revises: 0022_mcx_options

Small, additive schema support for growmore_bot/mcx_options/mcx_options_engine.py's
now-implemented futures contract rollover (reusing the existing Instrument-
level rollover mechanism in growmore_bot/scheduler/contract_rollover.py --
see that engine's module docstring for the full design):

  1. `mcx_options_legs.strike`/`.premium` become nullable -- a roll event is
     recorded as its own `MCXOptionsLeg` row (`opt_type="ROLL"`,
     `action="roll"`), and a roll is not an option leg at all: it has no
     strike and no premium.
  2. `mcx_options_configs.futures_roll_cost_per_lot` (Numeric, default 0) --
     a flat placeholder for a futures roll's bid/ask spread, rupees per lot,
     matching research/mcx_options/engine.py's
     EngineConfig.futures_roll_cost_per_lot default. NOT a real sourced
     roll-spread figure -- see that module's own documented simplification.

`opt_type="ROLL"` and `action="roll"` are plain Text values (this table has
no DB-level enum), so nothing else here needs a migration to support them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_mcx_options_rollover"
down_revision = "0022_mcx_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("mcx_options_legs", "strike", existing_type=sa.Numeric(), nullable=True)
    op.alter_column("mcx_options_legs", "premium", existing_type=sa.Numeric(), nullable=True)
    op.add_column(
        "mcx_options_configs",
        sa.Column("futures_roll_cost_per_lot", sa.Numeric(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("mcx_options_configs", "futures_roll_cost_per_lot")
    op.alter_column("mcx_options_legs", "premium", existing_type=sa.Numeric(), nullable=False)
    op.alter_column("mcx_options_legs", "strike", existing_type=sa.Numeric(), nullable=False)
