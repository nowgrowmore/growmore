"""One-shot: pull 5y of daily bars for every configured instrument into the cache.

    python -m research.dailydata.fetch [--from-date ...] [--to-date ...] [--force]

Reads the instrument rows (symbol + security_id + lot_size + tick_size) from
whatever DATABASE_URL points at, fetches through DhanClient -- so the bars are
already duplicate-repaired and corrupt-bar-dropped by _validated_bars -- and
writes parquet. Read-only against the database; writes nothing back.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from growmore_bot.broker.dhan_client import DhanClient
from growmore_bot.config import Settings
from growmore_bot.persistence.db import session_scope
from growmore_bot.persistence.models import Instrument
from research.dailydata import cache

#: Instrument metadata the backtests need but the bar frame doesn't carry.
META_PATH = cache.CACHE_DIR / "instruments.json"
REQUEST_DELAY_SECONDS = 3.0
MAX_RETRIES = 4


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Dhan's SDK json-encodes the payload, so these stay STRINGS all the
    # way through -- passing a date object raises deep inside dhan_http.
    parser.add_argument("--from-date", default=str(date.today() - timedelta(days=5 * 365)))
    parser.add_argument("--to-date", default=str(date.today()))
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch symbols that are already cached.")
    parser.add_argument("--symbols", nargs="*", default=None)
    args = parser.parse_args(argv)

    settings = Settings()
    client = DhanClient(
        client_id=settings.dhan_client_id, access_token=settings.dhan_access_token
    )
    client.refresh_access_token_if_needed()

    meta: dict = {}
    if META_PATH.exists():
        meta = json.loads(META_PATH.read_text())

    with session_scope() as session:
        instruments = session.query(Instrument).all()
        for inst in instruments:
            if args.symbols and inst.symbol not in args.symbols:
                continue
            meta[inst.symbol] = {
                "lot_size": inst.lot_size,
                "tick_size": float(inst.tick_size or 0.0),
                "security_id": inst.security_id,
            }
            if cache.is_cached(inst.symbol) and not args.force:
                print(f"  {inst.symbol}: cached", file=sys.stderr)
                continue
            # Dhan rate-limits a burst of eight historical calls (DH-904),
            # so throttle and back off rather than losing the whole run to
            # one 429 seven instruments in.
            bars = None
            for attempt in range(MAX_RETRIES):
                try:
                    bars = client.get_historical_ohlc(
                        inst, from_date=args.from_date, to_date=args.to_date, interval="day"
                    )
                    break
                except Exception as exc:  # noqa: BLE001 -- retry anything transient
                    wait = REQUEST_DELAY_SECONDS * (2 ** attempt) * 5
                    print(f"  {inst.symbol}: {exc} -- retrying in {wait:.0f}s",
                          file=sys.stderr)
                    time.sleep(wait)
            time.sleep(REQUEST_DELAY_SECONDS)
            if not bars:
                print(f"  {inst.symbol}: NO BARS", file=sys.stderr)
                continue
            path = cache.save(inst.symbol, cache.bars_to_frame(bars))
            print(f"  {inst.symbol}: {len(bars)} bars -> {path.name}", file=sys.stderr)

    cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, indent=2, sort_keys=True))
    print(f"instrument metadata -> {META_PATH}", file=sys.stderr)
    return 0


def load_meta() -> dict:
    if not META_PATH.exists():
        raise FileNotFoundError(
            f"{META_PATH} missing -- run `python -m research.dailydata.fetch` first."
        )
    return json.loads(META_PATH.read_text())


if __name__ == "__main__":
    raise SystemExit(main())
