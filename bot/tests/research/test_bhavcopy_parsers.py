"""Tests for research.stock_options.bhavcopy -- normalising NSE's two F&O
bhavcopy formats into one row shape.

Both formats are real and both are needed: NSE serves UDiFF from roughly
2024-07 onward and the legacy format before that, and this study spans the
boundary. Confirmed live 2026-09-06 that both download without a token.

The legacy format is missing two fields the UDiFF one carries, and how each
is recovered is the interesting part of this module:

  * **Underlying price** -- absent from legacy entirely. Recovered from the
    cash-equity OHLCV already cached for all 210 F&O names.
  * **Lot size** -- absent from legacy. Recovered from the turnover identity
    applied to the file's own FUTURES rows. It cannot be applied to option
    rows: NSE reports option turnover on the underlying's notional, not the
    premium, and that guess reproduced only 2 of 210 known UDiFF lot sizes
    against 54 exact (and the rest within ~1%) via futures.
"""
from __future__ import annotations

from datetime import date

import pytest

from research.stock_options.bhavcopy import (
    OptionRow,
    infer_lot_size,
    legacy_lot_sizes,
    parse_legacy,
    parse_udiff,
)

UDIFF_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)

UDIFF_CSV = "\n".join(
    [
        UDIFF_HEADER,
        # A real stock option: RELIANCE 1400 PE, Sep 2026 monthly.
        "2026-09-04,2026-09-04,FO,NSE,STO,12345,INE002A01018,RELIANCE,,2026-09-29,"
        "2026-09-29,1400.00,PE,RELIANCE,20.00,24.00,19.00,22.50,22.50,21.00,"
        "1425.60,22.50,1500000,25000,350419,1234.56,4567,F1,500,,,,,",
        # An INDEX option -- must not be picked up by a stock-option parser.
        "2026-09-04,2026-09-04,FO,NSE,IDO,999,,NIFTY,,2026-09-08,2026-09-08,"
        "23900.00,CE,NIFTY,120.00,130.00,118.00,123.80,123.80,119.00,"
        "23897.70,123.80,5027750,1000,3733592,999.9,100,F1,65,,,,,",
        # A stock FUTURE -- also excluded.
        "2026-09-04,2026-09-04,FO,NSE,STF,555,INE002A01018,RELIANCE,,2026-09-29,"
        "2026-09-29,0.00,,RELIANCE,1430.00,1440.00,1425.00,1432.00,1432.00,1428.00,"
        "1425.60,1432.00,9000000,100,50000,888.8,900,F1,500,,,,,",
        # A strike that never printed -- kept, but not tradeable.
        "2026-09-04,2026-09-04,FO,NSE,STO,12399,INE002A01018,RELIANCE,,2026-09-29,"
        "2026-09-29,900.00,PE,RELIANCE,0.00,0.00,0.00,0.05,0.05,0.05,"
        "1425.60,0.05,0,0,0,0.0,0,F1,500,,,,,",
    ]
)

LEGACY_HEADER = (
    "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,"
    "CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,"
)

LEGACY_CSV = "\n".join(
    [
        LEGACY_HEADER,
        # RELIANCE 1400 PE. 100 contracts * 500 lot * 22.50 = Rs 11,25,000 = 11.25 lakh.
        "OPTSTK,RELIANCE,26-Sep-2019,1400.00,PE,20.00,24.00,19.00,22.50,22.50,"
        "100,11.25,1500000,25000,05-SEP-2019,",
        # An index option -- excluded.
        "OPTIDX,NIFTY,26-Sep-2019,11000.00,CE,120.00,130.00,118.00,123.80,123.80,"
        "500,300.00,5027750,1000,05-SEP-2019,",
        # A stock future -- excluded.
        "FUTSTK,RELIANCE,26-Sep-2019,0.00,,1430.00,1440.00,1425.00,1432.00,1432.00,"
        "50,358.00,9000000,100,05-SEP-2019,",
        # Never traded: zero contracts, zero turnover.
        "OPTSTK,RELIANCE,26-Sep-2019,900.00,PE,0.00,0.00,0.00,0.05,0.05,"
        "0,0.00,0,0,05-SEP-2019,",
    ]
)


def test_udiff_yields_only_stock_options():
    rows = parse_udiff(UDIFF_CSV)
    assert all(isinstance(r, OptionRow) for r in rows)
    assert {r.symbol for r in rows} == {"RELIANCE"}
    # The NIFTY index option and the RELIANCE future are both excluded.
    assert len(rows) == 2


def test_udiff_carries_every_field_the_engine_needs():
    row = next(r for r in parse_udiff(UDIFF_CSV) if r.strike == 1400.0)
    assert row.trade_date == date(2026, 9, 4)
    assert row.expiry == date(2026, 9, 29)
    assert row.opt_type == "PE"
    assert row.settle == 22.50
    assert row.close == 22.50
    assert row.open_interest == 1_500_000
    assert row.volume == 350_419
    assert row.lot_size == 500
    assert row.underlying == pytest.approx(1425.60)


