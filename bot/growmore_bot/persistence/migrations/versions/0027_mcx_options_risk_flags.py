"""mcx options risk/selection flags, all default-OFF

Revision ID: 0027_mcx_options_risk_flags
Revises: 0026_mcx_options_guards

The independent code review of 2026-09-15 separated two kinds of finding:
correctness bugs (fixed outright, migration 0026) and STRATEGY weaknesses,
which are judgement calls the account owner has to make -- they change what
the bot does with real (paper) money and they break comparability with the
offline backtest in `bot/research/mcx_options/`.

This migration adds the columns for the second kind. **Every one of them is
NULL/false by default and every code path treats that as "disabled"**, so
applying this migration changes the strategy's behaviour in no way at all
until the owner sets a value. See docs/pending-actions.md for what each one
is for and what enabling it would mean.

  - `stop_loss_premium_multiple` -- flatten a long-futures position once its
    unrealized loss exceeds N x the premium collected on that position. The
    strategy is deliberately stop-less today (see
    `research/mcx_options/engine.py`'s "Do not add early-exit logic here"),
    but that design was validated on a backtest whose option costs were
    placeholders, and an unhedged short gold put is the single largest tail
    risk in this system.
  - `min_dte_days` / `max_dte_days` -- a real days-to-expiry preference. The
    engine currently takes whatever the nearest expiry is, 1 day or 45, with
    very different gamma and premium characteristics. (Distinct from
    `live_data.MIN_OPTION_DTE_DAYS`, which is a hard correctness floor that
    always applies and is not a tunable.)
  - `min_credit_pct_of_strike` -- refuse to sell for a premium below this
    fraction of the strike. There is no richness gate at all today: the
    engine sells at any implied vol, so it writes the same delta whether
    premium is fat or derisory.
  - `max_relative_spread` -- reject a strike whose bid/ask is wider than this
    fraction of its mid. The existing executability gate only checks that LTP
    sits INSIDE the spread, so the real 2026-09-14 SILVERM market of
    38 / 2044.5 would still have passed it.
  - `use_bid_for_entry_premium` -- book the entry credit at the bid rather
    than the last-traded price. A seller hits the bid; `picked.ltp`
    systematically overstates every entry credit, and therefore every P&L
    number on the dashboard.
  - `fallback_sigma` -- per-commodity replacement for the hard-coded
    `mcx_options_engine.DEFAULT_SIGMA` of 0.20, used when a strike's own
    quoted IV is missing or implausible. Silver's realised vol is materially
    higher than gold's; one constant for both is a known compromise.

Additive and nullable throughout -- no destructive change, and `downgrade()`
simply drops the columns.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_mcx_options_risk_flags"
down_revision = "0026_mcx_options_guards"
branch_labels = None
depends_on = None

_NULLABLE_NUMERIC = (
    "stop_loss_premium_multiple",
    "min_credit_pct_of_strike",
    "max_relative_spread",
    "fallback_sigma",
)
_NULLABLE_INTEGER = ("min_dte_days", "max_dte_days")


def upgrade() -> None:
    for name in _NULLABLE_NUMERIC:
        op.add_column("mcx_options_configs", sa.Column(name, sa.Numeric(), nullable=True))
    for name in _NULLABLE_INTEGER:
        op.add_column("mcx_options_configs", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column(
        "mcx_options_configs",
        sa.Column(
            "use_bid_for_entry_premium",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


    # A stop-out is recorded as a leg row the same way a roll is
    # (opt_type="STOP"/action="stop_loss", no strike or premium -- it is a
    # futures-only event, not an option). 0026's CHECKs only knew about ROLL,
    # so they have to be widened here, in the migration that makes STOP legs
    # possible in the first place.
    op.drop_constraint("ck_mcx_options_legs_opt_type", "mcx_options_legs", type_="check")
    op.create_check_constraint(
        "ck_mcx_options_legs_opt_type",
        "mcx_options_legs",
        "opt_type IN ('PE', 'CE', 'ROLL', 'STOP')",
    )
    op.drop_constraint("ck_mcx_options_legs_action", "mcx_options_legs", type_="check")
    op.create_check_constraint(
        "ck_mcx_options_legs_action",
        "mcx_options_legs",
        "action IN ('sell_put', 'sell_call', 'assigned', 'called_away', "
        "'put_expired_otm', 'call_expired_otm', 'roll', 'stop_loss')",
    )
    op.drop_constraint(
        "ck_mcx_options_legs_roll_has_no_strike", "mcx_options_legs", type_="check"
    )
    op.create_check_constraint(
        "ck_mcx_options_legs_roll_has_no_strike",
        "mcx_options_legs",
        "(opt_type IN ('ROLL', 'STOP')) = (strike IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mcx_options_legs_roll_has_no_strike", "mcx_options_legs", type_="check"
    )
    op.create_check_constraint(
        "ck_mcx_options_legs_roll_has_no_strike",
        "mcx_options_legs",
        "(opt_type = 'ROLL') = (strike IS NULL)",
    )
    op.drop_constraint("ck_mcx_options_legs_action", "mcx_options_legs", type_="check")
    op.create_check_constraint(
        "ck_mcx_options_legs_action",
        "mcx_options_legs",
        "action IN ('sell_put', 'sell_call', 'assigned', 'called_away', "
        "'put_expired_otm', 'call_expired_otm', 'roll')",
    )
    op.drop_constraint("ck_mcx_options_legs_opt_type", "mcx_options_legs", type_="check")
    op.create_check_constraint(
        "ck_mcx_options_legs_opt_type",
        "mcx_options_legs",
        "opt_type IN ('PE', 'CE', 'ROLL')",
    )

    op.drop_column("mcx_options_configs", "use_bid_for_entry_premium")
    for name in _NULLABLE_INTEGER + _NULLABLE_NUMERIC:
        op.drop_column("mcx_options_configs", name)
