"""Tests for growmore_bot.risk.sizing.shares_for_capital -- the cash-equity
analogue of a futures lot.

`BacktestEngine` sizes as `qty = (signal.size or 1) * lot_size`, so a share
count goes in through `lot_size`. Choosing that count from a fixed rupee
budget is what puts 210 stocks priced from Rs 15 to Rs 1,50,000 on the same
1x-leverage footing, exactly as `capital_for_run`'s "notional" mode did for
MCX contracts whose lot notionals spanned 40x.

The rounding this introduces is the "1 lot regardless of capital" bias from
docs/technical-debt.md, in its equity form: a share count is an integer, so
an expensive stock cannot spend its whole budget. That is measured
(`rounding_drag`), not assumed away.
"""
from __future__ import annotations

import pytest

from growmore_bot.risk.sizing import rounding_drag, shares_for_capital


def test_a_cheap_stock_spends_essentially_all_of_the_budget():
    # Rs 5,00,000 at Rs 15.00 -> 33,333 shares, Rs 4,99,995 deployed.
    assert shares_for_capital(15.0, 500_000.0) == 33_333
    assert rounding_drag(15.0, 33_333, 500_000.0) == pytest.approx(0.00001, abs=1e-5)


def test_a_share_priced_above_the_whole_budget_still_yields_one_share():
    # MRF trades near Rs 1,50,000. Zero shares would drop the stock from the
    # study entirely and silently bias the universe toward cheap names; one
    # share is still exactly 1x leverage, just against a bigger account.
    assert shares_for_capital(150_000.0, 100_000.0) == 1


def test_an_expensive_stock_reports_real_rounding_drag():
    # Rs 5,00,000 at Rs 1,50,000 -> 3 shares, Rs 4,50,000 deployed: 10% idle.
    shares = shares_for_capital(150_000.0, 500_000.0)
    assert shares == 3
    assert rounding_drag(150_000.0, shares, 500_000.0) == pytest.approx(0.10)


def test_an_exact_multiple_has_no_drag():
    assert shares_for_capital(100.0, 500_000.0) == 5_000
    assert rounding_drag(100.0, 5_000, 500_000.0) == pytest.approx(0.0)


def test_drag_is_never_negative_even_when_one_share_exceeds_the_budget():
    # The max(1, ...) floor means deployed capital can EXCEED the budget.
    # That is a bigger account, not negative drag, so it clamps at zero.
    assert rounding_drag(150_000.0, 1, 100_000.0) == 0.0


def test_a_non_positive_price_is_rejected_rather_than_returning_a_share_count():
    with pytest.raises(ValueError):
        shares_for_capital(0.0, 500_000.0)
    with pytest.raises(ValueError):
        shares_for_capital(100.0, 0.0)
