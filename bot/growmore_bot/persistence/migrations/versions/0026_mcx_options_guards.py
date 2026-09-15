"""mcx options indexes, uniqueness and state CHECK constraints

Revision ID: 0026_mcx_options_guards
Revises: 0025_mcx_options_expiry

Found by independent code review, 2026-09-15: the four `mcx_options_*`
tables shipped with **zero indexes and zero constraints** beyond their
primary and foreign keys. Every one of the engine's and dashboard's hot
predicates was a sequential scan plus a sort, and every invariant the
engine relies on was enforced only by the engine itself -- so a re-run, a
crash mid-cycle, or a hand-written UPDATE could put the tables into a state
the engine then read back as gospel.

What this adds, and the concrete failure each one closes:

1. **Indexes on the exact predicates that are actually queried.**
   `mcx_options_selections` grows one row per config per day forever and is
   re-queried on every dashboard render (`/mcx-options` is `force-dynamic`
   and auto-refreshes every 60s), ordered by `cycle_date desc, created_at
   desc` and then LIMITed -- a seq scan + full sort each time.

2. **`UNIQUE (config_id, cycle_date)` on `mcx_options_selections`.**
   One cycle_date is one decision. Three production cycles were run by hand
   on 2026-09-14 (19:13, 20:42, 23:59 IST) and each appended its own row for
   the same date, so the selection log showed that day three times with
   three different "why" strings and no way to tell which one the engine
   acted on. `mcx_options_engine._record_selection` now replaces the day's
   row in place; this constraint is what makes that guarantee real.

   **The upgrade de-duplicates before adding the constraint** -- keeping the
   newest row per (config_id, cycle_date), which is the one whose decision
   actually stands -- otherwise it would simply fail on production.

3. **At most one open position per config, and at most one unsettled leg per
   position**, as partial unique indexes. `run_cycle` used `.first()` for the
   former (silently ignoring a second open position, which would then be
   orphaned forever -- never settled, never marked to market) and
   `.one_or_none()` for the latter (raising a bare `MultipleResultsFound`
   that the scheduler's blanket `except Exception` turned into a permanent,
   silent daily skip for that commodity). The engine now raises a named
   `MCXOptionsStateError` for both; these indexes stop the state arising.

4. **CHECK constraints on the free-text state columns.** `status`, `state`,
   `opt_type`, `action`, `mode` and `regime` are all plain `Text` with a
   small closed set of legal values, and a typo in any code path persisted
   silently. `mcx_options_configs.mode` matters most of the three: it is the
   live-trading gate, and it was unconstrained text.

5. **`CHECK ((opt_type = 'ROLL') = (strike IS NULL))`** on `mcx_options_legs`
   -- migration 0023 made `strike`/`premium` nullable for exactly one reason
   (a ROLL leg carries neither), but nothing tied the nullability to that
   reason, so a PE/CE leg with a null strike was schema-legal. The engine
   guarded it with `assert`s, which `python -O` strips.

6. **`UNIQUE (strategy_id, symbol)` on `mcx_options_configs`** -- which
   `research/provision_mcx_options_configs.py`'s `.one_or_none()` lookup has
   always assumed.

Every constraint here describes a state the engine already treats as
impossible, so a correctly-behaving database has nothing to change beyond
the de-duplication in (2). `downgrade()` drops all of it and is a clean
round-trip.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_mcx_options_guards"
down_revision = "0025_mcx_options_expiry"
branch_labels = None
depends_on = None


#: Mirrors MCXOptionsLeg.action's documented literal set (models.py) plus the
#: two settlement outcomes the engine writes.
_LEG_ACTIONS = (
    "sell_put",
    "sell_call",
    "assigned",
    "called_away",
    "put_expired_otm",
    "call_expired_otm",
    "roll",
)


def upgrade() -> None:
    # --- 2. de-duplicate before the unique constraint can be added ---------
    # Keep the NEWEST row per (config_id, cycle_date): a re-run of a cycle is
    # a correction of that day's decision, so the last one written is the one
    # that stands.
    op.execute(
        """
        DELETE FROM mcx_options_selections a
        USING mcx_options_selections b
        WHERE a.config_id = b.config_id
          AND a.cycle_date = b.cycle_date
          AND (a.created_at, a.id) < (b.created_at, b.id)
        """
    )

    # --- 1. indexes on the predicates actually queried ---------------------
    op.create_index(
        "ix_mcx_options_selections_config_cycle",
        "mcx_options_selections",
        ["config_id", "cycle_date", "created_at"],
    )
    op.create_index(
        "ix_mcx_options_positions_config_opened",
        "mcx_options_positions",
        ["config_id", "opened_at"],
    )
    op.create_index("ix_mcx_options_legs_position", "mcx_options_legs", ["position_id"])

    # --- 2/3/6. uniqueness -------------------------------------------------
    op.create_unique_constraint(
        "uq_mcx_options_selections_config_cycle",
        "mcx_options_selections",
        ["config_id", "cycle_date"],
    )
    op.create_unique_constraint(
        "uq_mcx_options_configs_strategy_symbol",
        "mcx_options_configs",
        ["strategy_id", "symbol"],
    )
    op.create_index(
        "uq_mcx_options_positions_one_open_per_config",
        "mcx_options_positions",
        ["config_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "uq_mcx_options_legs_one_unsettled_per_position",
        "mcx_options_legs",
        ["position_id"],
        unique=True,
        postgresql_where=sa.text("settled_at IS NULL"),
    )

    # --- 4/5. CHECK constraints on the closed-set text columns -------------
    op.create_check_constraint(
        "ck_mcx_options_configs_mode", "mcx_options_configs", "mode IN ('paper', 'live')"
    )
    op.create_check_constraint(
        "ck_mcx_options_positions_status",
        "mcx_options_positions",
        "status IN ('open', 'closed')",
    )
    op.create_check_constraint(
        "ck_mcx_options_positions_state",
        "mcx_options_positions",
        "state IN ('flat', 'long_futures', 'closed')",
    )
    op.create_check_constraint(
        "ck_mcx_options_legs_opt_type", "mcx_options_legs", "opt_type IN ('PE', 'CE', 'ROLL')"
    )
    op.create_check_constraint(
        "ck_mcx_options_legs_action",
        "mcx_options_legs",
        "action IN (" + ", ".join(f"'{a}'" for a in _LEG_ACTIONS) + ")",
    )
    op.create_check_constraint(
        "ck_mcx_options_legs_roll_has_no_strike",
        "mcx_options_legs",
        "(opt_type = 'ROLL') = (strike IS NULL)",
    )
    op.create_check_constraint(
        "ck_mcx_options_selections_regime",
        "mcx_options_selections",
        "regime IS NULL OR regime IN "
        "('consolidating', 'trend_favorable', 'trend_unfavorable')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mcx_options_selections_regime", "mcx_options_selections", type_="check"
    )
    op.drop_constraint("ck_mcx_options_legs_roll_has_no_strike", "mcx_options_legs", type_="check")
    op.drop_constraint("ck_mcx_options_legs_action", "mcx_options_legs", type_="check")
    op.drop_constraint("ck_mcx_options_legs_opt_type", "mcx_options_legs", type_="check")
    op.drop_constraint("ck_mcx_options_positions_state", "mcx_options_positions", type_="check")
    op.drop_constraint("ck_mcx_options_positions_status", "mcx_options_positions", type_="check")
    op.drop_constraint("ck_mcx_options_configs_mode", "mcx_options_configs", type_="check")

    op.drop_index("uq_mcx_options_legs_one_unsettled_per_position", "mcx_options_legs")
    op.drop_index("uq_mcx_options_positions_one_open_per_config", "mcx_options_positions")
    op.drop_constraint(
        "uq_mcx_options_configs_strategy_symbol", "mcx_options_configs", type_="unique"
    )
    op.drop_constraint(
        "uq_mcx_options_selections_config_cycle", "mcx_options_selections", type_="unique"
    )

    op.drop_index("ix_mcx_options_legs_position", "mcx_options_legs")
    op.drop_index("ix_mcx_options_positions_config_opened", "mcx_options_positions")
    op.drop_index("ix_mcx_options_selections_config_cycle", "mcx_options_selections")
