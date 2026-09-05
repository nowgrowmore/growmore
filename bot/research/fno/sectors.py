"""NSE's own macro-sector label for the F&O universe, plus a Defence overlay.

The `Industry` column of NSE's public index-constituent CSVs is the only
sector classification available anywhere in this repo without hand-curation.
`research.smallcap_momentum.universe` already parses exactly this file shape
(`Company Name,Industry,Symbol,Series,ISIN Code`) and already carries the
browser-like User-Agent NSE's Akamai front-end demands, so both are reused
verbatim rather than reimplemented.

Confirmed live 2026-09-05: all 210 real F&O underlyings appear in the Nifty
500 list, spread over 18 macro sectors:

    Financial Services 55   Capital Goods 23   Healthcare 16   Auto 16
    FMCG 14   Information Technology 13   Metals & Mining 10
    Consumer Durables 10   Oil Gas & Consumable Fuels 9   Consumer Services 9
    Power 8   Realty 6   Services 5   Chemicals 5   Construction Materials 4
    Telecommunication 3   Construction 3   Textiles 1

**Defence is not one of them, and cannot be.** NSE has no defence sector;
its defence names live under Capital Goods (BEL, HAL, BDL, MAZDOCK,
COCHINSHIP), Automobile and Auto Components (BHARATFORG) and Chemicals
(SOLARINDS). Making Defence a 19th mutually-exclusive bucket would pull
those seven out of the sectors they genuinely belong to and quietly distort
every sector count. So it is a NON-EXCLUSIVE overlay taken from the NIFTY
India Defence constituent list -- 19 names, 7 of them in the F&O universe.

Sector is recorded as a ROBUSTNESS AXIS and a concentration control, not a
selection filter. Over 2021-2026 Capital Goods and Defence *were* the boom;
picking the best sector after the fact is the same best-of-N error as
picking the best stock (docs/crosstrend-results.md).
"""
from __future__ import annotations

from dataclasses import dataclass

from research.smallcap_momentum.universe import (
    fetch_index_constituents_csv,
    parse_constituents,
)

NIFTY_500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"

#: NOTE the underscore before "list". Every other index file in that
#: directory omits it (`ind_niftyitlist.csv`, `ind_niftyrealtylist.csv`);
#: this one 404s without it. Confirmed live 2026-09-05.
DEFENCE_URL = (
    "https://nsearchives.nseindia.com/content/indices/ind_niftyindiadefence_list.csv"
)


@dataclass(frozen=True)
class SectorLabel:
    company_name: str
    #: NSE's own macro-economic sector, one of 18. Mutually exclusive.
    nse_industry: str
    #: Overlay, NOT a bucket -- a defence name keeps its NSE sector.
    is_defence: bool


def build_sector_map(
    nifty_500_csv: str, defence_csv: str, symbols: set[str]
) -> dict[str, SectorLabel]:
    """Label every symbol in `symbols` that the Nifty 500 knows about.

    A symbol absent from the Nifty 500 gets NO entry, and that omission is
    load-bearing: it is the clause that excludes the 18 "011NSETEST"-style
    exchange test symbols, which have both FUTSTK rows and real cash-equity
    security_ids and so survive every other filter.
    """
    defence = {c.symbol for c in parse_constituents(defence_csv)}
    labels: dict[str, SectorLabel] = {}
    for constituent in parse_constituents(nifty_500_csv):
        if constituent.symbol not in symbols:
            continue
        labels[constituent.symbol] = SectorLabel(
            company_name=constituent.company_name,
            nse_industry=constituent.industry,
            is_defence=constituent.symbol in defence,
        )
    return labels


def fetch_sector_map(symbols: set[str]) -> dict[str, SectorLabel]:
    """Live variant of `build_sector_map`. Two HTTP GETs, no caching."""
    return build_sector_map(
        fetch_index_constituents_csv(NIFTY_500_URL),
        fetch_index_constituents_csv(DEFENCE_URL),
        symbols,
    )


__all__ = ["NIFTY_500_URL", "DEFENCE_URL", "SectorLabel", "build_sector_map", "fetch_sector_map"]
