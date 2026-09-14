"""TDD for growmore_bot.mcx_options.mcx_options_engine.run_cycle -- the
live paper-trading state machine for the MCX Goldmini/Silvermini
options-selling strategy.

Mirrors tests/growmore_bot/wheel_basket/test_wheel_basket_engine.py's shape
(sqlite-in-memory session, hand-computed cycle scenarios) and
tests/research/mcx_options/test_engine.py's scenario coverage (put
OTM/ITM, call OTM/ITM, regime gating), but against the LIVE engine's
DB-session-taking, MCXCycleData-taking `run_cycle` rather than the offline
backtest's array-walking `run`.

The regime is computed for real inside `run_cycle` (a pure function of
`cycle_data.futures_bars`, no Dhan mock needed) via synthetic
uptrend/downtrend/flat/too-short bar series -- same technique
tests/growmore_bot/mcx_options/test_regime.py uses.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.broker.dhan_client import Bar, OptionChainRow, OptionChainSnapshot
from growmore_bot.costs import DEFAULT_COST_MODEL, leg_cost
from growmore_bot.mcx_options.live_data import MCXCycleData
from growmore_bot.mcx_options.mcx_options_engine import run_cycle
from growmore_bot.mcx_options.pricing import black76_price
from growmore_bot.persistence.models import (
    Base,
    MCXOptionsConfig,
    MCXOptionsLeg,
    MCXOptionsPosition,
    MCXOptionsSelection,
    Strategy,
)

TODAY = date(2026, 9, 14)
SIGMA = 0.25
R = 0.0


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _config(session, **overrides) -> MCXOptionsConfig:
    strategy = Strategy(id=uuid.uuid4(), name="mcx_options", version="1.0", params={})
    session.add(strategy)
    session.flush()
    defaults = dict(
        id=uuid.uuid4(), strategy_id=strategy.id, enabled=True, symbol="GOLDM", lots=1,
        consolidating_target_delta=0.30, trend_favorable_target_delta=0.50,
        min_open_interest=0, updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    cfg = MCXOptionsConfig(**defaults)
    session.add(cfg)
    session.flush()
    return cfg


def _bar(d: date, close: float, pad: float = 1.0) -> Bar:
    return Bar(
        timestamp=datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
        open=close, high=close + pad, low=close - pad, close=close, volume=100,
    )


def _bars(closes: list[float], *, pad: float = 1.0) -> tuple[Bar, ...]:
    days = [TODAY - timedelta(days=len(closes) - 1 - i) for i in range(len(closes))]
    return tuple(_bar(d, c, pad) for d, c in zip(days, closes))


def _consolidating_bars(n: int = 90) -> tuple[Bar, ...]:
    return _bars([6000.0 + (0.3 if i % 2 else -0.3) for i in range(n)], pad=0.4)


def _downtrend_bars(n: int = 300) -> tuple[Bar, ...]:
    return _bars([6000.0 * (0.9985**i) for i in range(n)])


def _too_short_bars(n: int = 5) -> tuple[Bar, ...]:
    return _bars([6000.0] * n)


def _chain_row(strike: float, opt_type: str, F: float, T: float, oi: float = 5000.0) -> OptionChainRow:
    price = black76_price(opt_type, F, strike, T, SIGMA, R)
    return OptionChainRow(strike=strike, opt_type=opt_type, ltp=price, iv=SIGMA, oi=oi, volume=10)


def _chain(F: float, T: float, *, pe_strikes=None, ce_strikes=None, oi: float = 5000.0) -> OptionChainSnapshot:
    pe_strikes = pe_strikes if pe_strikes is not None else [F - 200, F - 150, F - 100, F - 50, F]
    ce_strikes = ce_strikes if ce_strikes is not None else [F, F + 50, F + 100, F + 150, F + 200]
    rows = [_chain_row(k, "PE", F, T, oi) for k in pe_strikes] + [
        _chain_row(k, "CE", F, T, oi) for k in ce_strikes
    ]
    return OptionChainSnapshot(spot=F, rows=rows)


def _cycle_data(
    futures_bars, futures_price, chain, option_expiry, T_years, lot_size=100,
    instrument_contract_expiry=None,
) -> MCXCycleData:
    return MCXCycleData(
        futures_bars=futures_bars, futures_price=futures_price, option_chain=chain,
        option_expiry=option_expiry, T_years=T_years, lot_size=lot_size,
        instrument_contract_expiry=instrument_contract_expiry,
    )


def _open_position_with_leg(
    session, cfg, *, state, opt_type, strike, cycle_expiry, basis=None, futures_qty=0,
    futures_contract_expiry=None, realized_pnl=0,
):
    now = datetime.now(timezone.utc)
    position = MCXOptionsPosition(
        id=uuid.uuid4(), config_id=cfg.id, status="open", state=state, basis=basis,
        futures_qty=futures_qty, opened_at=now - timedelta(days=1),
        futures_contract_expiry=futures_contract_expiry, realized_pnl=realized_pnl,
    )
    session.add(position)
    session.flush()
    leg = MCXOptionsLeg(
        id=uuid.uuid4(), position_id=position.id, cycle_expiry=cycle_expiry, opt_type=opt_type,
        strike=strike, premium=50.0, lots=cfg.lots,
        action="sell_put" if opt_type == "PE" else "sell_call", opened_at=now - timedelta(days=1),
    )
    session.add(leg)
    session.flush()
    return position, leg


def test_opens_fresh_put_when_consolidating_and_no_open_position(session):
    cfg = _config(session)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    cycle_data = _cycle_data(_consolidating_bars(), F, _chain(F, T), expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    positions = session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all()
    assert len(positions) == 1
    position = positions[0]
    assert position.status == "open"
    assert position.state == "flat"

    legs = session.query(MCXOptionsLeg).filter_by(position_id=position.id).all()
    assert len(legs) == 1
    leg = legs[0]
    assert leg.opt_type == "PE"
    assert leg.action == "sell_put"
    assert leg.cycle_expiry == expiry
    assert float(leg.lots) == cfg.lots

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].regime == "consolidating"
    assert float(selections[0].target_delta) == 0.30
    assert selections[0].selected_strike == leg.strike


def test_skips_entry_when_no_regime_label(session):
    cfg = _config(session)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    cycle_data = _cycle_data(_too_short_bars(), F, _chain(F, T), expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all() == []
    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].regime is None
    assert selections[0].target_delta is None
    assert selections[0].selected_strike is None
    assert "no regime" in selections[0].reason.lower()


def test_skips_entry_when_trend_unfavorable_for_put_side(session):
    cfg = _config(session)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    bars = _downtrend_bars()
    F = float(bars[-1].close)
    cycle_data = _cycle_data(bars, F, _chain(F, T), expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all() == []
    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].regime == "trend_unfavorable"
    assert selections[0].target_delta is None


def test_put_expires_otm_closes_the_position(session):
    cfg = _config(session)
    position, leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=5900.0, cycle_expiry=TODAY,
    )
    F = 6050.0  # F >= strike -> OTM
    cycle_data = _cycle_data(_too_short_bars(), F, _chain(F, 10 / 365.25), TODAY + timedelta(days=1), 10 / 365.25)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.settled_at is not None
    assert leg.action == "put_expired_otm"
    assert leg.assigned is False
    assert position.status == "closed"
    assert position.state == "flat"
    assert position.closed_at is not None


def test_put_expires_itm_assigns_and_writes_covered_call_same_day(session):
    cfg = _config(session)
    position, leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=6100.0, cycle_expiry=TODAY,
    )
    F = 6050.0  # F < strike -> ITM, assigned
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    chain = _chain(F, T, ce_strikes=[6100, 6150, 6200, 6250, 6300])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.settled_at is not None
    assert leg.action == "assigned"
    assert leg.assigned is True
    assert position.state == "long_futures"
    assert float(position.basis) == 6100.0
    assert float(position.futures_qty) == cfg.lots * cycle_data.lot_size

    open_legs = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, settled_at=None)
        .all()
    )
    assert len(open_legs) == 1
    new_leg = open_legs[0]
    assert new_leg.opt_type == "CE"
    assert new_leg.action == "sell_call"
    assert new_leg.strike >= 6100.0  # covered call floor at basis

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].regime == "consolidating"
    assert float(selections[0].target_delta) == 0.30


def test_call_expires_otm_stays_long_futures_and_writes_new_call(session):
    cfg = _config(session)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6200.0,
        cycle_expiry=TODAY, basis=6000.0, futures_qty=100,
    )
    F = 6100.0  # F <= strike -> OTM, keep futures
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    chain = _chain(F, T, ce_strikes=[6000, 6050, 6100, 6150, 6200])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.settled_at is not None
    assert leg.action == "call_expired_otm"
    assert leg.called_away is False
    assert position.status == "open"
    assert position.state == "long_futures"
    # Mark-to-market against basis (documented simplification -- see engine
    # module docstring: no persisted "last mark" column exists to support a
    # true day-by-day incremental M2M ledger).
    assert float(position.unrealized_pnl) == pytest.approx((F - 6000.0) * 100)

    open_legs = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, settled_at=None)
        .all()
    )
    assert len(open_legs) == 1
    assert open_legs[0].opt_type == "CE"
    assert open_legs[0].action == "sell_call"


def test_call_expires_itm_closes_position_called_away(session):
    cfg = _config(session)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6100.0,
        cycle_expiry=TODAY, basis=6000.0, futures_qty=100,
    )
    F = 6200.0  # F > strike -> ITM, called away
    cycle_data = _cycle_data(_too_short_bars(), F, _chain(F, 10 / 365.25), TODAY + timedelta(days=1), 10 / 365.25)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.settled_at is not None
    assert leg.action == "called_away"
    assert leg.called_away is True
    assert position.status == "closed"
    assert position.state == "closed"
    assert position.closed_at is not None


def test_marks_to_market_when_leg_not_yet_due(session):
    cfg = _config(session)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6300.0,
        cycle_expiry=TODAY + timedelta(days=5), basis=6000.0, futures_qty=100,
    )
    F = 6300.0
    cycle_data = _cycle_data(_too_short_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.settled_at is None  # not due yet -- untouched
    assert float(position.unrealized_pnl) == pytest.approx((F - 6000.0) * 100)

    positions = session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all()
    assert len(positions) == 1  # no second position/leg created

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].regime is None  # no entry decision made this cycle


def test_no_strike_clears_oi_filter_skips_entry(session):
    cfg = _config(session, min_open_interest=100_000)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    chain = _chain(F, T, oi=100.0)
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all() == []
    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].regime == "consolidating"
    assert float(selections[0].target_delta) == 0.30
    assert selections[0].selected_strike is None
    assert "no strike" in selections[0].reason.lower()


def _expected_roll_cost(notional: float, lots: int, per_lot: float = 0.0) -> float:
    return (
        leg_cost(notional, "sell", DEFAULT_COST_MODEL)
        + leg_cost(notional, "buy", DEFAULT_COST_MODEL)
        + per_lot * lots
    )


def test_rolls_long_futures_position_when_instrument_contract_advances(session):
    """The Instrument's own `contract_expiry` has already moved on (via
    `contract_rollover.roll_to_next_contract`, run earlier in the same
    tick) past what this position was assigned/last rolled against --
    the position must roll: mark-to-market the old exposure into
    `realized_pnl`, charge the round-trip roll cost, rebase `basis` to
    today's futures price, and advance `futures_contract_expiry`. The
    covered call isn't due today, so nothing else should happen this cycle.
    """
    cfg = _config(session)
    old_expiry = date(2026, 9, 20)
    new_expiry = date(2026, 10, 20)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6300.0,
        cycle_expiry=TODAY + timedelta(days=5), basis=6000.0, futures_qty=100,
        futures_contract_expiry=old_expiry,
    )
    F = 6100.0
    cycle_data = _cycle_data(
        _too_short_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
        instrument_contract_expiry=new_expiry,
    )

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    mtm = (F - 6000.0) * 100
    roll_cost = _expected_roll_cost(F * 100, cfg.lots)

    assert float(position.basis) == F
    assert position.futures_contract_expiry == new_expiry
    assert float(position.realized_pnl) == pytest.approx(mtm - roll_cost)
    # mark-to-market after the roll (basis now equals F) -- zero, as expected
    # since the roll itself has no price effect.
    assert float(position.unrealized_pnl) == pytest.approx(0.0)

    roll_legs = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, action="roll")
        .all()
    )
    assert len(roll_legs) == 1
    roll_leg = roll_legs[0]
    assert roll_leg.opt_type == "ROLL"
    assert roll_leg.strike is None
    assert roll_leg.premium is None
    assert float(roll_leg.lots) == pytest.approx(1.0)
    assert roll_leg.cycle_expiry == new_expiry
    assert roll_leg.settled_at is not None
    assert float(roll_leg.pnl) == pytest.approx(-roll_cost)

    # The existing covered call isn't due yet -- untouched.
    assert leg.settled_at is None


def test_no_roll_when_instrument_contract_expiry_unchanged(session):
    cfg = _config(session)
    same_expiry = date(2026, 9, 20)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6300.0,
        cycle_expiry=TODAY + timedelta(days=5), basis=6000.0, futures_qty=100,
        futures_contract_expiry=same_expiry,
    )
    F = 6100.0
    cycle_data = _cycle_data(
        _too_short_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
        instrument_contract_expiry=same_expiry,
    )

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    assert float(position.basis) == 6000.0  # unchanged -- no roll happened
    assert position.futures_contract_expiry == same_expiry
    assert float(position.realized_pnl) == 0.0
    roll_legs = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, action="roll")
        .all()
    )
    assert roll_legs == []


def test_roll_and_same_day_covered_call_settlement_both_happen(session):
    """A roll happening the same cycle as the covered call's own expiry:
    the roll runs first (rebasing `basis`/`futures_contract_expiry`), then
    the normal settle-and-decide logic proceeds using the now-current
    contract -- both should be reflected.
    """
    cfg = _config(session)
    old_expiry = date(2026, 9, 20)
    new_expiry = date(2026, 10, 20)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6200.0,
        cycle_expiry=TODAY, basis=6000.0, futures_qty=100,
        futures_contract_expiry=old_expiry,
    )
    F = 6100.0  # OTM against the 6200 call -> keeps futures, writes a new call
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    chain = _chain(F, T, ce_strikes=[6100, 6150, 6200, 6250, 6300])
    cycle_data = _cycle_data(
        _consolidating_bars(), F, chain, expiry, T, instrument_contract_expiry=new_expiry,
    )

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    mtm = (F - 6000.0) * 100
    roll_cost = _expected_roll_cost(F * 100, cfg.lots)

    # Roll happened.
    assert position.futures_contract_expiry == new_expiry
    assert float(position.realized_pnl) == pytest.approx(mtm - roll_cost)
    roll_legs = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, action="roll")
        .all()
    )
    assert len(roll_legs) == 1

    # The existing covered call settled OTM against the now-current F/basis.
    assert leg.settled_at is not None
    assert leg.action == "call_expired_otm"
    assert position.status == "open"
    assert position.state == "long_futures"

    # A fresh covered call was written for the next cycle.
    open_legs = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, settled_at=None)
        .all()
    )
    assert len(open_legs) == 1
    assert open_legs[0].opt_type == "CE"
    assert open_legs[0].action == "sell_call"
    assert open_legs[0].strike >= float(position.basis)  # floored at post-roll basis
