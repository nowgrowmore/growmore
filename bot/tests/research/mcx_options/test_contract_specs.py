"""Tests for research.mcx_options.contract_specs -- Goldmini/Silvermini OPTION
contract specs.

These are placeholders, not real data: unlike the FUTURES specs in
growmore_bot/config.py's CommodityPlaceholder (verified 2026-09 against Dhan's
instrument master and MCX's published contract pages), nobody has looked up
the real Goldmini/Silvermini OPTION lot size, tick size, strike interval, or
expiry-offset-from-futures in this codebase yet. This test suite exists to
pin down the *shape* of the placeholder (never a silently-invented number)
rather than to check real values.
"""
from __future__ import annotations

from research.mcx_options.contract_specs import (
    DEFAULT_MCX_OPTION_SPECS,
    MCXOptionContractSpec,
)


def test_a_spec_with_no_values_supplied_is_all_unverified_placeholders():
    # Nothing has been looked up yet, so every numeric field must default to
    # None rather than to some plausible-looking guessed number -- a wrong
    # lot/tick size here would silently mis-size every position downstream.
    spec = MCXOptionContractSpec(symbol="GOLDM", name="Gold Mini Options")
    assert spec.lot_size is None
    assert spec.tick_size is None
    assert spec.strike_interval is None
    assert spec.expiry_offset_days_from_futures is None


def test_default_universe_covers_goldmini_and_silvermini_only():
    symbols = {s.symbol for s in DEFAULT_MCX_OPTION_SPECS}
    assert symbols == {"GOLDM", "SILVERM"}


def test_default_universe_entries_are_still_unverified_placeholders():
    # This is the whole point of phase 1: the module ships with the two
    # symbols named, but not a single number invented for them.
    for spec in DEFAULT_MCX_OPTION_SPECS:
        assert spec.lot_size is None
        assert spec.tick_size is None
        assert spec.strike_interval is None
        assert spec.expiry_offset_days_from_futures is None


def test_a_spec_is_immutable_like_commodityplaceholder_style_config():
    spec = MCXOptionContractSpec(symbol="GOLDM", name="Gold Mini Options")
    try:
        spec.lot_size = 10  # type: ignore[misc]
        assert False, "expected the frozen dataclass to refuse mutation"
    except AttributeError:
        pass
