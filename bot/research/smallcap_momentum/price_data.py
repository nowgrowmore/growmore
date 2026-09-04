"""Fetch + locally cache 5 years of daily NSE_EQ bars per stock via the
EXISTING growmore_bot.broker.dhan_client.DhanClient -- confirmed live
2026-09-04 to work unchanged for NSE_EQ/EQUITY (4,134 real daily bars,
2010-01-03 to 2026-08-30, for TTML). No changes to dhan_client.py.

Sequential, rate-limited (Dhan's historical-data endpoint is reported ~1
req/sec in practice despite a higher advertised limit) with retry-with-
backoff on transient failures. Each stock's bars are cached to a local
parquet file so a crash or an expired token mid-run doesn't require
re-fetching everything already done.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_DELAY_SECONDS = 1.2
DEFAULT_MAX_RETRIES = 3


@dataclass(frozen=True)
class FetchResult:
    fetched: list[str]
    cached: list[str]
    failed: list[str]


def _cache_path(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / f"{symbol}.parquet"


def load_cached_bars(cache_dir: Path, symbol: str) -> Optional[pd.DataFrame]:
    path = _cache_path(cache_dir, symbol)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def _save_bars(cache_dir: Path, symbol: str, bars: list) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        {
            "date": [b.timestamp.date() for b in bars],
            "close": [b.close for b in bars],
            "volume": [getattr(b, "volume", None) for b in bars],
        }
    )
    df.to_parquet(_cache_path(cache_dir, symbol))
    return df


def fetch_all_price_histories(
    dhan_client,
    symbol_to_security_id: dict[str, str],
    from_date: str,
    to_date: str,
    cache_dir: Path,
    request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> FetchResult:
    """Populates `cache_dir` with one parquet file per symbol. Resumable:
    a symbol whose cache file already exists is skipped entirely (not
    re-validated against `from_date`/`to_date` -- delete the file to force
    a re-fetch of that one symbol).
    """
    fetched: list[str] = []
    cached: list[str] = []
    failed: list[str] = []

    for symbol, security_id in symbol_to_security_id.items():
        if _cache_path(cache_dir, symbol).exists():
            cached.append(symbol)
            continue

        instrument = SimpleNamespace(
            security_id=security_id, exchange_segment="NSE_EQ", instrument_type="EQUITY"
        )
        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                bars = dhan_client.get_historical_ohlc(
                    instrument, from_date=from_date, to_date=to_date, interval="day"
                )
                _save_bars(cache_dir, symbol, bars)
                fetched.append(symbol)
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 -- logged and retried, never crashes the batch
                last_error = exc
                logger.warning(
                    "Fetch failed for %s (attempt %d/%d): %s", symbol, attempt, max_retries, exc
                )
                time.sleep(request_delay_seconds * attempt)
        if last_error is not None:
            failed.append(symbol)
            logger.error("Giving up on %s after %d attempts: %s", symbol, max_retries, last_error)

        time.sleep(request_delay_seconds)

    return FetchResult(fetched=fetched, cached=cached, failed=failed)


__all__ = ["FetchResult", "load_cached_bars", "fetch_all_price_histories"]
