"""Add backtest_runs.dsr

Deflated Sharpe Ratio, per run -- the probability a run's Sharpe reflects
real edge rather than being the luckiest of everything tried in its sweep.
Previously computed only by research/validation/deflate_sweep.py's read-only
CLI report, printed to stdout and hand-pasted into docs/backtest-results.md
-- never persisted, so the dashboard had no data source for a DSR column.
Nullable: it's a sweep-relative statistic, populated by a batch job
(deflate_sweep.py --persist), not computed per-run at insert time.

Revision ID: 0018_backtest_dsr
Revises: 0017_risk_state
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_backtest_dsr"
down_revision = "0017_risk_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backtest_runs", sa.Column("dsr", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_runs", "dsr")
