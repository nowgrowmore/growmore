"""Fetch + locally cache ROE / debt-equity / EPS growth per stock via
yfinance -- an unofficial, best-effort library, used here ONLY because
Dhan's Data API has no fundamentals endpoint and no clean, free, bulk-
friendly official alternative was found for ~400 Indian small/mid-caps
(see docs/smallcap-momentum-research.md). Coverage gaps for smaller names
are expected and reported explicitly, never silently zero-filled -- a stock
missing fundamentals is still eligible on momentum alone (see
scoring.composite_score).

`yfinance` is a `research` extra (pyproject.toml), never a dependency of
the live trading bot.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_DELAY_SECONDS = 0.5


@dataclass(frozen=True)
class FundamentalsResult:
    covered: list[str]
    missing: list[str]


def _cache_path(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / f"{symbol}.json"


def load_cached_fundamentals(cache_dir: Path, symbol: str) -> Optional[tuple[float, float, float]]:
    path = _cache_path(cache_dir, symbol)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("roe") is None or data.get("debt_to_equity") is None or data.get("eps_growth") is None:
        return None
    return (data["roe"], data["debt_to_equity"], data["eps_growth"])


def _derive_roe(info: dict) -> Optional[float]:
    """`returnOnEquity` is NOT populated by this yfinance version's `.info`
    for any ticker checked (confirmed live 2026-09-04, several real NSE
    symbols) -- an undocumented library shift, not a per-stock coverage
    gap. Derived instead from primitives that ARE populated: net income /
    book equity (bookValue is per-share, so multiplied by shares
    outstanding). A non-positive book equity (real for at least one
    financially-distressed stock checked, TTML) makes the ratio
    sign-inverted and meaningless -- treated as unavailable, not computed.
    """
    net_income = info.get("netIncomeToCommon")
    book_value_per_share = info.get("bookValue")
    shares_outstanding = info.get("sharesOutstanding")
    if net_income is None or book_value_per_share is None or shares_outstanding is None:
        return None
    book_equity = book_value_per_share * shares_outstanding
    if book_equity <= 0:
        return None
    return net_income / book_equity


def _extract_from_yfinance_info(info: dict) -> dict[str, Optional[float]]:
    """`info` is `yfinance.Ticker(...).info` -- an unofficial, undocumented
    dict whose keys have shifted before; extracted defensively (missing key
    -> None, never a KeyError).
    """
    return {
        "roe": _derive_roe(info),
        "debt_to_equity": info.get("debtToEquity"),
        "eps_growth": info.get("earningsGrowth"),
    }


def fetch_all_fundamentals(
    symbols: list[str],
    cache_dir: Path,
    request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
) -> FundamentalsResult:
    import yfinance as yf  # imported lazily -- optional `research` extra

    cache_dir.mkdir(parents=True, exist_ok=True)
    covered: list[str] = []
    missing: list[str] = []

    for symbol in symbols:
        path = _cache_path(cache_dir, symbol)
        if path.exists():
            (covered if load_cached_fundamentals(cache_dir, symbol) else missing).append(symbol)
            continue

        try:
            info = yf.Ticker(f"{symbol}.NS").info
            extracted = _extract_from_yfinance_info(info)
        except Exception as exc:  # noqa: BLE001 -- one symbol's failure never aborts the batch
            logger.warning("Fundamentals fetch failed for %s: %s", symbol, exc)
            extracted = {"roe": None, "debt_to_equity": None, "eps_growth": None}

        path.write_text(json.dumps(extracted))
        if all(v is not None for v in extracted.values()):
            covered.append(symbol)
        else:
            missing.append(symbol)

        time.sleep(request_delay_seconds)

    return FundamentalsResult(covered=covered, missing=missing)


__all__ = ["FundamentalsResult", "load_cached_fundamentals", "fetch_all_fundamentals"]
