"""Add transaction-cost and capital-basis columns

Three related gaps, all of which made stored results un-interpretable:

1. `backtest_runs` never recorded WHICH capital a run was measured against.
   Every run used one flat `default_virtual_capital` regardless of
   instrument, so a Copper run (~Rs 34 lakh a lot) was silently measured at
   ~6.9x leverage and a Crude Oil Mini run (~Rs 0.86 lakh) at ~0.17x -- the
   CAGR column ranked contract size as much as edge. Once capital becomes
   per-instrument, old and new rows must be distinguishable, hence
   `initial_capital`.

2. No cost model existed anywhere, so `pnl` was always gross. `pnl` now
   becomes NET (which keeps every existing consumer -- the daily-loss guard,
   the dashboard, the metrics -- correct with no change), and the gross
   figure plus its cost breakdown are recorded alongside it for audit.

3. `instruments.tick_size` -- needed because slippage is a tick effect, not
   a basis-point one. One Copper tick is Rs 125 a lot; assuming a flat bps
   slippage would rank the instruments backwards.

All columns are nullable so this is a data-free migration: existing rows
read as "no cost model applied / capital basis unknown", which is exactly
true of them.

Revision ID: 0016_costs_and_capital
Revises: 0015_signal_history
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_costs_and_capital"
down_revision = "0015_signal_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("instruments", sa.Column("tick_size", sa.Numeric(), nullable=True))

    op.add_column("backtest_runs", sa.Column("initial_capital", sa.Numeric(), nullable=True))
    op.add_column(
        "backtest_runs", sa.Column("total_transaction_cost", sa.Numeric(), nullable=True)
    )
    op.add_column("backtest_runs", sa.Column("gross_cagr_pct", sa.Numeric(), nullable=True))
    op.add_column(
        "backtest_runs",
        sa.Column("cost_model", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    for table in ("paper_orders", "live_orders"):
        op.add_column(table, sa.Column("gross_pnl", sa.Numeric(), nullable=True))
        op.add_column(table, sa.Column("transaction_cost", sa.Numeric(), nullable=True))
        op.add_column(table, sa.Column("slippage_cost", sa.Numeric(), nullable=True))


def downgrade() -> None:
    for table in ("paper_orders", "live_orders"):
        op.drop_column(table, "slippage_cost")
        op.drop_column(table, "transaction_cost")
        op.drop_column(table, "gross_pnl")

    op.drop_column("backtest_runs", "cost_model")
    op.drop_column("backtest_runs", "gross_cagr_pct")
    op.drop_column("backtest_runs", "total_transaction_cost")
    op.drop_column("backtest_runs", "initial_capital")

    op.drop_column("instruments", "tick_size")
