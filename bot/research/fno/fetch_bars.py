"""One-shot: pull 15y of daily OHLCV for the F&O universe into the local store.

    python -m research.fno.fetch_bars [--from-date ...] [--to-date ...]
                                      [--force] [--symbols ...]

Reads `research/fno/universe.csv` (committed -- build it with
`python -m research.fno.manifest --write`), fetches through the existing
`DhanClient` so `_validated_bars`' corrupt-bar drop already applies, and
writes one parquet per symbol. **Read-only against Dhan and the database;
writes nothing back to either.**

Resumable: a symbol whose parquet already exists is skipped, so an expired
token or a crash 150 symbols in does not cost the first 150. Note the
existing-file check does NOT re-validate the cached window against
`--from-date`/`--to-date` (same behaviour as the small-cap fetcher) -- delete
a file, or pass `--force`, to re-fetch it.

WHY 15 YEARS AND NOT 5. Dhan's NSE_EQ history is confirmed to reach 2010
(4,134 daily bars for TTML), and depth costs nothing: it is the same single
API call per symbol. The documented flaw of BOTH prior studies here is that
2021-2026 is a single bull regime, which makes buy-and-hold nearly
unbeatable and measures beta rather than edge
(docs/walk-forward-results.md, docs/smallcap-momentum-backtest-results.md).
Fifteen years spans the 2011 bear, the 2013 taper tantrum, the 2018-19
midcap crash and COVID, and takes walk-forward from ~6 folds per stock to
~22.

RATE LIMITS. Dhan's historical endpoint is ~1 req/sec in practice and
429s (DH-904) on a burst of eight, so this throttles at 1.2s and backs off
exponentially -- the settings the small-cap fetcher arrived at over a real
400-symbol run.

CORPORATE ACTIONS. Dhan's NSE_EQ daily series is adjusted -- verified
2026-09-05: only 2 of 400 cached stocks show a >35% single-day move, both
real news events (ZEEL 2024-01-22, IEX 2025-07-23), and IRCTC's Oct-2021 1:5
split is already backed out of its Sept-2021 prices. Since an unadjusted
split would look exactly like a stop-triggering gap to an ATR system, every
symbol is still screened for extreme single-day moves and the survivors are
reported for manual review rather than trusted silently.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Optional, Sequence

from growmore_bot.broker.dhan_client import DhanClient
from growmore_bot.config import Settings
from research.fno import bar_cache
from research.fno.manifest import load_manifest

META_PATH = bar_cache.CACHE_DIR / "meta.json"
REQUEST_DELAY_SECONDS = 1.2
MAX_RETRIES = 4

#: A daily |log return| above this is either a real crash or an unadjusted
#: corporate action. Reported, never auto-corrected.
EXTREME_MOVE_LOG_RETURN = 0.35

#: Below this many bars a symbol is "unmeasured" rather than a weak result --
#: the treatment docs/walk-forward-results.md gave the short-history MCX
#: contracts. ~5 years of sessions.
MIN_BARS_FOR_INCLUSION = 1260


def extreme_moves(bars: Sequence) -> list[tuple[str, float]]:
    """Dates where |log return| exceeded EXTREME_MOVE_LOG_RETURN."""
    flagged: list[tuple[str, float]] = []
    for previous, current in zip(bars, bars[1:]):
        if previous.close <= 0 or current.close <= 0:
            continue
        move = math.log(current.close / previous.close)
        if abs(move) > EXTREME_MOVE_LOG_RETURN:
            flagged.append((bar_cache.trading_date(current.timestamp).isoformat(), move))
    return flagged


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Dhan's SDK json-encodes the payload, so these stay STRINGS all the way
    # through -- passing a date object raises deep inside dhan_http.
    parser.add_argument("--from-date", default=str(date.today() - timedelta(days=15 * 365 + 4)))
    parser.add_argument("--to-date", default=str(date.today()))
    parser.add_argument("--force", action="store_true", help="Re-fetch already-cached symbols.")
    parser.add_argument("--symbols", nargs="*", default=None)
    args = parser.parse_args(argv)

    rows = load_manifest()
    if args.symbols:
        wanted = set(args.symbols)
        rows = [r for r in rows if r.symbol in wanted]

    settings = Settings()
    client = DhanClient(
        client_id=settings.dhan_client_id, access_token=settings.dhan_access_token
    )
    client.refresh_access_token_if_needed()

    meta: dict = {}
    if META_PATH.exists():
        meta = json.loads(META_PATH.read_text())

    flagged: dict[str, list] = {}
    failed: list[str] = []
    thin: list[str] = []

    for index, row in enumerate(rows, start=1):
        prefix = f"[{index:3d}/{len(rows)}] {row.symbol:<14}"
        if bar_cache.is_cached(row.symbol) and not args.force:
            print(f"{prefix} cached", file=sys.stderr)
            continue

        instrument = SimpleNamespace(
            security_id=row.security_id,
            exchange_segment="NSE_EQ",
            instrument_type="EQUITY",
            symbol=row.symbol,
        )
        bars = None
        for attempt in range(MAX_RETRIES):
            try:
                bars = client.get_historical_ohlc(
                    instrument, from_date=args.from_date, to_date=args.to_date, interval="day"
                )
                break
            except Exception as exc:  # noqa: BLE001 -- retry anything transient
                wait = REQUEST_DELAY_SECONDS * (2 ** attempt) * 5
                print(f"{prefix} {str(exc)[:70]} -- retry in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
        time.sleep(REQUEST_DELAY_SECONDS)

        if not bars:
            print(f"{prefix} NO BARS", file=sys.stderr)
            failed.append(row.symbol)
            continue

        bar_cache.save(row.symbol, bar_cache.bars_to_frame(bars))
        moves = extreme_moves(bars)
        if moves:
            flagged[row.symbol] = moves
        first = bar_cache.trading_date(bars[0].timestamp)
        last = bar_cache.trading_date(bars[-1].timestamp)
        if len(bars) < MIN_BARS_FOR_INCLUSION:
            thin.append(row.symbol)
        meta[row.symbol] = {
            "security_id": row.security_id,
            "n_bars": len(bars),
            "first_bar": first.isoformat(),
            "last_bar": last.isoformat(),
            "nse_industry": row.nse_industry,
            "is_defence": row.is_defence,
            "fno_lot_size": row.fno_lot_size,
        }
        note = "  THIN" if len(bars) < MIN_BARS_FOR_INCLUSION else ""
        print(f"{prefix} {len(bars):5d} bars  {first} -> {last}{note}", file=sys.stderr)

    bar_cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, indent=2, sort_keys=True))

    print(f"\ncached symbols: {len(bar_cache.cached_symbols())}", file=sys.stderr)
    print(f"failed: {len(failed)} {failed if failed else ''}", file=sys.stderr)
    print(
        f"under {MIN_BARS_FOR_INCLUSION} bars (unmeasured): {len(thin)} {thin if thin else ''}",
        file=sys.stderr,
    )
    if flagged:
        print(
            f"\n{len(flagged)} symbols with a >|{EXTREME_MOVE_LOG_RETURN}| single-day log move "
            "-- review for an unadjusted corporate action:",
            file=sys.stderr,
        )
        for symbol, moves in sorted(flagged.items()):
            shown = ", ".join(f"{d} {m:+.2f}" for d, m in moves[:4])
            print(f"  {symbol:<14} {len(moves):2d}x  {shown}", file=sys.stderr)
    print(f"\nmetadata -> {META_PATH}", file=sys.stderr)
    return 0


def load_meta() -> dict:
    if not META_PATH.exists():
        raise FileNotFoundError(
            f"{META_PATH} missing -- run `python -m research.fno.fetch_bars` first."
        )
    return json.loads(META_PATH.read_text())


if __name__ == "__main__":
    raise SystemExit(main())
