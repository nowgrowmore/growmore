"""Add bot_config.daily_loss_limit_enabled and close_reason on sell orders

Two independent additions, bundled in one migration since both came from
the same account-owner request:

1. `bot_config.daily_loss_limit_enabled` (default true, so every existing
   config keeps today's always-on behavior) -- lets a config opt out of the
   P&L-based daily_loss_limit circuit breaker entirely and rely purely on
   the strategy's own BUY/SELL signals. The end-of-day-flatten and
   contract-expiry force-closes are unaffected either way -- those are
   position-lifecycle safety nets, not P&L risk management.
2. `paper_orders.close_reason` / `live_orders.close_reason` (nullable, no
   default -- existing historical rows stay NULL, meaning "before this
   feature existed") -- records why a sell fired: "strategy_signal",
   "expiry", "end_of_day", or "daily_loss_limit".

Revision ID: 0014_close_reason_toggle
Revises: 0013_portfolio_backtest
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_close_reason_toggle"
down_revision = "0013_portfolio_backtest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_config",
        sa.Column("daily_loss_limit_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column("paper_orders", sa.Column("close_reason", sa.Text(), nullable=True))
    op.add_column("live_orders", sa.Column("close_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("live_orders", "close_reason")
    op.drop_column("paper_orders", "close_reason")
    op.drop_column("bot_config", "daily_loss_limit_enabled")
