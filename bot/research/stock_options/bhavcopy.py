"""Parse NSE's two F&O bhavcopy formats into one option-row shape.

NSE serves UDiFF from roughly 2024-07 onward and a legacy format before that;
this study spans the boundary, so both are needed. Both were confirmed to
download without a token on 2026-09-06:

    UDiFF   .../content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
    legacy  .../content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip

Only STOCK options are kept. Index options (`IDO` / `OPTIDX`) and futures
(`STF` / `FUTSTK`) share the file and are filtered out -- stock options are
the ones that are PHYSICALLY SETTLED, which is the whole reason this study
uses them rather than Nifty.

The legacy format is missing two fields UDiFF carries, and how each is
recovered is the interesting part of this module:

  * **Underlying price** -- absent entirely. Left as None here and filled
    later from the cash-equity OHLCV already cached for all 210 F&O names.
    A silent 0.0 would look like a real price and would put every strike
    infinitely far out of the money.
  * **Lot size** -- absent entirely. Recovered from the turnover identity
    `VAL_INLAKH * 1e5 == contracts * lot_size * price` applied to the
    **futures** rows in the same file, where turnover is unambiguously
    price x quantity. Applying it to option rows does not work: NSE reports
    option turnover on the UNDERLYING's notional rather than the premium, and
    validating that guess against UDiFF's known `NewBrdLotQty` matched only
    2 of 210 symbols. The futures route matched 54 of 210 exactly and every
    other symbol to within ~1% (3,099 vs 3,100; 677 vs 675), the residue
    being intraday price variation against the close.

    That ~1% is immaterial here, and it is worth saying why rather than
    hiding it: positions are sized at one lot and capital is `strike x lot`,
    so the lot size appears in the P&L and in the denominator alike and
    **cancels out of every return**. It survives only in the flat Rs 20
    brokerage, which is basis points on a Rs 7 lakh notional.

A malformed line is skipped rather than raised on: one bad row must not cost
the whole trading day's chain.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
from typing import Optional

#: UDiFF `FinInstrmTp` for a stock option, and the legacy `INSTRUMENT`.
_UDIFF_STOCK_OPTION = "STO"
_LEGACY_STOCK_OPTION = "OPTSTK"
_OPTION_TYPES = ("CE", "PE")


@dataclass(frozen=True)
class OptionRow:
    """One (date, symbol, expiry, strike, type) settlement record."""

    trade_date: date
    symbol: str
    expiry: date
    strike: float
    opt_type: str          # "CE" | "PE"
    open: float
    high: float
    low: float
    close: float
    #: The exchange settlement price. This, not `close`, is what marks a
    #: position and what settles it at expiry.
    settle: float
    open_interest: int
    volume: int            # contracts traded
    lot_size: Optional[int]
    #: None for legacy rows -- filled from the cached cash series later.
    underlying: Optional[float]

    @property
    def is_tradeable(self) -> bool:
        """Whether this strike actually printed.

        Bhavcopy lists every strike the exchange offered, including ones that
        never traded. Selling those in a backtest is fiction, so the chain is
        kept complete (strike selection has to see it) while the engine is
        only allowed to transact where volume was real.
        """
        return self.volume > 0


def infer_lot_size(value_in_lakh: float, contracts: int, price: float) -> Optional[int]:
    """Recover a lot size from the turnover identity.

    `VAL_INLAKH * 1e5 == contracts * lot_size * price`, so the lot size falls
    out -- but only when something traded. Zero contracts or a zero price make
    the identity `0 == 0`, which says nothing; returning a guess there would
    silently mis-size every position built on that row, so it refuses.

    Feed this FUTURES rows, not option rows -- see the module docstring for
    why the option turnover convention defeats it.

    Turnover in the file is rounded to two decimals, so the division rarely
    lands exactly and the result is rounded to the nearest whole contract.
    """
    if contracts <= 0 or price <= 0 or value_in_lakh <= 0:
        return None
    lot = (value_in_lakh * 1e5) / (contracts * price)
    if lot <= 0:
        return None
    return int(round(lot))


def legacy_lot_sizes(csv_text: str) -> dict[str, int]:
    """symbol -> lot size, from the legacy file's own stock-futures rows.

    Takes the median across every traded futures contract for a symbol, so a
    single thin expiry cannot skew it.
    """
    estimates: dict[str, list[int]] = {}
    for raw in csv.DictReader(io.StringIO(csv_text)):
        if (raw.get("INSTRUMENT") or "").strip() != "FUTSTK":
            continue
        lot = infer_lot_size(
            _f(raw.get("VAL_INLAKH")), _i(raw.get("CONTRACTS")), _f(raw.get("CLOSE"))
        )
        if lot:
            estimates.setdefault(raw["SYMBOL"].strip(), []).append(lot)
    return {s: int(median(v)) for s, v in estimates.items() if v}


def _f(value: Optional[str]) -> float:
    return float((value or "0").strip() or 0)


def _i(value: Optional[str]) -> int:
    return int(float((value or "0").strip() or 0))


def parse_udiff(csv_text: str) -> list[OptionRow]:
    """Stock-option rows from a UDiFF bhavcopy (2024-07 onward)."""
    rows: list[OptionRow] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        if (raw.get("FinInstrmTp") or "").strip() != _UDIFF_STOCK_OPTION:
            continue
        opt_type = (raw.get("OptnTp") or "").strip()
        if opt_type not in _OPTION_TYPES:
            continue
        try:
            rows.append(
                OptionRow(
                    trade_date=date.fromisoformat(raw["TradDt"].strip()),
                    symbol=raw["TckrSymb"].strip(),
                    expiry=date.fromisoformat(raw["XpryDt"].strip()),
                    strike=_f(raw.get("StrkPric")),
                    opt_type=opt_type,
                    open=_f(raw.get("OpnPric")),
                    high=_f(raw.get("HghPric")),
                    low=_f(raw.get("LwPric")),
                    close=_f(raw.get("ClsPric")),
                    settle=_f(raw.get("SttlmPric")),
                    open_interest=_i(raw.get("OpnIntrst")),
                    volume=_i(raw.get("TtlTradgVol")),
                    lot_size=_i(raw.get("NewBrdLotQty")) or None,
                    underlying=_f(raw.get("UndrlygPric")) or None,
                )
            )
        except (KeyError, ValueError, AttributeError):
            continue  # one bad line must not cost the day
    return rows


def _legacy_date(value: str) -> date:
    """Legacy dates are `26-Sep-2019` (expiry) or `05-SEP-2019` (timestamp)."""
    return datetime.strptime(value.strip().upper(), "%d-%b-%Y").date()


def parse_legacy(
    csv_text: str, lot_sizes: Optional[dict[str, int]] = None
) -> list[OptionRow]:
    """Stock-option rows from a legacy bhavcopy (before 2024-07).

    `underlying` is always None here. `lot_sizes` defaults to the file's own
    futures-derived table -- see the module docstring.
    """
    lots = lot_sizes if lot_sizes is not None else legacy_lot_sizes(csv_text)
    rows: list[OptionRow] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        if (raw.get("INSTRUMENT") or "").strip() != _LEGACY_STOCK_OPTION:
            continue
        opt_type = (raw.get("OPTION_TYP") or "").strip()
        if opt_type not in _OPTION_TYPES:
            continue
        try:
            close = _f(raw.get("CLOSE"))
            contracts = _i(raw.get("CONTRACTS"))
            rows.append(
                OptionRow(
                    trade_date=_legacy_date(raw["TIMESTAMP"]),
                    symbol=raw["SYMBOL"].strip(),
                    expiry=_legacy_date(raw["EXPIRY_DT"]),
                    strike=_f(raw.get("STRIKE_PR")),
                    opt_type=opt_type,
                    open=_f(raw.get("OPEN")),
                    high=_f(raw.get("HIGH")),
                    low=_f(raw.get("LOW")),
                    close=close,
                    settle=_f(raw.get("SETTLE_PR")),
                    open_interest=_i(raw.get("OPEN_INT")),
                    volume=contracts,
                    lot_size=lots.get(raw["SYMBOL"].strip()),
                    underlying=None,
                )
            )
        except (KeyError, ValueError, AttributeError):
            continue
    return rows


__all__ = [
    "OptionRow", "infer_lot_size", "legacy_lot_sizes",
    "parse_udiff", "parse_legacy",
]
