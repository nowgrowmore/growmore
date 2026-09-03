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

from datetime import date, timedelta

from growmore_bot.scheduler.contract_rollover import (
    close_out_cutoff_date,
    is_past_close_out_cutoff,
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
