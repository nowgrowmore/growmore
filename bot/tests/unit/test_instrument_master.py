"""Tests for growmore_bot.broker.instrument_master.find_next_contract.

Fixture CSV rows mirror the real column layout confirmed against a live
download of Dhan's instrument master 2026-09-04 (see module docstring) --
trimmed to just the columns/rows relevant to the filtering logic.
"""
from __future__ import annotations

from datetime import date

from growmore_bot.broker.instrument_master import NextContract, find_next_contract

HEADER = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
    "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
    "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
    "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME"
)


def _row(security_id, symbol, expiry, instrument_name="FUTCOM", exch="MCX"):
    return (
        f"{exch},M,{security_id},{instrument_name},0,{symbol}-FUT,1.0,{symbol} FUT,"
        f"{expiry} 23:30:00,0.00000,XX,100.0000,M,{instrument_name},2,{symbol}"
    )


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def test_finds_the_immediate_next_contract_not_a_later_one():
    csv_text = _csv(
        _row(569003, "GOLDM", "2026-10-05"),  # current -- excluded (not > current_expiry)
        _row(571445, "GOLDM", "2026-11-05"),  # immediate next
        _row(575011, "GOLDM", "2026-12-04"),  # further out -- must NOT be picked
    )

    result = find_next_contract(csv_text, "GOLDM", current_expiry=date(2026, 10, 5))

    assert result == NextContract(security_id="571445", expiry=date(2026, 11, 5))


def test_ignores_option_rows_with_the_same_symbol_name():
    csv_text = _csv(
        _row(578780, "GOLDM", "2026-09-25", instrument_name="OPTFUT"),
        _row(571445, "GOLDM", "2026-11-05", instrument_name="FUTCOM"),
    )

    result = find_next_contract(csv_text, "GOLDM", current_expiry=date(2026, 10, 5))

    assert result == NextContract(security_id="571445", expiry=date(2026, 11, 5))


def test_ignores_other_symbols_and_other_exchanges():
    csv_text = _csv(
        _row(1, "SILVERM", "2026-11-05"),
        _row(2, "GOLDM", "2026-11-05", exch="NSE"),
        _row(571445, "GOLDM", "2026-11-05"),
    )

    result = find_next_contract(csv_text, "GOLDM", current_expiry=date(2026, 10, 5))

    assert result == NextContract(security_id="571445", expiry=date(2026, 11, 5))


def test_returns_none_when_nothing_qualifies():
    csv_text = _csv(_row(569003, "GOLDM", "2026-10-05"))
    assert find_next_contract(csv_text, "GOLDM", current_expiry=date(2026, 10, 5)) is None


def test_returns_none_for_unknown_symbol():
    csv_text = _csv(_row(571445, "GOLDM", "2026-11-05"))
    assert find_next_contract(csv_text, "SOMESYMBOL", current_expiry=date(2026, 10, 5)) is None


def test_returns_none_on_ambiguous_tie_for_minimal_expiry():
    # Defensive: shouldn't happen with clean data, but never guess.
    csv_text = _csv(
        _row(111, "GOLDM", "2026-11-05"),
        _row(222, "GOLDM", "2026-11-05"),
    )
    assert find_next_contract(csv_text, "GOLDM", current_expiry=date(2026, 10, 5)) is None
