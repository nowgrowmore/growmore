"""Dhan's public instrument master -- used only to look up the next MCX
futures contract month when an instrument nears its close-out cutoff (see
growmore_bot.scheduler.contract_rollover). Never used for anything else;
security IDs for the *current* 8-commodity universe are still hand-verified
once in growmore_bot.config.DEFAULT_COMMODITY_UNIVERSE.

Schema confirmed against a real download 2026-09-04 (columns, MCX/FUTCOM
filter values, and that GOLDM/COPPER/NICKEL/etc.'s current security_id in
config.py matches this file's current front-month row exactly):

    SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,
    SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,
    SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,
    SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME

A commodity future is `SEM_EXM_EXCH_ID == "MCX"`, `SEM_INSTRUMENT_NAME ==
"FUTCOM"` (excludes options -- `OPTFUT` rows share the same `SM_SYMBOL_NAME`),
`SM_SYMBOL_NAME` matching our symbol exactly (e.g. "GOLDM"). `SEM_LOT_UNITS`
is NOT the real contract trading unit (it's 1.0 for every row observed,
including Gold Mini) -- deliberately not used here; lot_size never changes
between contract months anyway, and stays whatever's already in the
`instruments` table.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import requests

INSTRUMENT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


@dataclass(frozen=True)
class NextContract:
    security_id: str
    expiry: date


def fetch_instrument_master_csv() -> str:
    response = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    response.raise_for_status()
    return response.text


def find_next_contract(csv_text: str, symbol: str, current_expiry: date) -> Optional[NextContract]:
    """The MCX FUTCOM contract for `symbol` with the smallest expiry that's
    strictly after `current_expiry` -- i.e. the immediate next contract
    month, not just any later one.

    Returns None if nothing qualifies, or (defensively) if more than one row
    ties for that same minimal expiry -- an ambiguous match should fall back
    to a manual lookup rather than guess.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    candidates: list[NextContract] = []
    for row in reader:
        if row.get("SEM_EXM_EXCH_ID") != "MCX":
            continue
        if row.get("SEM_INSTRUMENT_NAME") != "FUTCOM":
            continue
        if row.get("SM_SYMBOL_NAME") != symbol:
            continue
        try:
            expiry = datetime.strptime(row["SEM_EXPIRY_DATE"], "%Y-%m-%d %H:%M:%S").date()
        except (KeyError, ValueError):
            continue
        if expiry <= current_expiry:
            continue
        candidates.append(NextContract(security_id=row["SEM_SMST_SECURITY_ID"], expiry=expiry))

    if not candidates:
        return None

    min_expiry = min(c.expiry for c in candidates)
    matching = [c for c in candidates if c.expiry == min_expiry]
    if len(matching) != 1:
        return None
    return matching[0]


__all__ = ["INSTRUMENT_MASTER_URL", "NextContract", "fetch_instrument_master_csv", "find_next_contract"]
