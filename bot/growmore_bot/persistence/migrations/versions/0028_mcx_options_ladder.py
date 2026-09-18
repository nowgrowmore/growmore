"""mcx options: multiple concurrent puts per config (weekly ladder)

Revision ID: 0028_mcx_options_ladder
Revises: 0027_mcx_options_risk_flags

The strategy moves from holding ONE short put per instrument at a time to a
laddered book: on the first MCX trading day of each week it attempts to sell
`weekly_new_puts_target` (2) NEW puts per instrument, on top of whatever is
already open, retrying every trading morning until that week's target is met.

Two things migration 0026 deliberately locked down have to be relaxed for
that, and one has to stay:

  - **DROPPED: `uq_mcx_options_positions_one_open_per_config`.** Several open
    positions per config is now the whole point. (0026 added this because the
    engine's `.first()` would silently orphan a second one; the engine now
    fans out over all of them, the way `wheel_basket_engine` always has.)
  - **DROPPED: `uq_mcx_options_selections_config_cycle`.** A cycle date can
    now carry several entry attempts, so "one cycle_date is one decision" is
    no longer true. `position_id` and `attempt_seq` below are what make each
    row attributable instead.
  - **KEPT: `uq_mcx_options_legs_one_unsettled_per_position`.** Still true and
    still worth enforcing -- each position runs its own single-leg
    put -> assignment -> covered-call chain.

**Accumulation is real and this migration is where its brakes live.** Monthly
expiries are ~4 weeks apart, so "2 per week" builds to 6-8 short puts per
instrument, most expiring on the SAME date. At the futures prices recorded in
production on 2026-09-14 (GOLDM 152,978 x lot_size 10 = ~15.3 lakh/lot;
SILVERM 236,895 x 5 = ~11.8 lakh/lot), six of each assigning together is
~1.63 crore of physical commodity -- on an account with no margin model in
the bot at all. `max_concurrent_positions`, `max_positions_per_expiry` and
`min_strike_separation_pct` below are defaulted permissively enough not to
change the requested behaviour, and exist to bound its tail. See
docs/pending-actions.md.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_mcx_options_ladder"
down_revision = "0027_mcx_options_risk_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- relax 0026's single-position invariants -------------------------
    op.drop_index("uq_mcx_options_positions_one_open_per_config", "mcx_options_positions")
    op.drop_constraint(
        "uq_mcx_options_selections_config_cycle", "mcx_options_selections", type_="unique"
    )

    # --- selection rows become per-ATTEMPT, not per-day -------------------
    op.add_column(
        "mcx_options_selections",
        sa.Column(
            "position_id",
            sa.Uuid(),
            sa.ForeignKey("mcx_options_positions.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "mcx_options_selections",
        sa.Column("attempt_seq", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    # One row per (config, date, attempt) -- the replacement invariant. Still
    # lets a re-run of the same cycle correct itself in place rather than
    # stacking duplicates, which is what 0026 was really protecting.
    op.create_unique_constraint(
        "uq_mcx_options_selections_config_cycle_attempt",
        "mcx_options_selections",
        ["config_id", "cycle_date", "attempt_seq"],
    )

    # --- which week's entry round a position belongs to -------------------
    # Derivable from `opened_at`, but that is a UTC timestamp and the week
    # boundary is an IST Monday -- storing it keeps the one query the weekly
    # target depends on free of timezone arithmetic.
    op.add_column(
        "mcx_options_positions", sa.Column("entry_week_start", sa.Date(), nullable=True)
    )
    op.create_index(
        "ix_mcx_options_positions_config_week",
        "mcx_options_positions",
        ["config_id", "entry_week_start"],
    )

    # --- ladder configuration --------------------------------------------
    op.add_column(
        "mcx_options_configs",
        sa.Column("weekly_new_puts_target", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "mcx_options_configs",
        sa.Column("entry_min_dte_days", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "mcx_options_configs",
        sa.Column("max_concurrent_positions", sa.Integer(), nullable=True, server_default="8"),
    )
    op.add_column(
        "mcx_options_configs",
        sa.Column("max_positions_per_expiry", sa.Integer(), nullable=True, server_default="4"),
    )
    op.add_column(
        "mcx_options_configs",
        sa.Column(
            "min_strike_separation_pct", sa.Numeric(), nullable=True, server_default="0.01"
        ),
    )
    for name in (
        "secondary_target_delta",
        "min_breakeven_cushion_pct",
        "min_iv_minus_realised_vol",
    ):
        op.add_column("mcx_options_configs", sa.Column(name, sa.Numeric(), nullable=True))
    op.add_column(
        "mcx_options_configs",
        sa.Column("min_volume", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    for name in (
        "min_volume",
        "min_iv_minus_realised_vol",
        "min_breakeven_cushion_pct",
        "secondary_target_delta",
        "min_strike_separation_pct",
        "max_positions_per_expiry",
        "max_concurrent_positions",
        "entry_min_dte_days",
        "weekly_new_puts_target",
    ):
        op.drop_column("mcx_options_configs", name)

    op.drop_index("ix_mcx_options_positions_config_week", "mcx_options_positions")
    op.drop_column("mcx_options_positions", "entry_week_start")

    op.drop_constraint(
        "uq_mcx_options_selections_config_cycle_attempt",
        "mcx_options_selections",
        type_="unique",
    )
    op.drop_column("mcx_options_selections", "attempt_seq")
    op.drop_column("mcx_options_selections", "position_id")

    # Restoring 0026's per-day uniqueness means collapsing the per-attempt
    # rows back to one per cycle date first -- the same courtesy 0026's own
    # upgrade does, and without it this downgrade simply fails on any book
    # that has run a weekly round. The newest row per (config, date) is the
    # one kept, matching 0026's rule that a re-run's decision is the one that
    # stands.
    op.execute(
        """
        DELETE FROM mcx_options_selections a
        USING mcx_options_selections b
        WHERE a.config_id = b.config_id
          AND a.cycle_date = b.cycle_date
          AND (a.created_at, a.id) < (b.created_at, b.id)
        """
    )

    # The position index below is deliberately NOT given the same treatment:
    # collapsing a laddered book back to one open position per config would
    # mean CLOSING real positions, which a schema downgrade has no business
    # doing silently. On a book with several open positions this will fail
    # loudly, which is the correct outcome -- flatten the book first.
    op.create_unique_constraint(
        "uq_mcx_options_selections_config_cycle",
        "mcx_options_selections",
        ["config_id", "cycle_date"],
    )
    op.create_index(
        "uq_mcx_options_positions_one_open_per_config",
        "mcx_options_positions",
        ["config_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
