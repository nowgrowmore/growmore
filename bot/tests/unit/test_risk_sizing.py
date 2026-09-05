"""Tests for growmore_bot.risk.sizing -- the capital/leverage arithmetic.

Why this module exists at all: the backtest trades exactly 1 lot against a
flat `default_virtual_capital` for every instrument, so a sweep across 8 MCX
commodities with wildly different lot notionals silently ran a different
amount of leverage on each one. These functions make that explicit so a
ranking can be put on an equal-risk footing.

`lot_size` throughout is QUOTE UNITS per lot (see
growmore_bot.config.CommodityPlaceholder) -- e.g. Gold Mini is a 100g lot
quoted per 10g, so lot_size=10 and one lot's notional is
price_per_10g * 10.
"""
from __future__ import annotations

import pytest

from growmore_bot.risk.sizing import leverage_at_1_lot, notional_per_lot


def test_notional_per_lot_gold_mini():
    # GOLDM quoted per 10g at Rs 15,767; lot_size=10 quote units per lot.
    assert notional_per_lot(price=15_767.0, lot_size=10) == pytest.approx(157_670.0)


def test_notional_per_lot_copper():
    # COPPER quoted per kg at Rs 1,381.35; lot_size=2500 kg per lot.
    assert notional_per_lot(price=1_381.35, lot_size=2500) == pytest.approx(3_453_375.0)


def test_leverage_at_1_lot_exposes_the_hidden_sweep_bias():
    """The whole point: at one flat capital figure, 1 lot is a totally
    different amount of risk per instrument. Copper runs ~22x the leverage
    Gold Mini does off the same account -- which is why their CAGRs were
    never comparable.
    """
    capital = 500_000.0
    goldm = leverage_at_1_lot(capital=capital, price=15_767.0, lot_size=10)
    copper = leverage_at_1_lot(capital=capital, price=1_381.35, lot_size=2500)

    assert goldm == pytest.approx(0.31534)
    assert copper == pytest.approx(6.90675)
    assert copper / goldm == pytest.approx(21.9, abs=0.1)


def test_leverage_at_1_lot_rejects_non_positive_capital():
    with pytest.raises(ValueError):
        leverage_at_1_lot(capital=0.0, price=100.0, lot_size=1)


def test_notional_per_lot_rejects_non_positive_inputs():
    with pytest.raises(ValueError):
        notional_per_lot(price=0.0, lot_size=10)
    with pytest.raises(ValueError):
        notional_per_lot(price=100.0, lot_size=0)
