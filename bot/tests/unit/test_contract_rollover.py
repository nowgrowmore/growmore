"""Known-input/known-output tests for the pre-expiry close-out guard.

Mirrors Dhan's own real MCX delivery rules (verified against Dhan's Risk
Management Policy and settlement-policy support article, 2026-09-03):
compulsory-delivery contracts get force-squared-off "post 11:00 AM on the
trading day prior to the commencement of the Tender Period", and the Tender
Period itself starts 5 trading days before expiry for bullion (Gold/Silver
Mini), 3 for base metals (Copper/Zinc/Nickel/Aluminium/Lead Mini). Crude Oil
Mini is cash-settled -- no delivery obligation, so no close-out applies.

Dates use 2024-01-01 (a real Monday) as an anchor so exact weekdays can be
hand-verified without needing to know any specific future year's calendar.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from growmore_bot.broker.dhan_client import Quote
from growmore_bot.scheduler.contract_rollover import (
    close_out_cutoff_date,
    is_past_close_out_cutoff,
    roll_to_next_contract,
)

MONDAY = date(2024, 1, 1)
FRIDAY = MONDAY + timedelta(days=4)  # 2024-01-05


def test_bullion_cutoff_is_eight_trading_days_before_expiry():
    # tender_days(5) + dhan's own 1-day-prior square-off + default 2-day
    # safety buffer = 8 trading days back from expiry.
    # Fri Jan5 -> Thu4,Wed3,Tue2,Mon1,Fri(Dec29),Thu28,Wed27,Tue26 = Dec26,2023
    assert close_out_cutoff_date("GOLDM", FRIDAY) == date(2023, 12, 26)
    assert close_out_cutoff_date("SILVERM", FRIDAY) == date(2023, 12, 26)


def test_base_metal_cutoff_is_six_trading_days_before_expiry():
    # tender_days(3) + 1 + 2 = 6 trading days back from expiry.
    # Fri Jan5 -> Thu4,Wed3,Tue2,Mon1,Fri(Dec29),Thu28 = Dec28,2023
    for symbol in ("COPPER", "ZINCMINI", "NICKEL", "ALUMINI", "LEADMINI"):
        assert close_out_cutoff_date(symbol, FRIDAY) == date(2023, 12, 28)


def test_cash_settled_crude_has_no_close_out_cutoff():
    # No delivery obligation -- Dhan doesn't force-close it, so we don't either.
    assert close_out_cutoff_date("CRUDEOILM", FRIDAY) is None


def test_unknown_symbol_has_no_close_out_cutoff():
    assert close_out_cutoff_date("SOMENEWSYMBOL", FRIDAY) is None


def test_none_expiry_has_no_close_out_cutoff():
    assert close_out_cutoff_date("GOLDM", None) is None


def test_safety_buffer_is_configurable():
    assert close_out_cutoff_date("GOLDM", FRIDAY, safety_buffer_trading_days=0) == date(2023, 12, 28)


def test_is_past_close_out_cutoff_true_on_and_after_cutoff_date():
    cutoff = date(2023, 12, 26)
    assert is_past_close_out_cutoff("GOLDM", FRIDAY, today=cutoff) is True
    assert is_past_close_out_cutoff("GOLDM", FRIDAY, today=cutoff + timedelta(days=1)) is True


def test_is_past_close_out_cutoff_false_before_cutoff_date():
    cutoff = date(2023, 12, 26)
    assert is_past_close_out_cutoff("GOLDM", FRIDAY, today=cutoff - timedelta(days=1)) is False


def test_is_past_close_out_cutoff_false_for_cash_settled():
    assert is_past_close_out_cutoff("CRUDEOILM", FRIDAY, today=date(2026, 1, 1)) is False


_CSV_HEADER = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
    "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
    "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
    "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME"
)


def _csv_row(security_id, symbol, expiry):
    return (
        f"MCX,M,{security_id},FUTCOM,0,{symbol}-FUT,1.0,{symbol} FUT,"
        f"{expiry} 23:30:00,0.00000,XX,100.0000,M,FUTCOM,2,{symbol}"
    )


def _instrument(**overrides):
    defaults = dict(
        id=uuid.uuid4(), symbol="GOLDM", exchange_segment="MCX_COMM",
        security_id="569003", contract_expiry=date(2026, 10, 5), lot_size=100,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_roll_to_next_contract_updates_instrument_and_writes_audit_log():
    instrument = _instrument()
    csv_text = "\n".join([_CSV_HEADER, _csv_row(571445, "GOLDM", "2026-11-05")])
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    session = MagicMock()

    result = roll_to_next_contract(session, dhan_client, instrument, csv_text)

    assert result is True
    assert instrument.security_id == "571445"
    assert instrument.contract_expiry == date(2026, 11, 5)
    # Validated the candidate with a real quote request before committing.
    quoted_instrument = dhan_client.get_quote.call_args[0][0]
    assert quoted_instrument.security_id == "571445"

    added = [c.args[0] for c in session.add.call_args_list]
    audit_entries = [obj for obj in added if hasattr(obj, "event_type")]
    assert len(audit_entries) == 1
    assert audit_entries[0].event_type == "contract_rolled"
    assert audit_entries[0].payload["old_security_id"] == "569003"
    assert audit_entries[0].payload["new_security_id"] == "571445"


def test_roll_to_next_contract_does_not_mutate_when_no_candidate_found():
    instrument = _instrument()
    csv_text = "\n".join([_CSV_HEADER, _csv_row(569003, "GOLDM", "2026-10-05")])  # only current
    dhan_client = MagicMock()
    session = MagicMock()

    result = roll_to_next_contract(session, dhan_client, instrument, csv_text)

    assert result is False
    assert instrument.security_id == "569003"
    assert instrument.contract_expiry == date(2026, 10, 5)
    dhan_client.get_quote.assert_not_called()
    session.add.assert_not_called()


def test_roll_to_next_contract_does_not_mutate_when_quote_check_raises():
    instrument = _instrument()
    csv_text = "\n".join([_CSV_HEADER, _csv_row(571445, "GOLDM", "2026-11-05")])
    dhan_client = MagicMock()
    dhan_client.get_quote.side_effect = RuntimeError("boom")
    session = MagicMock()

    result = roll_to_next_contract(session, dhan_client, instrument, csv_text)

    assert result is False
    assert instrument.security_id == "569003"
    session.add.assert_not_called()


def test_roll_to_next_contract_does_not_mutate_on_implausible_quote():
    instrument = _instrument()
    csv_text = "\n".join([_CSV_HEADER, _csv_row(571445, "GOLDM", "2026-11-05")])
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=0, open=0, high=0, low=0, close=0)
    session = MagicMock()

    result = roll_to_next_contract(session, dhan_client, instrument, csv_text)

    assert result is False
    assert instrument.security_id == "569003"
    session.add.assert_not_called()
