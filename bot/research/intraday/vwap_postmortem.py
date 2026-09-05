"""CLI: does VwapSessionBounceStrategy actually work?

It has been enabled in paper trading with no backtest at all, justified by
"today's live session VWAP doesn't exist in historical bars". Dhan's intraday
endpoint makes that false, so this settles it.

Reports trade FREQUENCY before P&L, deliberately. At roughly 5-9 bps a round
trip an intraday strategy firing several times a day is dead on cost alone,
and no amount of P&L analysis changes that.

Usage (from bot/):
    python -m research.intraday.vwap_postmortem --symbols GOLDM SILVERM
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Sequence

from growmore_bot.broker.dhan_client import DhanClient
from growmore_bot.config import DEFAULT_COMMODITY_UNIVERSE, Settings
from growmore_bot.costs import DEFAULT_COST_MODEL
from growmore_bot.strategies.vwap_session_bounce import VwapSessionBounceStrategy
from research.intraday.bar_cache import fetch_symbol_year, load_range
from research.intraday.replay import replay

INTERVAL = 5


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["GOLDM"])
    parser.add_argument("--from-date", default="2025-09-05")
    parser.add_argument("--to-date", default="2026-09-05")
    args = parser.parse_args(argv)

    from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    to_date = datetime.strptime(args.to_date, "%Y-%m-%d").date()

    settings = Settings()
    client = DhanClient(
        client_id=settings.dhan_client_id, access_token=settings.dhan_access_token
    )
    client.refresh_access_token_if_needed()

    universe = {c.symbol: c for c in DEFAULT_COMMODITY_UNIVERSE}
    print(f"{'symbol':<11}{'sessions':>9}{'trades':>8}{'sig/day':>9}"
          f"{'net P&L':>14}{'gross':>14}{'costs':>12}{'win%':>7}")
    print("-" * 84)

    exit_code = 1
    for symbol in args.symbols:
        instrument = universe.get(symbol)
        if instrument is None:
            print(f"{symbol}: not in the configured universe", file=sys.stderr)
            continue
        for year in range(from_date.year, to_date.year + 1):
            fetch_symbol_year(client, instrument, year, from_date, to_date, INTERVAL)
        frame = load_range(symbol, INTERVAL, from_date, to_date)
        if frame.empty:
            print(f"{symbol}: no intraday bars cached", file=sys.stderr)
            continue

        priced = replay(
            VwapSessionBounceStrategy, frame,
            lot_size=instrument.lot_size, tick_size=instrument.tick_size or 0.0,
            cost_model=DEFAULT_COST_MODEL,
        )
        free = replay(
            VwapSessionBounceStrategy, frame,
            lot_size=instrument.lot_size, tick_size=instrument.tick_size or 0.0,
        )
        wins = sum(1 for t in priced.trades if t.pnl > 0)
        win_pct = wins / len(priced.trades) * 100 if priced.trades else 0.0
        print(
            f"{symbol:<11}{priced.sessions_replayed:>9}{len(priced.trades):>8}"
            f"{priced.signals_per_session:>9.2f}{priced.total_pnl:>14,.0f}"
            f"{free.total_pnl:>14,.0f}{free.total_pnl - priced.total_pnl:>12,.0f}{win_pct:>7.1f}"
        )
        exit_code = 0
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
