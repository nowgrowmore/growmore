"""Turn the daily-loss guard off everywhere, and stop defaulting it on

The account owner's decision (2026-09-05): never use it. Two reasons the
mechanism was a poor fit, both discovered while designing the buy-and-hold
config:

  * Tripping it sets `bot_config.enabled = False` -- PERMANENTLY, until
    somebody notices and re-enables by hand. It is not a pause.
  * It measures REALISED P&L against a flat rupee figure, and Rs 15,000 is
    about 1% of one Gold Mini lot's ~Rs 15.2 lakh notional. So a single roll
    or signal exit that happens to close 1% underwater switches the config
    off for good, on an ordinary day, with no relationship to whether the
    strategy is working.

A guard whose failure mode is "silently stop trading" is worse than no guard:
loss control now lives entirely in the strategies' own ATR stops, where it is
visible and backtested. Buy-and-hold deliberately has none, which is what
buy-and-hold means.

This flips the server_default so a config created by any other code path (the
dashboard, a script, a future migration) does NOT quietly get the guard back,
and sets every existing row to false. `daily_loss_limit` itself is kept as a
column -- dropping it would break the dashboard's reads and throw away a
figure that is still useful to display.

The end-of-day-flatten and contract-expiry force-closes are position-lifecycle
safety nets, not P&L risk management, and are unaffected -- they stay active.

Revision ID: 0019_retire_daily_loss_limit
Revises: 0018_backtest_dsr
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_retire_daily_loss_limit"
down_revision = "0018_backtest_dsr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "bot_config",
        "daily_loss_limit_enabled",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("false"),
    )
    op.execute("UPDATE bot_config SET daily_loss_limit_enabled = false")


def downgrade() -> None:
    op.alter_column(
        "bot_config",
        "daily_loss_limit_enabled",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("true"),
    )
    # Deliberately NOT re-enabling the guard on existing rows: that would
    # re-arm a control the owner switched off, which is not a downgrade's
    # job. Flip individual configs back by hand if you ever want it.
