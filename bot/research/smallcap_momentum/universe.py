"""NSE index constituent lists (Nifty Smallcap 250 / Nifty Midcap 150).

Real, public CSVs -- confirmed fetchable 2026-09-04 with a browser-like
User-Agent (NSE's Akamai front-end blocks/times out default HTTP clients
without one):

    https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv  (250 rows)
    https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv    (150 rows)

Shape confirmed live: 'Company Name,Industry,Symbol,Series,ISIN Code'. There is
no point-in-time historical version of these files identified anywhere in this
research -- using them means today's constituents, not the constituents that
were actually in the index at each point in backtest history (survivorship
bias, accepted and documented in docs/smallcap-momentum-research.md).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import requests

SMALLCAP_250_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv"
MIDCAP_150_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv"

# NSE's archives host blocks/times out requests without a browser-like
# User-Agent (confirmed live 2026-09-04) -- not a real browser fingerprint,
# just enough to pass whatever check is in front of it.
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


@dataclass(frozen=True)
class Constituent:
    symbol: str
    company_name: str
    industry: str


def fetch_index_constituents_csv(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.text


def parse_constituents(csv_text: str) -> list[Constituent]:
    reader = csv.DictReader(io.StringIO(csv_text))
    result: list[Constituent] = []
    for row in reader:
        symbol = (row.get("Symbol") or "").strip()
        if not symbol:
            continue
        result.append(
            Constituent(
                symbol=symbol,
                company_name=(row.get("Company Name") or "").strip(),
                industry=(row.get("Industry") or "").strip(),
            )
        )
    return result


__all__ = [
    "SMALLCAP_250_URL",
    "MIDCAP_150_URL",
    "Constituent",
    "fetch_index_constituents_csv",
    "parse_constituents",
]
