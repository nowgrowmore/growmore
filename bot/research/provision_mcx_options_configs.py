"""Create/refresh the `mcx_options_configs` rows for GOLDM and SILVERM,
seeded with the values validated in the offline backtest.

    python -m research.provision_mcx_options_configs             # show the plan
    python -m research.provision_mcx_options_configs --apply

Seed values come from `research/mcx_options/run_strategies.py`'s
`default_variants()` -- specifically its "dynamic-0.30-0.50-delta" variant
(`consolidating_target_delta=0.30`, `trend_favorable_target_delta=0.50`),
the one of the two default variants that outperformed a flat 0.30 delta
across regimes in the backtest report.

Idempotent, mirroring `research/provision_configs.py`'s own upsert
discipline:

1. **New config rows** are created for any (strategy, symbol) pair that
   doesn't already have one, ALWAYS with `enabled=False` and `mode="paper"`.
   A live options order-placement path does not exist in this codebase (see
   `growmore_bot.persistence.models.MCXOptionsConfig`'s docstring) -- `mode`
   stays "paper" unconditionally, there is no "live" variant to create here,
   unlike `provision_configs.py`'s paper+live pair for `BotConfig`.
2. **Existing config rows** have only their tunable numeric params
   (target deltas, lots) refreshed to the current seed values on a re-run.
   `enabled` is NEVER written by this script, in either direction -- flipping
   it to `True` is a deliberate, separate manual decision by the account
   owner (see docs/pending-actions.md), exactly like `bot_config`'s
   live-mode flip.
3. The backing `strategies` row (name="mcx_options_wheel") is created if
   missing -- unlike `vol_filtered`/`buy_and_hold` in `provision_configs.py`,
   there is no backtest sweep pipeline that produces this row first, so this
   script is its one source (mirrors how `wheel_basket_iv`'s Strategy row is
   also just created directly, not produced by a sweep).

This script must NEVER set `enabled=True`. Running it (with or without
--apply) never places an order and never connects to anything but the
configured `DATABASE_URL` via the ordinary `session_scope()` -- see
CLAUDE.md non-negotiables.
"""
from __future__ import annotations

import argparse
import sys
import uuid

from growmore_bot.persistence.db import session_scope
from growmore_bot.persistence.models import MCXOptionsConfig
from growmore_bot.persistence.models import Strategy as StrategyRow

STRATEGY_NAME = "mcx_options_wheel"
STRATEGY_VERSION = "1.0"

#: Validated in the offline backtest -- see module docstring.
CONSOLIDATING_TARGET_DELTA = 0.30
TREND_FAVORABLE_TARGET_DELTA = 0.50
LOTS = 1

#: Minimum open interest a strike must carry to be considered at all.
#:
#: Found by independent code review 2026-09-15: this script never wrote
#: `min_open_interest`, so every production row sat at the column's server
#: default of **0** -- i.e. the OI floor the engine passes to
#: `strike_selection.evaluate_candidates` was a complete no-op, and the
#: bid/ask executability gate was the only filter doing any work at all.
#:
#: 10 is a deliberately CONSERVATIVE "this strike has a real, ongoing market"
#: floor, in the same spirit as `growmore_bot/options/strike_selection.py`'s
#: `MIN_STRIKE_VOLUME = 1` ("a strike must have actually printed to be
#: sellable") -- it is NOT a sourced or tuned figure, and it is not a view on
#: what liquidity these contracts "should" have. It is set low enough that it
#: cannot plausibly starve the strategy of candidates while still excluding
#: the oi=0/oi=1 rows the 2026-09-14 phantom-quote incident came from.
#:
#: TUNE THIS against real data: `mcx_options_selections.candidates_considered`
#: now records the OI of every evaluated strike on every cycle, and the
#: /mcx-options selection log surfaces it. See docs/pending-actions.md.
MIN_OPEN_INTEREST = 10

WANTED_SYMBOLS = ["GOLDM", "SILVERM"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Without this, prints the plan and changes nothing."
    )
    args = parser.parse_args(argv)
    apply = args.apply

    with session_scope() as session:
        changes: list[str] = []

        strategy = (
            session.query(StrategyRow)
            .filter_by(name=STRATEGY_NAME, version=STRATEGY_VERSION)
            .one_or_none()
        )
        if strategy is None:
            changes.append(f"CREATE   strategy row {STRATEGY_NAME}/{STRATEGY_VERSION}")
            strategy = StrategyRow(
                id=uuid.uuid4(), name=STRATEGY_NAME, version=STRATEGY_VERSION, params={},
            )
            if apply:
                session.add(strategy)
                session.flush()

        for symbol in WANTED_SYMBOLS:
            existing = (
                session.query(MCXOptionsConfig)
                .filter_by(strategy_id=strategy.id, symbol=symbol)
                .one_or_none()
            )
            if existing is None:
                changes.append(
                    f"CREATE   {symbol:9} consolidating={CONSOLIDATING_TARGET_DELTA} "
                    f"trend_favorable={TREND_FAVORABLE_TARGET_DELTA} lots={LOTS} "
                    f"min_open_interest={MIN_OPEN_INTEREST} enabled=False mode=paper"
                )
                if apply:
                    session.add(
                        MCXOptionsConfig(
                            id=uuid.uuid4(),
                            strategy_id=strategy.id,
                            # Deliberate manual step, never flipped by this script.
                            enabled=False,
                            mode="paper",
                            symbol=symbol,
                            lots=LOTS,
                            consolidating_target_delta=CONSOLIDATING_TARGET_DELTA,
                            trend_favorable_target_delta=TREND_FAVORABLE_TARGET_DELTA,
                            min_open_interest=MIN_OPEN_INTEREST,
                        )
                    )
                continue

            # Existing row: refresh tunables only. `enabled` is never read or
            # written here in either direction -- an owner who has already
            # flipped it to True must see it stay True across every re-run.
            tunables_changed = (
                float(existing.consolidating_target_delta) != CONSOLIDATING_TARGET_DELTA
                or float(existing.trend_favorable_target_delta) != TREND_FAVORABLE_TARGET_DELTA
                or int(existing.lots) != LOTS
                or int(existing.min_open_interest) != MIN_OPEN_INTEREST
            )
            changes.append(
                f"{'UPDATE ' if tunables_changed else 'exists '} {symbol:9} "
                f"enabled={existing.enabled} (untouched by this script)"
            )
            if apply and tunables_changed:
                existing.consolidating_target_delta = CONSOLIDATING_TARGET_DELTA
                existing.trend_favorable_target_delta = TREND_FAVORABLE_TARGET_DELTA
                existing.lots = LOTS
                existing.min_open_interest = MIN_OPEN_INTEREST

        print()
        for c in changes:
            print(" ", c)
        print(f"\n{len(changes)} item(s). {'APPLIED.' if apply else 'DRY RUN -- nothing written.'}")
        if not apply:
            session.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
