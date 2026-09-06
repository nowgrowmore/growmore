"""Tests for growmore_bot.wheel_basket.wheel_basket_engine.WheelBasketEngine.

Real SQLite session (Base.metadata.create_all), same pattern as
tests/unit/test_backtest_engine_persist.py -- strong enough to prove the
full position/leg lifecycle round-trips through the DB, without needing a
real Postgres. Candidate market data is handed in directly (spot/IV/RSI and
pre-filtered tradeable strikes), mirroring how the offline backtest's
run_wheel takes a pre-sliced day_chain rather than fetching live -- so these
tests exercise the DECISION logic only, not Dhan parsing (that's
live_iv_rank.py's job, tested separately against a mocked DhanClient).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.persistence.models import (
    Base,
    Strategy,
    WheelBasketConfig,
    WheelBasketLeg,
    WheelBasketPosition,
    WheelBasketSelection,
)
from growmore_bot.wheel_basket.wheel_basket_engine import CandidateData, WheelBasketEngine

CYCLE1_EXPIRY = date(2026, 1, 29)
CYCLE2_EXPIRY = date(2026, 2, 26)
CYCLE3_EXPIRY = date(2026, 3, 26)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def config(session):
    strategy = Strategy(id=uuid.uuid4(), name="wheel_basket_iv", version="1.0", params={})
    session.add(strategy)
    session.flush()
    cfg = WheelBasketConfig(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        enabled=True,
        mode="paper",
        total_virtual_capital=1_000_000.0,
        top_iv_frac=0.5,
        rotation_hysteresis_pct=0.10,
        call_basis_buffer_tiers=[[60.0, 0.05], [40.0, 0.02], [0.0, 0.0]],
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.commit()
    return cfg


def _candidate(symbol, spot, avg_iv, cycle_expiry, lot_size=100, rsi=None, macd_bullish=None,
               put_chain=(), call_chain=()):
    return CandidateData(
        symbol=symbol, spot=spot, avg_iv=avg_iv, lot_size=lot_size, rsi=rsi,
        macd_bullish=macd_bullish, put_chain=put_chain, call_chain=call_chain,
        cycle_expiry=cycle_expiry,
    )


def test_opens_a_put_on_the_highest_iv_eligible_candidate_not_already_held(session, config):
    candidates = {
        "LOWIV": _candidate("LOWIV", spot=100.0, avg_iv=0.20, cycle_expiry=CYCLE1_EXPIRY,
                             put_chain=((100.0, 2.0),)),
        "HIGHIV": _candidate("HIGHIV", spot=200.0, avg_iv=0.60, cycle_expiry=CYCLE1_EXPIRY,
                              put_chain=((200.0, 5.0),)),
    }
    engine = WheelBasketEngine(session=session)
    engine.run_cycle(config, candidates, today=date(2026, 1, 2))
    session.commit()

    positions = session.query(WheelBasketPosition).all()
    assert len(positions) == 1
    assert positions[0].symbol == "HIGHIV"
    assert positions[0].state == "short_put"

    legs = session.query(WheelBasketLeg).all()
    assert len(legs) == 1
    assert legs[0].action == "sell_put"
    assert legs[0].strike == pytest.approx(200.0)
    # Whole pool goes to the one eligible candidate: 50 lots at Rs 20,000/lot.
    assert positions[0].lots == pytest.approx(50)

    # Every candidate considered gets a selection row, selected or not.
    selections = {s.symbol: s for s in session.query(WheelBasketSelection).all()}
    assert selections["HIGHIV"].selected is True
    assert selections["LOWIV"].selected is False
    assert "not selected" in selections["LOWIV"].reason


def test_a_deep_itm_put_is_assigned_and_takes_delivery(session, config):
    candidates = {
        "HIGHIV": _candidate("HIGHIV", spot=200.0, avg_iv=0.60, cycle_expiry=CYCLE1_EXPIRY,
                              put_chain=((200.0, 5.0),)),
    }
    engine = WheelBasketEngine(session=session)
    engine.run_cycle(config, candidates, today=date(2026, 1, 2))
    session.commit()

    # Expiry day: spot has fallen well below the strike -- assigned.
    assigned_candidates = {
        "HIGHIV": _candidate("HIGHIV", spot=150.0, avg_iv=0.55, cycle_expiry=CYCLE2_EXPIRY,
                              rsi=45.0, call_chain=((150.0, 3.0), (205.0, 2.0))),
    }
    engine.run_cycle(config, assigned_candidates, today=CYCLE1_EXPIRY)
    session.commit()

    position = session.query(WheelBasketPosition).one()
    assert position.basis == pytest.approx(200.0)
    assert position.shares == pytest.approx(5000.0)  # 50 lots * lot_size 100

    put_leg = session.query(WheelBasketLeg).filter_by(action="sell_put").one()
    assert put_leg.assigned is True


def test_the_covered_call_is_struck_at_the_rsi_scaled_buffer_above_basis(session, config):
    candidates = {
        "HIGHIV": _candidate("HIGHIV", spot=200.0, avg_iv=0.60, cycle_expiry=CYCLE1_EXPIRY,
                              put_chain=((200.0, 5.0),)),
    }
    engine = WheelBasketEngine(session=session)
    engine.run_cycle(config, candidates, today=date(2026, 1, 2))
    session.commit()

    # Assigned at 200 (spot just below strike at expiry), hot RSI (75) --
    # the call should be struck at basis*1.05 = 210, not at basis (200).
    hot_candidates = {
        "HIGHIV": _candidate(
            "HIGHIV", spot=198.0, avg_iv=0.55, cycle_expiry=CYCLE2_EXPIRY, rsi=75.0,
            call_chain=((200.0, 8.0), (205.0, 6.0), (210.0, 4.0), (215.0, 2.5)),
        ),
    }
    engine.run_cycle(config, hot_candidates, today=CYCLE1_EXPIRY)
    session.commit()

    call_leg = session.query(WheelBasketLeg).filter_by(action="sell_call").one()
    assert call_leg.strike == pytest.approx(210.0)


def test_called_away_closes_the_position_and_frees_capital(session, config):
    candidates = {
        "HIGHIV": _candidate("HIGHIV", spot=200.0, avg_iv=0.60, cycle_expiry=CYCLE1_EXPIRY,
                              put_chain=((200.0, 5.0),)),
    }
    engine = WheelBasketEngine(session=session)
    engine.run_cycle(config, candidates, today=date(2026, 1, 2))
    session.commit()

    # Assigned; the call gets struck at the buffered floor (204), only 205 clears it.
    engine.run_cycle(
        config,
        {"HIGHIV": _candidate("HIGHIV", spot=150.0, avg_iv=0.55, cycle_expiry=CYCLE2_EXPIRY,
                               rsi=45.0, call_chain=((150.0, 3.0), (205.0, 2.0)))},
        today=CYCLE1_EXPIRY,
    )
    session.commit()
    call_leg = session.query(WheelBasketLeg).filter_by(action="sell_call").one()
    assert call_leg.strike == pytest.approx(205.0)

    # Recovers well above the call strike by the call's own expiry.
    engine.run_cycle(
        config,
        {"HIGHIV": _candidate("HIGHIV", spot=999.0, avg_iv=0.55, cycle_expiry=CYCLE3_EXPIRY)},
        today=CYCLE2_EXPIRY,
    )
    session.commit()

    position = session.query(WheelBasketPosition).one()
    assert position.status == "closed"
    assert position.shares == pytest.approx(0.0)
    call_leg_after = session.query(WheelBasketLeg).filter_by(action="sell_call").one()
    assert call_leg_after.called_away is True


def test_an_unassigned_put_rotates_when_a_clearly_better_candidate_appears(session, config):
    candidates = {
        "STOCKA": _candidate("STOCKA", spot=100.0, avg_iv=0.60, cycle_expiry=CYCLE1_EXPIRY,
                              put_chain=((100.0, 5.0),)),
        "STOCKB": _candidate("STOCKB", spot=50.0, avg_iv=0.10, cycle_expiry=CYCLE1_EXPIRY,
                              put_chain=((50.0, 1.0),)),
    }
    engine = WheelBasketEngine(session=session)
    engine.run_cycle(config, candidates, today=date(2026, 1, 2))
    session.commit()
    assert session.query(WheelBasketPosition).one().symbol == "STOCKA"

    # STOCKA's put expires OTM (unassigned); meanwhile STOCKB has become far
    # richer in IV -- clears the 10% hysteresis margin easily.
    next_cycle = {
        "STOCKA": _candidate("STOCKA", spot=110.0, avg_iv=0.15, cycle_expiry=CYCLE2_EXPIRY,
                              put_chain=((110.0, 1.0),)),
        "STOCKB": _candidate("STOCKB", spot=50.0, avg_iv=0.90, cycle_expiry=CYCLE2_EXPIRY,
                              put_chain=((50.0, 4.0),)),
    }
    engine.run_cycle(config, next_cycle, today=CYCLE1_EXPIRY)
    session.commit()

    positions = session.query(WheelBasketPosition).all()
    open_positions = [p for p in positions if p.status == "open"]
    assert len(open_positions) == 1
    assert open_positions[0].symbol == "STOCKB"
    # The old STOCKA position is closed, not left dangling.
    stocka = next(p for p in positions if p.symbol == "STOCKA")
    assert stocka.status == "closed"


def test_an_unassigned_put_stays_on_the_same_stock_without_a_clear_challenger(session, config):
    # Exactly 2 candidates with top_iv_frac=0.5 means only the higher-IV one
    # is ever eligible -- STOCKA stays on top in both cycles, so STOCKB is
    # never even a legal challenger, regardless of the hysteresis margin.
    candidates = {
        "STOCKA": _candidate("STOCKA", spot=100.0, avg_iv=0.60, cycle_expiry=CYCLE1_EXPIRY,
                              put_chain=((100.0, 5.0),)),
        "STOCKB": _candidate("STOCKB", spot=50.0, avg_iv=0.55, cycle_expiry=CYCLE1_EXPIRY,
                              put_chain=((50.0, 1.0),)),
    }
    engine = WheelBasketEngine(session=session)
    engine.run_cycle(config, candidates, today=date(2026, 1, 2))
    session.commit()

    next_cycle = {
        "STOCKA": _candidate("STOCKA", spot=110.0, avg_iv=0.58, cycle_expiry=CYCLE2_EXPIRY,
                              put_chain=((110.0, 2.0),)),
        "STOCKB": _candidate("STOCKB", spot=50.0, avg_iv=0.57, cycle_expiry=CYCLE2_EXPIRY,
                              put_chain=((50.0, 1.0),)),
    }
    engine.run_cycle(config, next_cycle, today=CYCLE1_EXPIRY)
    session.commit()

    open_positions = [p for p in session.query(WheelBasketPosition).all() if p.status == "open"]
    assert len(open_positions) == 1
    assert open_positions[0].symbol == "STOCKA"
