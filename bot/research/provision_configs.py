"""Create/refresh the bot_config rows for the strategies that survived validation.

    python -m research.provision_configs --dry-run     # show the plan
    python -m research.provision_configs --apply

Idempotent: re-running changes nothing it has already done. Three jobs.

1. **New configs.** A paper (enabled) and a live (DISABLED) row for each
   surviving strategy/instrument pair, matching the pattern already in the
   table. Live rows are created switched off and stay that way -- a real order
   additionally requires Settings().live_trading_enabled, which is a separate
   global gate. Nothing here can place an order.

     vol_filtered ensemble_trend-agree3-stop2-trail3-vol90  GOLDM, SILVERM
       The only one of five candidate improvements that survived
       out-of-sample testing on BOTH bullion contracts (+0.13 / +0.20 Sharpe).

     buy_and_hold 1lot                                      GOLDM, SILVERM
       The benchmark, run for real. Out of sample it beats the trading system
       on five of eight contracts, so it needs to be measured by the same
       engine, cost model and rollover machinery rather than asserted from a
       research script.

2. **Retire the daily-loss guard** on every config (owner's decision; see
   migration 0019 for why the mechanism was a footgun). The migration does the
   data change -- this verifies it.

3. **Report the real leverage.** `bot_config` no longer carries a capital
   figure at all (migration 0020 dropped `virtual_capital`: nothing read it,
   and Rs 2.5 lakh shown against a Rs 15.2 lakh lot implied leverage the bot
   was not taking). Position size is `max_position_size`, in lots. So this
   prints what one lot of each instrument is actually worth, which is the
   number that matters when deciding how many configs can run at once.
"""
from __future__ import annotations

import argparse
import sys
import uuid

from growmore_bot.persistence.db import session_scope
from growmore_bot.persistence.models import BotConfig, Instrument
from growmore_bot.persistence.models import Strategy as StrategyRow

#: (strategy name, version tag, instrument symbols)
WANTED = [
    ("vol_filtered", "ensemble_trend-agree3-stop2-trail3-vol90", ["GOLDM", "SILVERM"]),
    ("buy_and_hold", "1lot", ["GOLDM", "SILVERM"]),
]

#: `daily_loss_limit` is a retired, unread column (see migration 0019) that is
#: still NOT NULL, so a value has to go in it. 2% of one lot's notional is what
#: it would have meant if it were ever used again.
DAILY_LOSS_DISPLAY_PCT = 0.02


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Without this, prints the plan and changes nothing.")
    args = parser.parse_args(argv)
    apply = args.apply

    with session_scope() as session:
        instruments = {i.symbol: i for i in session.query(Instrument).all()}
        changes: list[str] = []

        # --- notional per lot, from the cached daily series -------------
        # Deliberately NOT a live Dhan quote: the only thing this figure now
        # feeds is `daily_loss_limit`, a retired display column, and calling
        # the quote API here collides with a running sweep's rate limit. The
        # last cached close is more than accurate enough for a number nothing
        # acts on -- and it keeps this script runnable offline.
        from research.dailydata import cache as daily_cache
        notional: dict[str, float] = {}
        for sym, inst in instruments.items():
            try:
                last = daily_cache.load(sym)[-1]
            except (FileNotFoundError, IndexError):
                print(f"  {sym:10} no cached series -- skipping", file=sys.stderr)
                continue
            notional[sym] = round(float(last.close) * inst.lot_size, -3)
            print(f"  {sym:10} close {float(last.close):>12,.2f} x lot {inst.lot_size:>5} "
                  f"= Rs {notional[sym]:>13,.0f} per lot", file=sys.stderr)

        # --- 1. new configs -------------------------------------------------
        for name, version, symbols in WANTED:
            row = (session.query(StrategyRow)
                   .filter_by(name=name, version=version).one_or_none())
            if row is None:
                changes.append(
                    f"MISSING strategy row {name}/{version} -- run the sweep first "
                    f"(python -m growmore_bot.backtest.run_all ...) so the backtest "
                    f"and the config point at the SAME strategy id")
                continue
            for sym in symbols:
                inst = instruments.get(sym)
                if inst is None:
                    changes.append(f"MISSING instrument {sym}")
                    continue
                for mode in ("paper", "live"):
                    existing = (session.query(BotConfig)
                                .filter_by(strategy_id=row.id, instrument_id=inst.id,
                                           mode=mode).one_or_none())
                    if existing is not None:
                        changes.append(f"exists   {mode:5} {sym:9} {name}/{version}")
                        continue
                    cfg = BotConfig(
                        id=uuid.uuid4(),
                        strategy_id=row.id,
                        instrument_id=inst.id,
                        # Paper on, live off. A live config is created switched
                        # off and enabling one is a deliberate, separate act.
                        enabled=(mode == "paper"),
                        mode=mode,
                        max_position_size=1,
                        daily_loss_limit=round(notional[sym] * DAILY_LOSS_DISPLAY_PCT, -2),
                        daily_loss_limit_enabled=False,
                    )
                    if apply:
                        session.add(cfg)
                    changes.append(
                        f"CREATE   {mode:5} {sym:9} {name}/{version}  "
                        f"enabled={cfg.enabled}  max 1 lot "
                        f"(= Rs {notional[sym]:,.0f} notional)  loss-guard off")

        # --- 2 & 3. normalise every config ----------------------------------
        for cfg in session.query(BotConfig).all():
            inst = session.get(Instrument, cfg.instrument_id)
            st = session.get(StrategyRow, cfg.strategy_id)
            tag = f"{cfg.mode:5} {inst.symbol:9} {st.name}/{st.version}"
            if cfg.daily_loss_limit_enabled:
                changes.append(f"GUARD-OFF {tag}")
                if apply:
                    cfg.daily_loss_limit_enabled = False
            target = notional.get(inst.symbol)
            if target and abs(float(cfg.daily_loss_limit) - target * DAILY_LOSS_DISPLAY_PCT) > 1:
                if apply:
                    cfg.daily_loss_limit = round(target * DAILY_LOSS_DISPLAY_PCT, -2)

        print()
        for c in changes:
            print(" ", c)
        print(f"\n{len(changes)} item(s). {'APPLIED.' if apply else 'DRY RUN -- nothing written.'}")
        if not apply:
            session.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
