"""Tests for research.smallcap_momentum.security_mapping -- matching an NSE
equity trading symbol to its Dhan `NSE_EQ` security_id via the real public
scrip master CSV shape (confirmed live 2026-09-04):

    SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,...,
    SEM_TRADING_SYMBOL,...

e.g. NSE,E,8954,EQUITY,...,TTML,... for Tata Teleservices Maharashtra.
"""
from __future__ import annotations

from research.smallcap_momentum.security_mapping import match_nse_equity_security_id

HEADER = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,"
    "SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,"
    "SEM_OPTION_TYPE,SEM_TICK_SIZE,SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,"
    "SM_SYMBOL_NAME"
)

FIXTURE_CSV = "\n".join(
    [
        HEADER,
        "NSE,E,8954,EQUITY,0,TTML,1.0,Tata Teleservices Maharashtra,,,,1.0000,NA,ES,EQ,"
        "TATA TELESERV(MAHARASTRA)",
        # A commodity FUTCOM row for the same-ish symbol shape -- must not match.
        "MCX,D,12345,FUTCOM,1,TTML,1.0,,2026-12-31 23:30:00,,,0.01,NA,,, ",
        # A different NSE segment (e.g. F&O) sharing the symbol -- must not match.
        "NSE,D,99999,OPTSTK,1,TTML,1.0,,2026-12-31 23:30:00,100,CE,0.05,NA,,,",
    ]
)


def test_matches_the_real_nse_equity_row():
    security_id = match_nse_equity_security_id(FIXTURE_CSV, "TTML")
    assert security_id == "8954"


def test_returns_none_for_an_unlisted_symbol():
    assert match_nse_equity_security_id(FIXTURE_CSV, "NOTREAL") is None


def test_ignores_non_nse_equity_segment_rows_sharing_the_symbol():
    # MCX/F&O rows above share "TTML" -- confirms the exchange/segment filter
    # actually discriminates, not just a bare symbol match.
    csv_without_equity_row = "\n".join(FIXTURE_CSV.splitlines()[0:1] + FIXTURE_CSV.splitlines()[2:])
    assert match_nse_equity_security_id(csv_without_equity_row, "TTML") is None
