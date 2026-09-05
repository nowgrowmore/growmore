"""Drop bot_config.virtual_capital

The account owner's decision (2026-09-05). The column never drove anything:
nothing in the bot's trading path reads it -- position size comes from
`max_position_size` (in lots) and the backtests capitalise themselves from one
lot's own notional via `capital_for_run`. Only the dashboard displayed it.

Displaying it was actively harmful. Every config carried Rs 2,50,000 against a
Gold Mini lot worth ~Rs 15.2 lakh, so the figure implied 6x leverage that the
bot was not actually taking, and made every capital number on the dashboard
incomparable to the backtest figures beside it. A field that is read by
nothing and misleads when shown is worth removing rather than correcting --
correcting it would only have made a meaningless number look authoritative.

`max_position_size` stays: it is the real position control, and lots are the
unit the exchange actually deals in.

Revision ID: 0020_drop_virtual_capital
Revises: 0019_retire_daily_loss_limit
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_drop_virtual_capital"
down_revision = "0019_retire_daily_loss_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("bot_config", "virtual_capital")


def downgrade() -> None:
    # Re-added WITH a server_default, because the original column was NOT NULL
    # with none -- without a default this downgrade would fail on any table
    # that already has rows. The value is arbitrary; the column meant nothing.
    op.add_column(
        "bot_config",
        sa.Column("virtual_capital", sa.Numeric(), nullable=False,
                  server_default=sa.text("0")),
    )
