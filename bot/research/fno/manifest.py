"""Build the committed F&O universe manifest.

    python -m research.fno.manifest --write

Writes `research/fno/universe.csv` -- symbol, security_id, company_name,
nse_industry, is_defence, fno_lot_size -- which is CHECKED IN, unlike the
small-cap work's universe, which re-scraped NSE on every run and therefore
could not reproduce its own constituent list. Everything downstream reads
this file; nothing downstream touches the network.

Membership needs all THREE clauses:

    1. an NSE FUTSTK row          -- it is an F&O name
    2. an NSE_EQ cash-equity row  -- we can fetch its price history
    3. a Nifty 500 entry          -- it is a real company, and we get a sector

Clause 3 is not redundant. The 18 "011NSETEST".."181NSETEST" exchange test
symbols satisfy clauses 1 and 2 (they have genuine equity security_ids), and
only their absence from the Nifty 500 removes them. 228 underlyings in,
210 out.

SURVIVORSHIP: this is today's F&O membership applied backwards over the whole
backtest window. Being in the F&O list *now* is itself a selection on having
become large and liquid, and no point-in-time F&O membership file was found
anywhere. It is not solved here. The mitigation is the one
docs/smallcap-momentum-backtest-results.md used: the buy-and-hold control
runs on the IDENTICAL universe, so the bias hits both arms equally and the
DIFFERENCE between them stays meaningful even though neither LEVEL does.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from growmore_bot.broker.instrument_master import fetch_instrument_master_csv
from research.fno import sectors as sectors_mod
from research.fno.universe import (
    fno_lot_sizes,
    nse_equity_security_ids,
    parse_fno_underlyings,
)

MANIFEST_PATH = Path(__file__).resolve().parent / "universe.csv"
COLUMNS = [
    "symbol",
    "security_id",
    "company_name",
    "nse_industry",
    "is_defence",
    "fno_lot_size",
]


@dataclass(frozen=True)
class UniverseRow:
    symbol: str
    security_id: str
    company_name: str
    nse_industry: str
    is_defence: bool
    fno_lot_size: int


def build_universe(
    scrip_master_csv: str, nifty_500_csv: str, defence_csv: str
) -> list[UniverseRow]:
    """Apply the three membership clauses. Sorted by symbol, deterministic."""
    underlyings = parse_fno_underlyings(scrip_master_csv)
    equity_ids = nse_equity_security_ids(scrip_master_csv)
    lots = fno_lot_sizes(scrip_master_csv)
    labels = sectors_mod.build_sector_map(nifty_500_csv, defence_csv, underlyings)

    rows: list[UniverseRow] = []
    for symbol in sorted(underlyings):
        security_id = equity_ids.get(symbol)
        label = labels.get(symbol)
        if security_id is None or label is None:
            continue
        rows.append(
            UniverseRow(
                symbol=symbol,
                security_id=security_id,
                company_name=label.company_name,
                nse_industry=label.nse_industry,
                is_defence=label.is_defence,
                fno_lot_size=lots.get(symbol, 0),
            )
        )
    return rows


def write_manifest(rows: Sequence[UniverseRow], path: Path = MANIFEST_PATH) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row.symbol,
                    row.security_id,
                    row.company_name,
                    row.nse_industry,
                    "true" if row.is_defence else "false",
                    row.fno_lot_size,
                ]
            )
    return path


def load_manifest(path: Path = MANIFEST_PATH) -> list[UniverseRow]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing -- run `python -m research.fno.manifest --write` first."
        )
    with path.open(newline="") as handle:
        return [
            UniverseRow(
                symbol=r["symbol"],
                security_id=r["security_id"],
                company_name=r["company_name"],
                nse_industry=r["nse_industry"],
                is_defence=r["is_defence"] == "true",
                fno_lot_size=int(r["fno_lot_size"] or 0),
            )
            for r in csv.DictReader(handle)
        ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write research/fno/universe.csv.")
    args = parser.parse_args(argv)

    print("downloading Dhan scrip master (~24 MB) ...", file=sys.stderr)
    scrip = fetch_instrument_master_csv()
    print("downloading NSE Nifty 500 + India Defence constituents ...", file=sys.stderr)
    nifty_500 = sectors_mod.fetch_index_constituents_csv(sectors_mod.NIFTY_500_URL)
    defence = sectors_mod.fetch_index_constituents_csv(sectors_mod.DEFENCE_URL)

    underlyings = parse_fno_underlyings(scrip)
    rows = build_universe(scrip, nifty_500, defence)
    dropped = sorted(underlyings - {r.symbol for r in rows})

    by_sector: dict[str, int] = {}
    for row in rows:
        by_sector[row.nse_industry] = by_sector.get(row.nse_industry, 0) + 1

    print(f"\n{len(underlyings)} FUTSTK underlyings -> {len(rows)} in universe", file=sys.stderr)
    print(f"dropped {len(dropped)}: {', '.join(dropped) if dropped else '(none)'}", file=sys.stderr)
    print(f"defence overlay: {sum(1 for r in rows if r.is_defence)} names", file=sys.stderr)
    for sector, count in sorted(by_sector.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {sector}", file=sys.stderr)

    if args.write:
        print(f"\n-> {write_manifest(rows)}", file=sys.stderr)
    else:
        print("\n(dry run -- pass --write to save)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
