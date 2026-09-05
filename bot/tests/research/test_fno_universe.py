"""Tests for research.fno.universe -- deriving the NSE F&O underlying list
from Dhan's public scrip master.

Real shapes, confirmed against a live download 2026-09-05 (200,289 rows):

  * A stock-futures row is `SEM_EXM_EXCH_ID == "NSE"` and
    `SEM_INSTRUMENT_NAME == "FUTSTK"` (647 rows -> 228 unique underlyings
    across three expiries).
  * `SM_SYMBOL_NAME` is EMPTY on every FUTSTK row, so the underlying has to
    come out of `SEM_TRADING_SYMBOL`, which is `<UNDERLYING>-<Mon><YYYY>-FUT`
    e.g. "RELIANCE-Sep2026-FUT".
  * 18 of the 228 are NSE exchange test symbols ("011NSETEST" ... "181NSETEST").
    They have REAL equity-segment rows too, so mapping to a security_id does
    not exclude them -- only Nifty-500 membership does.
"""
from __future__ import annotations

from research.fno.universe import (
    nse_equity_security_ids,
    parse_fno_underlyings,
)

HEADER = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,"
    "SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,"
    "SEM_OPTION_TYPE,SEM_TICK_SIZE,SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,"
    "SM_SYMBOL_NAME"
)

FIXTURE_CSV = "\n".join(
    [
        HEADER,
        # Three expiries of the same underlying -- must collapse to one name.
        "NSE,D,58932,FUTSTK,0,RELIANCE-Sep2026-FUT,500.0,RELIANCE 24 SEP FUT,"
        "2026-09-24 14:30:00,-0.01000,XX,5.0000,M,FUT,,",
        "NSE,D,58933,FUTSTK,1,RELIANCE-Oct2026-FUT,500.0,RELIANCE 29 OCT FUT,"
        "2026-10-29 14:30:00,-0.01000,XX,5.0000,M,FUT,,",
        "NSE,D,58934,FUTSTK,2,RELIANCE-Nov2026-FUT,500.0,RELIANCE 26 NOV FUT,"
        "2026-11-26 14:30:00,-0.01000,XX,5.0000,M,FUT,,",
        # An exchange TEST symbol, with a real FUTSTK row AND a real equity row.
        "NSE,D,36687,FUTSTK,0,011NSETEST-Nov2036-FUT,50.0,011NSETEST 27 NOV FUT,"
        "2036-11-27 14:30:00,-0.01000,XX,5.0000,W,FUT,,",
        "NSE,E,36686,EQUITY,0,011NSETEST,1.0,011NSETEST,,,,5.0000,NA,ES,EQ,011NSETEST",
        # The cash-equity rows.
        "NSE,E,2885,EQUITY,0,RELIANCE,1.0,Reliance Industries,,,,5.0000,NA,ES,EQ,RELIANCE",
        "NSE,E,11536,EQUITY,0,TCS,1.0,Tata Consultancy,,,,5.0000,NA,ES,EQ,TCS",
        # Rows that must NOT be picked up as underlyings or as equity.
        "NSE,D,99999,OPTSTK,0,RELIANCE-Sep2026-3000-CE,500.0,,2026-09-24 14:30:00,3000,CE,"
        "5.0000,M,OPT,,",
        "NSE,D,35000,FUTIDX,0,NIFTY-Sep2026-FUT,75.0,NIFTY 24 SEP FUT,"
        "2026-09-24 14:30:00,-0.01000,XX,5.0000,M,FUT,,",
        "MCX,D,114,FUTCOM,1,GOLDM-Oct2026-FUT,1.0,GOLDM 03 OCT FUT,"
        "2026-10-03 23:30:00,-0.01,XX,1.0,M,FUTCOM,,GOLDM",
        "NSE,I,13,INDEX,0,NIFTY,1.0,Nifty 50,,,,5.0000,NA,,,NIFTY",
    ]
)


def test_the_underlying_comes_out_of_the_trading_symbol_not_sm_symbol_name():
    # SM_SYMBOL_NAME is empty on every real FUTSTK row, so a parser that
    # trusted it would return one nameless underlying for the whole exchange.
    assert "RELIANCE" in parse_fno_underlyings(FIXTURE_CSV)


def test_three_expiries_of_one_underlying_collapse_to_one_name():
    assert sorted(parse_fno_underlyings(FIXTURE_CSV)) == ["011NSETEST", "RELIANCE"]


def test_index_futures_options_and_mcx_futures_are_not_underlyings():
    underlyings = parse_fno_underlyings(FIXTURE_CSV)
    assert "NIFTY" not in underlyings  # FUTIDX, not FUTSTK
    assert "GOLDM" not in underlyings  # MCX FUTCOM
    assert not any("-" in u for u in underlyings)  # no OPTSTK strike symbols


def test_equity_ids_come_only_from_the_nse_cash_segment():
    ids = nse_equity_security_ids(FIXTURE_CSV)
    assert ids["RELIANCE"] == "2885"
    assert ids["TCS"] == "11536"
    # The Nifty 50 INDEX row shares segment-ish shape but is segment "I".
    assert "NIFTY" not in ids
    assert "GOLDM" not in ids


def test_a_test_symbol_maps_to_a_real_equity_id_so_mapping_cannot_exclude_it():
    # This is why the membership rule needs a third clause (Nifty 500).
    assert nse_equity_security_ids(FIXTURE_CSV)["011NSETEST"] == "36686"
