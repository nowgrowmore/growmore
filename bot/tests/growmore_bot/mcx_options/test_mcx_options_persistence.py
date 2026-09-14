"""Model-level tests for the MCX options persistence tables (mirrors
tests/growmore_bot/wheel_basket/test_scheduler_job.py's sqlite-in-memory
fixture pattern, since these models use the generic Uuid type and work
against sqlite with no real Postgres needed for defaults/FK/cascade checks).

Covers: defaults (enabled=false, mode="paper", status="open", futures_qty=0,
realized/unrealized_pnl=0), the config->position->leg and config->selection
FK relationships (including cascade delete of legs with their position),
and that `mode` never accidentally defaults to "live".
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.persistence.models import (
    Base,
    MCXOptionsConfig,
    MCXOptionsLeg,
    MCXOptionsPosition,
    MCXOptionsSelection,
    Strategy,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _strategy(session):
    strategy = Strategy(id=uuid.uuid4(), name="mcx_options", version="1.0", params={})
    session.add(strategy)
    session.flush()
    return strategy


def test_config_defaults(session):
    strategy = _strategy(session)
    cfg = MCXOptionsConfig(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="GOLDM",
        lots=1,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.commit()
    session.refresh(cfg)

    assert cfg.enabled is False
    assert cfg.mode == "paper"
    assert float(cfg.consolidating_target_delta) == 0.30
    assert float(cfg.trend_favorable_target_delta) == 0.50
    assert cfg.min_open_interest == 0
    assert float(cfg.margin_multiple_of_premium) == 3.0
    assert float(cfg.futures_roll_cost_per_lot) == 0.0


def test_mode_never_defaults_to_live(session):
    strategy = _strategy(session)
    cfg = MCXOptionsConfig(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="SILVERM",
        lots=2,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    assert cfg.mode == "paper"
    assert cfg.mode != "live"


def test_position_defaults_and_config_relationship(session):
    strategy = _strategy(session)
    cfg = MCXOptionsConfig(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="GOLDM",
        lots=1,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.flush()

    position = MCXOptionsPosition(
        id=uuid.uuid4(),
        config_id=cfg.id,
        state="flat",
        opened_at=datetime.now(timezone.utc),
    )
    session.add(position)
    session.commit()
    session.refresh(position)

    assert position.status == "open"
    assert float(position.futures_qty) == 0
    assert float(position.realized_pnl) == 0
    assert float(position.unrealized_pnl) == 0
    assert position.basis is None
    assert position.futures_contract_expiry is None
    assert position.config.id == cfg.id
    assert cfg.positions == [position]


def test_leg_relationship_and_cascade_delete(session):
    strategy = _strategy(session)
    cfg = MCXOptionsConfig(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="GOLDM",
        lots=1,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.flush()

    position = MCXOptionsPosition(
        id=uuid.uuid4(),
        config_id=cfg.id,
        state="long_futures",
        basis=60000.0,
        futures_qty=100,
        futures_contract_expiry=date(2026, 10, 28),
        opened_at=datetime.now(timezone.utc),
    )
    session.add(position)
    session.flush()

    leg = MCXOptionsLeg(
        id=uuid.uuid4(),
        position_id=position.id,
        cycle_expiry=date(2026, 9, 24),
        opt_type="PE",
        strike=59000.0,
        premium=500.0,
        lots=1,
        action="assigned",
        opened_at=datetime.now(timezone.utc),
        assigned=True,
    )
    session.add(leg)
    session.commit()
    session.refresh(position)

    assert leg.position.id == position.id
    assert position.legs == [leg]
    assert leg.assigned is True
    assert leg.called_away is False
    assert leg.pnl is None

    # Cascade: deleting the position removes its legs (mirrors
    # WheelBasketPosition's cascade="all, delete-orphan" on .legs).
    session.delete(position)
    session.commit()
    remaining = session.query(MCXOptionsLeg).filter_by(id=leg.id).all()
    assert remaining == []


def test_selection_defaults_and_config_relationship(session):
    strategy = _strategy(session)
    cfg = MCXOptionsConfig(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="GOLDM",
        lots=1,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.flush()

    selection = MCXOptionsSelection(
        id=uuid.uuid4(),
        config_id=cfg.id,
        cycle_date=date(2026, 9, 14),
        regime=None,
        reason="no regime label for the day -- skipped entry",
    )
    session.add(selection)
    session.commit()
    session.refresh(selection)

    assert selection.regime is None
    assert selection.target_delta is None
    assert selection.selected_strike is None
    assert selection.reason == "no regime label for the day -- skipped entry"
    assert selection.config.id == cfg.id
    assert cfg.selections == [selection]


def test_roll_leg_has_no_strike_or_premium(session):
    """Migration 0023 made `strike`/`premium` nullable specifically so a
    futures rollover event (opt_type="ROLL", action="roll") -- which is not
    an option leg at all -- can be recorded without fabricating either.
    """
    strategy = _strategy(session)
    cfg = MCXOptionsConfig(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="GOLDM",
        lots=1,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.flush()

    position = MCXOptionsPosition(
        id=uuid.uuid4(),
        config_id=cfg.id,
        state="long_futures",
        basis=6100.0,
        futures_qty=100,
        futures_contract_expiry=date(2026, 10, 28),
        opened_at=datetime.now(timezone.utc),
    )
    session.add(position)
    session.flush()

    roll_leg = MCXOptionsLeg(
        id=uuid.uuid4(),
        position_id=position.id,
        cycle_expiry=date(2026, 10, 28),  # new contract's expiry, not an option's
        opt_type="ROLL",
        strike=None,
        premium=None,
        lots=1,
        action="roll",
        opened_at=datetime.now(timezone.utc),
        settled_at=datetime.now(timezone.utc),
        pnl=-450.0,
    )
    session.add(roll_leg)
    session.commit()
    session.refresh(roll_leg)

    assert roll_leg.strike is None
    assert roll_leg.premium is None
    assert roll_leg.opt_type == "ROLL"
    assert roll_leg.action == "roll"
    assert float(roll_leg.pnl) == -450.0

    # An ordinary option leg still requires strike/premium to be meaningful
    # (nullable at the schema level, but every non-roll write always
    # supplies both -- see mcx_options_engine.py).
    option_leg = MCXOptionsLeg(
        id=uuid.uuid4(),
        position_id=position.id,
        cycle_expiry=date(2026, 9, 24),
        opt_type="CE",
        strike=6200.0,
        premium=45.0,
        lots=1,
        action="sell_call",
        opened_at=datetime.now(timezone.utc),
    )
    session.add(option_leg)
    session.commit()
    session.refresh(option_leg)
    assert float(option_leg.strike) == 6200.0
    assert float(option_leg.premium) == 45.0


def test_regime_values_accepted(session):
    strategy = _strategy(session)
    cfg = MCXOptionsConfig(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="GOLDM",
        lots=1,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.flush()

    for regime, delta in [
        ("consolidating", 0.30),
        ("trend_favorable", 0.50),
        ("trend_unfavorable", None),
    ]:
        session.add(
            MCXOptionsSelection(
                id=uuid.uuid4(),
                config_id=cfg.id,
                cycle_date=date(2026, 9, 14),
                regime=regime,
                target_delta=delta,
            )
        )
    session.commit()

    stored = {s.regime: s.target_delta for s in cfg.selections}
    assert set(stored.keys()) == {"consolidating", "trend_favorable", "trend_unfavorable"}
