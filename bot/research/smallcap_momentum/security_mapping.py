"""Map an NSE equity trading symbol to its Dhan `NSE_EQ` security_id, via
Dhan's own public instrument-master CSV -- the same file
growmore_bot.broker.instrument_master already downloads for MCX contract
rollover (fetch_instrument_master_csv is reused as-is, unchanged).

A cash-equity row is `SEM_EXM_EXCH_ID == "NSE"` and `SEM_SEGMENT == "E"` --
confirmed live 2026-09-04 against a real download (e.g. `NSE,E,8954,EQUITY,
...,TTML,...` for Tata Teleservices Maharashtra). The same symbol can also
appear on other exchanges/segments (MCX, NSE F&O) in the same file; both
fields are required to avoid matching those instead.
"""
from __future__ import annotations

import csv
import io
from typing import Optional


def match_nse_equity_security_id(csv_text: str, symbol: str) -> Optional[str]:
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        if row.get("SEM_EXM_EXCH_ID") != "NSE":
            continue
        if row.get("SEM_SEGMENT") != "E":
            continue
        if row.get("SEM_TRADING_SYMBOL") != symbol:
            continue
        security_id = row.get("SEM_SMST_SECURITY_ID")
        if security_id:
            return security_id
    return None


__all__ = ["match_nse_equity_security_id"]
