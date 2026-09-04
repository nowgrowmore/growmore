"""Tests for research.smallcap_momentum.fundamentals's pure extraction
logic. Regression: found running the real 5-year backtest (2026-09-04) --
yfinance's `.info` no longer populates `returnOnEquity` at all (confirmed
against several real tickers), so every stock's quality score was silently
unusable. ROE is now derived from primitives that ARE populated
(netIncomeToCommon / (bookValue * sharesOutstanding)) rather than read
directly.
"""
from __future__ import annotations

import pytest

from research.smallcap_momentum.fundamentals import _extract_from_yfinance_info


def test_derives_roe_from_net_income_and_book_equity():
    info = {
        "netIncomeToCommon": 12_620_300_288,
        "bookValue": 242.533,
        "sharesOutstanding": 407_564_504,
        "debtToEquity": 161.977,
        "earningsGrowth": 0.128,
    }
    result = _extract_from_yfinance_info(info)
    expected_roe = 12_620_300_288 / (242.533 * 407_564_504)
    assert result["roe"] == pytest.approx(expected_roe)
    assert result["debt_to_equity"] == pytest.approx(161.977)
    assert result["eps_growth"] == pytest.approx(0.128)


def test_negative_book_equity_gives_none_roe_not_a_meaningless_ratio():
    # A real case (TTML.NS, 2026-09-04): negative bookValue means negative
    # shareholders' equity -- dividing by it gives a sign-inverted, not
    # meaningful, ratio. Treat as unavailable rather than compute garbage.
    info = {
        "netIncomeToCommon": 375_300_000,
        "bookValue": -102.221,
        "sharesOutstanding": 1_954_927_727,
        "debtToEquity": 50.0,
        "earningsGrowth": 0.05,
    }
    result = _extract_from_yfinance_info(info)
    assert result["roe"] is None


def test_missing_fields_give_none_not_a_key_error():
    result = _extract_from_yfinance_info({})
    assert result == {"roe": None, "debt_to_equity": None, "eps_growth": None}
