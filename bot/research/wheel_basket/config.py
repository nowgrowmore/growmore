"""One declared hypothesis per config. Nothing here is swept.

Every field is one DECISION, so each variant differs from the previous
stage's winner by exactly one thing and its contribution stays attributable
-- the discipline `research/stock_options/wheel_engine.StrategyConfig` uses,
where H, I and L each differ from G by a single field.

The tables below are the ones declared in `docs/wheel-basket-research.md`
Sec 5 and 6, before any run. They are constants, not parameters: a swept
threshold is a hidden trial, and this study's claim to being believable rests
on its trial count being 12 rather than 432.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: R1. Only `bearish` gates. `high_vol` is deliberately NOT gated -- high IV
#: is precisely when a premium seller earns most, and gating both would bundle
#: two mechanisms into one unattributable result. Recorded as prediction 2.
REGIME_GATE_BLOCKS = frozenset({"bearish"})

#: R2. Capital stays deployed; only the strike moves.
REGIME_PUT_OTM = {
    "bull": 0.00, "consolidating": 0.00, "bearish": 0.05, "high_vol": 0.05,
}
REGIME_CALL_OTM = {
    "bull": 0.00, "consolidating": 0.00, "bearish": 0.00, "high_vol": 0.00,
}

#: R3. Strikes unchanged; the fraction of the pool deployed moves.
REGIME_DEPLOY_FRACTION = {
    "bull": 1.00, "consolidating": 0.75, "high_vol": 0.60, "bearish": 0.40,
}


@dataclass(frozen=True)
class BasketConfig:
    tag: str

    # --- Frozen at today's live values. Sec 4 of the research doc explains
    # why these are NOT swept: Sec 7.1 already spent its trials on the IV cut
    # and concluded the choice is a deployment decision, not a backtest one.
    top_iv_frac: float = 0.33
    rotation_hysteresis_pct: float = 0.10

    # --- Baseline behaviour: today's live engine.
    put_otm: float = 0.0
    call_otm: float = 0.0
    call_basis_buffer_rsi_scaled: bool = True

    # --- Stage B: sector diversification.
    sector_round_robin: bool = False
    max_per_sector: Optional[int] = None

    # --- Stage C: the three regime uses, one per variant.
    regime_gate: bool = False
    regime_strikes: bool = False
    regime_sizing: bool = False

    # --- Stage D: the three per-stock signals, one per variant.
    headwind_filter: bool = False
    support_strikes: bool = False
    relative_strength_tiebreak: bool = False

    # --- The mandatory control, not a variant.
    buy_and_hold: bool = False


#: Stages A and B. C, D and E are appended by run_basket_configs once the
#: preceding stage has a winner -- sequential, never a cross product.
BASELINE_CONFIGS = [
    BasketConfig(tag="B0-live-logic"),
    BasketConfig(tag="BH-buy-and-hold", buy_and_hold=True),
]

SECTOR_CONFIGS = [
    BasketConfig(tag="S1-round-robin", sector_round_robin=True),
    BasketConfig(tag="S2-cap-1-per-sector", sector_round_robin=True, max_per_sector=1),
    BasketConfig(tag="S3-cap-3-per-sector", sector_round_robin=True, max_per_sector=3),
]


def regime_configs(base: BasketConfig) -> list:
    """Stage C: each differs from stage B's winner by one regime use."""
    from dataclasses import replace
    return [
        replace(base, tag="R1-regime-gate", regime_gate=True),
        replace(base, tag="R2-regime-strikes", regime_strikes=True),
        replace(base, tag="R3-regime-sizing", regime_sizing=True),
    ]


def per_stock_configs(base: BasketConfig) -> list:
    """Stage D: each differs from stage B's winner by one stock signal."""
    from dataclasses import replace
    return [
        replace(base, tag="T1-headwind-filter", headwind_filter=True),
        replace(base, tag="T2-support-strikes", support_strikes=True),
        replace(base, tag="T3-relative-strength", relative_strength_tiebreak=True),
    ]


__all__ = [
    "BasketConfig", "BASELINE_CONFIGS", "SECTOR_CONFIGS",
    "regime_configs", "per_stock_configs",
    "REGIME_GATE_BLOCKS", "REGIME_PUT_OTM", "REGIME_CALL_OTM",
    "REGIME_DEPLOY_FRACTION",
]
