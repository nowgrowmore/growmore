"""Tests for research.mcx_options.bhavcopy.

The column names exercised here (SYMBOL, EXPIRY, STRIKE, OPTIONTYPE, OPEN,
HIGH, LOW, CLOSE, PREVIOUS_CLOSE, VOLUME, VALUE, OPEN_INT) are NOT confirmed
against a real downloaded MCX bhavcopy file -- see the module docstring in
research/mcx_options/bhavcopy.py. This fixture is built to that unverified,
plausible schema so the parser has something concrete to run against; it is
not proof the real file looks like this.
"""
from __future__ import annotations

from datetime import date

from research.mcx_options.bhavcopy import MCXOptionRow, parse_bhavcopy

HEADER = (
    "SYMBOL,EXPIRY,STRIKE,OPTIONTYPE,OPEN,HIGH,LOW,CLOSE,PREVIOUS_CLOSE,"
    "VOLUME,VALUE,OPEN_INT"
)

CSV_TEXT = "\n".join(
    [
        HEADER,
        # A real-looking Gold Mini call that traded.
        "GOLDM,26-SEP-2026,72000,CE,850.00,920.00,800.00,880.00,845.00,"
        "1250,1100000.50,4200",
        # A Silver Mini put that traded.
        "SILVERM,30-NOV-2026,95000,PE,1200.00,1300.00,1150.00,1250.00,1180.00,"
        "300,375000.00,900",
        # A future in the same file -- OPTIONTYPE blank, must be excluded.
        "GOLDM,26-SEP-2026,0,,71500.00,72200.00,71000.00,71950.00,71400.00,"
        "5000,3.6e8,15000",
        # A strike MCX quotes but that never traded today -- kept, not
        # tradeable.
        "GOLDM,26-SEP-2026,75000,PE,0.00,0.00,0.00,10.00,10.00,0,0.00,50",
    ]
)


def test_parses_only_option_rows_not_futures():
    rows = parse_bhavcopy(CSV_TEXT, trade_date=date(2026, 9, 4))
    assert all(isinstance(r, MCXOptionRow) for r in rows)
    assert all(r.opt_type in ("CE", "PE") for r in rows)
    assert len(rows) == 3


def test_a_row_carries_every_field_the_engine_needs():
    rows = parse_bhavcopy(CSV_TEXT, trade_date=date(2026, 9, 4))
    row = next(r for r in rows if r.symbol == "GOLDM" and r.strike == 72000.0)
    assert row.trade_date == date(2026, 9, 4)
    assert row.expiry == date(2026, 9, 26)
    assert row.opt_type == "CE"
    assert row.open == 850.00
    assert row.high == 920.00
    assert row.low == 800.00
    assert row.close == 880.00
    assert row.previous_close == 845.00
    assert row.volume == 1250
    assert row.open_interest == 4200
    assert row.value == 1_100_000.50


def test_silvermini_row_parses_too():
    rows = parse_bhavcopy(CSV_TEXT, trade_date=date(2026, 9, 4))
    row = next(r for r in rows if r.symbol == "SILVERM")
    assert row.expiry == date(2026, 11, 30)
    assert row.opt_type == "PE"
    assert row.strike == 95000.0


def test_a_strike_that_never_traded_is_kept_but_not_tradeable():
    rows = parse_bhavcopy(CSV_TEXT, trade_date=date(2026, 9, 4))
    dead = next(r for r in rows if r.strike == 75000.0)
    assert dead.volume == 0
    assert dead.is_tradeable is False
    live = next(r for r in rows if r.strike == 72000.0)
    assert live.is_tradeable is True


def test_a_blank_or_malformed_row_is_skipped_rather_than_crashing_the_day():
    broken = CSV_TEXT + "\nGOLDM,not-a-date,72000,CE,x,y,z,,,,,\n"
    rows = parse_bhavcopy(broken, trade_date=date(2026, 9, 4))
    assert len(rows) == 3  # same 3 valid option rows as before, no crash
