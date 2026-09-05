"""Daily USD/INR, cached, for decomposing MCX bullion into its two drivers.

MCX bullion is not a bet on bullion. It is:

    MCX price = (international price in USD) x USDINR x (1 + import duty)

so a long position is simultaneously long metal and short rupee. Over the
backtest window the rupee went from ~74.35 to ~95.38 -- about 28% of pure
tailwind that a long-only trend follower collects for free and that has
nothing whatever to do with a MACD crossover. Nobody has ever checked how
much of the published CAGR that is.

Source is FRED's DEXINUS (the Federal Reserve H.10 India/US rate): daily,
free, no API key, and it is a published reference series rather than a
broker's quote, so it cannot drift with whatever Dhan happened to serve.
RBI's own reference rate would be marginally more canonical for an Indian
contract but is only available through a portal that resists scripting; the
two differ by a few paise, far below the effect being measured.
"""
from __future__ import annotations

import csv
import io
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
CACHE_PATH = CACHE_DIR / "usdinr_dexinus.csv"
FRED_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id=DEXINUS&cosd={start}&coed={end}"
)
#: FRED publishes '.' for non-business days and lags the last few sessions.
MISSING = "."
#: How far to carry the last known rate forward before giving up. FX does not
#: move on an Indian holiday either, so a few days is honest; a month is not.
MAX_FORWARD_FILL_DAYS = 10


def fetch(start: date, end: date, force: bool = False) -> Path:
    if CACHE_PATH.exists() and not force:
        return CACHE_PATH
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = FRED_URL.format(start=start.isoformat(), end=end.isoformat())
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 -- fixed host
        CACHE_PATH.write_bytes(resp.read())
    return CACHE_PATH


def parse(text: str) -> dict[date, float]:
    """FRED CSV -> {date: rate}, skipping the '.' rows for non-business days."""
    rows = csv.DictReader(io.StringIO(text))
    out: dict[date, float] = {}
    for row in rows:
        raw = (row.get("DEXINUS") or "").strip()
        if not raw or raw == MISSING:
            continue
        stamp = (row.get("observation_date") or row.get("DATE") or "").strip()
        if not stamp:
            continue
        out[date.fromisoformat(stamp)] = float(raw)
    return out


def forward_fill(
    rates: dict[date, float], days: Optional[list[date]] = None,
    max_gap: int = MAX_FORWARD_FILL_DAYS,
) -> dict[date, float]:
    """Carry the last known rate over weekends, holidays and FRED's publication lag.

    Only forward, never backward: interpolating a rate from a later
    observation would leak information the trading day did not have. A gap
    longer than `max_gap` is left unfilled rather than papered over -- a
    missing month is a data problem, not a holiday.
    """
    if not rates:
        return {}
    filled = dict(rates)
    if days is None:
        return filled
    known = sorted(rates)
    last: Optional[tuple[date, float]] = None
    idx = 0
    for day in sorted(days):
        while idx < len(known) and known[idx] <= day:
            last = (known[idx], rates[known[idx]])
            idx += 1
        if day in filled:
            continue
        if last is not None and (day - last[0]).days <= max_gap:
            filled[day] = last[1]
    return filled


def load(days: Optional[list[date]] = None, force: bool = False) -> dict[date, float]:
    start = min(days) - timedelta(days=30) if days else date(2020, 1, 1)
    end = max(days) + timedelta(days=1) if days else date.today()
    path = fetch(start, end, force=force)
    return forward_fill(parse(path.read_text()), days)


def annualised_depreciation_pct(rates: dict[date, float]) -> float:
    """Positive means the rupee weakened (more INR per USD) -- a tailwind for
    a long MCX bullion position."""
    if len(rates) < 2:
        return 0.0
    days = sorted(rates)
    first, last = rates[days[0]], rates[days[-1]]
    years = (days[-1] - days[0]).days / 365.25
    if years <= 0 or first <= 0:
        return 0.0
    return ((last / first) ** (1 / years) - 1) * 100