def test_legacy_normalises_to_the_identical_shape():
    udiff = next(r for r in parse_udiff(UDIFF_CSV) if r.strike == 1400.0)
    legacy = next(r for r in parse_legacy(LEGACY_CSV) if r.strike == 1400.0)
    assert type(udiff) is type(legacy)
    # Same fields populated, same types -- only the values differ by date.
    assert legacy.symbol == udiff.symbol == "RELIANCE"
    assert legacy.opt_type == udiff.opt_type == "PE"
    assert legacy.settle == udiff.settle
    assert legacy.lot_size == udiff.lot_size == 500
    assert legacy.trade_date == date(2019, 9, 5)
    assert legacy.expiry == date(2019, 9, 26)


def test_legacy_excludes_index_options_and_futures_too():
    rows = parse_legacy(LEGACY_CSV)
    assert {r.symbol for r in rows} == {"RELIANCE"}
    assert all(r.opt_type in ("CE", "PE") for r in rows)
    assert len(rows) == 2


def test_legacy_lot_size_comes_out_of_the_turnover_identity():
    # VAL_INLAKH * 1e5 == contracts * lot_size * price, so lot_size falls out.
    # 11.25 lakh / (100 contracts * 22.50) = 500.
    assert infer_lot_size(value_in_lakh=11.25, contracts=100, price=22.50) == 500


def test_legacy_lot_size_is_taken_from_the_futures_row_not_the_option_row():
    """The convention that defeats the obvious approach.

    NSE reports OPTION turnover on the underlying's notional rather than on
    the premium, so the identity does not hold on an option row. Validated
    against UDiFF's known lot sizes: the option route matched 2 of 210
    symbols, the futures route 54 exactly and the rest within ~1%.

    Here the futures row is 50 contracts x 500 lot x 1432.00 = 358.00 lakh,
    which recovers 500. The 1400 PE row, taken at face value, would give
    11.25e5 / (100 * 22.50) = 500 only by coincidence of this fixture --
    on real data it returns tens of thousands.
    """
    assert legacy_lot_sizes(LEGACY_CSV) == {"RELIANCE": 500}
    # And the parsed option rows carry the futures-derived value.
    assert {r.lot_size for r in parse_legacy(LEGACY_CSV)} == {500}


def test_an_option_row_that_never_traded_still_gets_the_symbols_lot_size():
    # Lot size is a property of the CONTRACT, not of whether a given strike
    # printed -- so an untraded strike must still be sized correctly if the
    # engine ever needs to price it.
    dead = next(r for r in parse_legacy(LEGACY_CSV) if r.strike == 900.0)
    assert dead.volume == 0
    assert dead.lot_size == 500


def test_lot_size_is_unknowable_when_nothing_traded():
    # Zero contracts makes the identity 0 == 0, which says nothing. Returning
    # a wrong lot size here would silently mis-size every position in a
    # backtest, so it must refuse rather than guess.
    assert infer_lot_size(value_in_lakh=0.0, contracts=0, price=0.05) is None
    assert infer_lot_size(value_in_lakh=11.25, contracts=100, price=0.0) is None


def test_lot_size_rounds_to_the_nearest_whole_contract():
    # Real turnover figures are rounded to 2dp in the file, so the identity
    # rarely divides exactly. 11.24 lakh / (100 * 22.50) = 499.55 -> 500.
    assert infer_lot_size(value_in_lakh=11.24, contracts=100, price=22.50) == 500


def test_a_strike_that_never_printed_is_kept_but_not_tradeable():
    # The chain must be complete for strike selection to be honest, but a
    # strike with no volume cannot be sold -- bhavcopy lists strikes that
    # never traded, and "selling" them is fiction.
    for parse, csv_text in ((parse_udiff, UDIFF_CSV), (parse_legacy, LEGACY_CSV)):
        dead = next(r for r in parse(csv_text) if r.strike == 900.0)
        assert dead.volume == 0
        assert dead.is_tradeable is False
        live = next(r for r in parse(csv_text) if r.strike == 1400.0)
        assert live.is_tradeable is True


def test_legacy_rows_have_no_underlying_price_and_say_so():
    # Legacy bhavcopy simply does not carry it; it is recovered later from the
    # cached cash-equity series. A silent 0.0 would look like a real price.
    legacy = next(r for r in parse_legacy(LEGACY_CSV) if r.strike == 1400.0)
    assert legacy.underlying is None


def test_a_blank_or_malformed_row_is_skipped_rather_than_crashing_the_day():
    # One bad line must not cost the whole trading day's chain.
    broken = LEGACY_CSV + "\nOPTSTK,RELIANCE,not-a-date,1400.00,PE,x,y,z,,,,,,,\n"
    assert len(parse_legacy(broken)) == 2
