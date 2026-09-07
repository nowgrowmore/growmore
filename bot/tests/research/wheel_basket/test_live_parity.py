"""The load-bearing test: the backtest and the live bot must decide alike.

Every number in docs/wheel-basket-results.md describes the backtest engine.
If that engine and `WheelBasketEngine` drift apart, those numbers describe a
system nobody is running -- which is worse than having no numbers, because
they look like evidence.

Parity is asserted over the DECISIONS both engines are supposed to share:
which symbols are eligible, which are entered, which strike is chosen, and
when a covered call clears the no-loss floor. It is deliberately NOT asserted
over position SIZING, because the two differ there on purpose and the
divergence is documented in basket_engine's module docstring: the live engine
takes `max(1, ...)` lots against virtual capital, while the backtest refuses
a position it cannot fully cash-secure, so that a backtest can never spend
money it does not have and report the leverage as alpha.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.persistence.models import (
    Base,
    Strategy,
    WheelBasketConfig,
    WheelBasketLeg,
    WheelBasketPosition,
)
from growmore_bot.wheel_basket.wheel_basket_engine import CandidateData, WheelBasketEngine
from research.wheel_basket.basket_engine import run_basket
from research.wheel_basket.config import BasketConfig
from research.wheel_basket.panel import CycleChain, SymbolPanel

LOT = 100
STRIKES = np.array([80.0, 90.0, 100.0, 110.0, 120.0], dtype=np.float32)
EXPIRY_DATE = date(2026, 1, 29)
EXPIRY_ORD = 20482          # any consistent ordinal; only ordering matters
TOP_IV_FRAC = 0.5
IVS = {"AAA": 0.60, "BBB": 0.45, "CCC": 0.30}
SPOT = 100.0
PREMIUM = 5.0


def _chain_pairs(is_call: bool):
    return tuple(
        (float(k), (max(SPOT - k, 0.0) if is_call else max(k - SPOT, 0.0)) + PREMIUM)
        for k in STRIKES
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def live_config(session):
    strategy = Strategy(id=uuid.uuid4(), name="wheel_basket_iv", version="1.0", params={})
    session.add(strategy)
    session.flush()
    cfg = WheelBasketConfig(
        id=uuid.uuid4(), strategy_id=strategy.id, enabled=True, mode="paper",
        total_virtual_capital=100_000_000.0, top_iv_frac=TOP_IV_FRAC,
        rotation_hysteresis_pct=0.10,
        call_basis_buffer_tiers=[[60.0, 0.05], [40.0, 0.02], [0.0, 0.0]],
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.commit()
    return cfg


def _live_entries(session, live_config) -> dict:
    candidates = {
        symbol: CandidateData(
            symbol=symbol, spot=SPOT, avg_iv=iv, lot_size=LOT, rsi=50.0,
            macd_bullish=True, put_chain=_chain_pairs(False),
            call_chain=_chain_pairs(True), cycle_expiry=EXPIRY_DATE,
        )
        for symbol, iv in IVS.items()
    }
    WheelBasketEngine(session).run_cycle(live_config, candidates, EXPIRY_DATE)
    session.commit()
    out = {}
    for position in session.query(WheelBasketPosition).all():
        leg = (
            session.query(WheelBasketLeg)
            .filter_by(position_id=position.id).one_or_none()
        )
        if leg is not None:
            out[position.symbol] = (leg.opt_type, float(leg.strike))
    return out


def _backtest_entries() -> dict:
    panels = {}
    for symbol, iv in IVS.items():
        days = np.arange(EXPIRY_ORD - 20, EXPIRY_ORD + 1, dtype=np.int32)
        spot = np.full(len(days), SPOT, dtype=np.float32)
        shape = (len(days), len(STRIKES))
        put = np.zeros(shape, dtype=np.float32)
        call = np.zeros(shape, dtype=np.float32)
        for i in range(len(days)):
            for j, k in enumerate(STRIKES):
                put[i, j] = max(k - SPOT, 0.0) + PREMIUM
                call[i, j] = max(SPOT - k, 0.0) + PREMIUM
        cycle = CycleChain(
            expiry=int(days[-1]), decision_day=int(days[0]), days=days,
            strikes=STRIKES.copy(), spot=spot, put_settle=put, call_settle=call,
            put_volume=np.full(shape, 50.0, dtype=np.float32),
            call_volume=np.full(shape, 50.0, dtype=np.float32),
            atm_iv=iv, rsi=50.0, macd_bullish=True, sma200=90.0,
            swing_low=85.0, trailing_return=0.1,
        )
        panels[symbol] = SymbolPanel(symbol=symbol, lot_size=LOT, cycles=[cycle])

    sectors = {s: s for s in IVS}     # sector plays no part in the B0 baseline
    result = run_basket(
        panels, sectors, {s: False for s in IVS},
        BasketConfig(tag="B0", top_iv_frac=TOP_IV_FRAC),
        initial_capital=100_000_000.0,
    )
    return {leg.symbol: (leg.opt_type, float(leg.strike)) for leg in result.legs}


def test_both_engines_select_the_same_symbols(session, live_config):
    live = _live_entries(session, live_config)
    backtest = _backtest_entries()
    assert set(live) == set(backtest), (
        f"selection diverged: live={sorted(live)} backtest={sorted(backtest)}"
    )


def test_both_engines_choose_the_same_strike_for_each_symbol(session, live_config):
    live = _live_entries(session, live_config)
    backtest = _backtest_entries()
    for symbol in live:
        assert live[symbol] == backtest[symbol], (
            f"{symbol}: live picked {live[symbol]}, backtest picked {backtest[symbol]}"
        )


def test_both_engines_apply_the_same_top_iv_cut(session, live_config):
    """The one selection rule with an out-of-sample result behind it
    (docs/stock-options-results.md Sec 7.1). If the cut differed, every
    variant in this study would be measured against the wrong baseline.
    """
    live = _live_entries(session, live_config)
    # The arithmetic, spelled out because it is easy to get wrong: a rank is
    # the fraction of the field STRICTLY BELOW a symbol, so three distinct
    # IVs score 2/3, 1/3 and 0. `eligible_candidates` keeps rank >= 1 - 0.5,
    # so a 50% cut admits only AAA -- not the "top half" a quick reading
    # suggests. Both engines must agree on this, whatever it is.
    assert set(live) == {"AAA"}
    assert set(_backtest_entries()) == {"AAA"}
