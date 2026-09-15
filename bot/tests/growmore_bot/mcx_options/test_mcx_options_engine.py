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
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from growmore_bot.broker.dhan_client import Bar, OptionChainRow, OptionChainSnapshot
from growmore_bot.costs import DEFAULT_COST_MODEL, FREE_COST_MODEL, leg_cost
from growmore_bot.mcx_options.live_data import MCXCycleData
from growmore_bot.mcx_options.mcx_options_engine import (
    MCXOptionsStateError,
    _settle_leg,
    run_cycle,
)
from growmore_bot.mcx_options.pricing import black76_delta, black76_price
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
    # A tight, executable market straddling the theoretical price -- these
    # fixtures pre-date the bid/ask executability gate in strike_selection
    # and just need to clear it, not exercise it (see
    # test_mcx_options_strike_selection.py for that).
    bid = price * 0.98 if price > 0 else 0.01
    ask = price * 1.02 + 0.01
    return OptionChainRow(
        strike=strike, opt_type=opt_type, ltp=price, iv=SIGMA, oi=oi, volume=10,
        top_bid_price=bid, top_ask_price=ask,
    )


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
    futures_contract_expiry=None, realized_pnl=0, premium=50.0,
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
        strike=strike, premium=premium, lots=cfg.lots,
        action="sell_put" if opt_type == "PE" else "sell_call", opened_at=now - timedelta(days=1),
    )
    session.add(leg)
    session.flush()
    return position, leg


def _entry_leg_amount(premium: float, lots: float, lot_size: int) -> float:
    """Reference formula for what an option leg's entry premium credit
    (net of the zero-cost `FREE_COST_MODEL`) should be -- mirrors
    `research/mcx_options/engine.py`'s `add_leg`: `credit = premium * qty`,
    `cost = leg_cost(credit, "sell", option_cost_model)`,
    `leg_amount = credit - cost`.
    """
    qty = float(lots) * lot_size
    credit = premium * qty
    cost = leg_cost(credit, "sell", FREE_COST_MODEL)
    return credit - cost


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
    # Entered path: futures_price/position snapshot, plus every evaluated
    # candidate (not just the winner), recorded alongside the winner.
    assert float(selections[0].futures_price) == F
    assert selections[0].position_state == "flat"  # freshly opened this cycle
    assert selections[0].position_basis is None
    # The expiry actually being considered this cycle -- recorded even on an
    # entered cycle, but the real point is that it's ALSO recorded on a
    # SKIPPED cycle (see test_skips_entry_when_no_regime_label below), where
    # no MCXOptionsLeg exists at all to carry it any other way.
    assert selections[0].option_expiry == expiry
    candidates = selections[0].candidates_considered
    assert candidates is not None
    # Four, not five: `_chain`'s default PE set is [F-200, F-150, F-100, F-50, F]
    # and the strike AT the futures price is not out of the money, so
    # `_cap_chain_for_short_put` drops it before any delta is computed (see
    # test_refuses_to_sell_an_in_the_money_put).
    assert len(candidates) == 4
    assert all(float(c["strike"]) < F for c in candidates)
    picked_entries = [c for c in candidates if float(c["strike"]) == float(leg.strike)]
    assert len(picked_entries) == 1
    for c in candidates:
        assert set(c.keys()) == {"strike", "delta", "oi", "ltp"}

    # The premium collected for selling this put is real cash credited
    # immediately -- it must be booked into realized_pnl right away, not
    # deferred to settlement (see module docstring's new "Option premium
    # booking" section).
    expected_credit = _entry_leg_amount(float(leg.premium), leg.lots, cycle_data.lot_size)
    assert expected_credit == pytest.approx(float(leg.premium) * cfg.lots * cycle_data.lot_size)
    assert float(position.realized_pnl) == pytest.approx(expected_credit)


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
    # No position exists yet on this config -- still get a futures price and
    # a "None" position-state snapshot, not left blank.
    assert float(selections[0].futures_price) == F
    assert selections[0].position_state is None
    assert selections[0].position_basis is None
    assert selections[0].position_unrealized_pnl is None
    assert selections[0].candidates_considered is None
    # The whole point of this field: a SKIPPED cycle writes no MCXOptionsLeg
    # at all, so without this the expiry being considered would be lost
    # entirely -- this is the only record of it.
    assert selections[0].option_expiry == expiry


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
    assert float(selections[0].futures_price) == pytest.approx(F)
    assert selections[0].position_state is None
    assert selections[0].candidates_considered is None


def test_put_expires_otm_closes_the_position(session):
    cfg = _config(session)
    entry_credit = _entry_leg_amount(50.0, cfg.lots, 100)
    position, leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=5900.0, cycle_expiry=TODAY,
        realized_pnl=entry_credit,  # already booked at entry, on a prior cycle
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

    # The premium was already credited at entry -- expiring OTM must not
    # double-count it, but the leg's own row should still show what it
    # earned (informational, for the dashboard trade-history table).
    assert float(position.realized_pnl) == pytest.approx(entry_credit)
    assert float(leg.pnl) == pytest.approx(entry_credit)


def test_put_expires_itm_assigns_and_writes_covered_call_same_day(session):
    cfg = _config(session)
    entry_credit = _entry_leg_amount(50.0, cfg.lots, 100)
    position, leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=6100.0, cycle_expiry=TODAY,
        realized_pnl=entry_credit,  # already booked at entry, on a prior cycle
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

    # The put's own entry credit was already booked, unaffected by
    # assignment (informational leg.pnl mirrors it); assignment itself adds
    # a SEPARATE, genuinely new debit -- the futures buy-in cost -- and the
    # freshly-written covered call's own entry credit is booked immediately
    # too (all hand-computed independently below).
    assert float(leg.pnl) == pytest.approx(entry_credit)
    qty = cfg.lots * cycle_data.lot_size
    assignment_cost = leg_cost(6100.0 * qty, "buy", DEFAULT_COST_MODEL)
    new_call_credit = _entry_leg_amount(float(new_leg.premium), new_leg.lots, cycle_data.lot_size)
    expected_realized_pnl = entry_credit - assignment_cost + new_call_credit
    assert float(position.realized_pnl) == pytest.approx(expected_realized_pnl)


def test_call_expires_otm_stays_long_futures_and_writes_new_call(session):
    cfg = _config(session)
    prior_realized = _entry_leg_amount(50.0, cfg.lots, 100)  # put + old call's entry credits
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6200.0,
        cycle_expiry=TODAY, basis=6000.0, futures_qty=100, realized_pnl=prior_realized,
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

    # The just-settled call's own entry credit was already booked when it
    # was written (on the prior cycle, folded into `prior_realized` here) --
    # expiring OTM must not double-book it, only record it informationally
    # on the leg row. The freshly-written call's entry credit IS a new
    # credit, on top of everything already in realized_pnl.
    assert float(leg.pnl) == pytest.approx(prior_realized)
    new_leg = open_legs[0]
    new_call_credit = _entry_leg_amount(float(new_leg.premium), new_leg.lots, cycle_data.lot_size)
    assert float(position.realized_pnl) == pytest.approx(prior_realized + new_call_credit)


def test_call_expires_itm_closes_position_called_away(session):
    cfg = _config(session)
    basis = 6000.0
    strike = 6100.0
    qty = cfg.lots * 100
    prior_realized = _entry_leg_amount(50.0, cfg.lots, 100)  # everything credited so far
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=strike,
        cycle_expiry=TODAY, basis=basis, futures_qty=100, realized_pnl=prior_realized,
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
    assert float(position.unrealized_pnl) == 0.0

    # The call's own entry credit was already booked when it was written --
    # informational on the leg row here. Being called away crystallizes a
    # SEPARATE, genuinely new gain: the futures move from basis to the call
    # strike, net of the futures sell-leg cost -- this is the gain the bug
    # discarded via `position.unrealized_pnl = 0` with nothing booked
    # anywhere else.
    assert float(leg.pnl) == pytest.approx(prior_realized)
    exit_mtm = (strike - basis) * qty
    exit_cost = leg_cost(strike * qty, "sell", DEFAULT_COST_MODEL)
    expected_realized_pnl = prior_realized + exit_mtm - exit_cost
    assert float(position.realized_pnl) == pytest.approx(expected_realized_pnl)


def test_marks_to_market_when_leg_not_yet_due(session):
    cfg = _config(session)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6300.0,
        cycle_expiry=TODAY + timedelta(days=5), basis=6000.0, futures_qty=100,
    )
    F = 6300.0
    cycle_data = _cycle_data(
        _consolidating_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
    )

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
    # No ENTRY decision was made this cycle (target_delta/selected_strike
    # stay null), but today's regime read IS still computed and recorded
    # purely for dashboard transparency -- see run_cycle's "not due yet"
    # early-return.
    assert selections[0].regime == "consolidating"
    assert selections[0].target_delta is None
    assert selections[0].selected_strike is None
    # Daily-snapshot fields ARE populated even on a hold day -- see
    # mcx_options_engine.run_cycle's "not due yet" early-return.
    assert float(selections[0].futures_price) == F
    assert selections[0].position_state == "long_futures"
    assert float(selections[0].position_basis) == 6000.0
    assert float(selections[0].position_unrealized_pnl) == pytest.approx((F - 6000.0) * 100)
    assert selections[0].candidates_considered is None


def test_marks_to_market_when_leg_not_yet_due_and_regime_undetermined(session):
    """Same hold-day path, but with too little bar history to warm up the
    regime classifier -- the daily-snapshot fields must still be populated
    even though `regime` genuinely has "no opinion" to record.
    """
    cfg = _config(session)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6300.0,
        cycle_expiry=TODAY + timedelta(days=5), basis=6000.0, futures_qty=100,
    )
    F = 6300.0
    cycle_data = _cycle_data(_too_short_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].regime is None  # genuinely no opinion -- insufficient warm-up
    assert float(selections[0].futures_price) == F
    assert selections[0].position_state == "long_futures"
    assert float(selections[0].position_basis) == 6000.0
    assert float(selections[0].position_unrealized_pnl) == pytest.approx((F - 6000.0) * 100)
    assert selections[0].candidates_considered is None


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
    # The chain WAS fetched and evaluated (every candidate failed the OI
    # floor) -- that's still valuable transparency, so this is an empty
    # list, not null.
    assert selections[0].candidates_considered == []
    assert float(selections[0].futures_price) == F
    assert selections[0].position_state is None


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
    new_leg = open_legs[0]
    assert new_leg.opt_type == "CE"
    assert new_leg.action == "sell_call"
    assert new_leg.strike >= float(position.basis)  # floored at post-roll basis

    # Composition check: realized_pnl reflects the roll's own crystallized
    # mtm/cost (`mtm - roll_cost`), the just-settled call's own entry credit
    # (already booked on the prior cycle -- folded into this fixture's
    # realized_pnl=0 default, since it never had a real entry), AND the
    # freshly-written call's entry credit on top -- all three composing
    # rather than conflicting.
    new_call_credit = _entry_leg_amount(float(new_leg.premium), new_leg.lots, cycle_data.lot_size)
    expected_realized_pnl = 0.0 + (mtm - roll_cost) + new_call_credit
    assert float(position.realized_pnl) == pytest.approx(expected_realized_pnl)
    assert float(leg.pnl) == pytest.approx(_entry_leg_amount(float(leg.premium), leg.lots, cycle_data.lot_size))


def test_full_scenario_put_sold_assigned_covered_call_called_away(session):
    """End-to-end walk through the whole state machine over three cycles --
    put sold, assigned ITM, covered call written, call called away ITM --
    running the real `run_cycle` three times in sequence and hand-computing
    the expected final `position.realized_pnl` independently of the
    implementation, using the SAME formulas `research/mcx_options/engine.py`
    uses (see module docstring's "Option premium booking" section):

        realized_pnl = put_entry_credit
                      - assignment_cost
                      + call_entry_credit
                      + (call_strike - basis) * qty
                      - call_exit_cost

    where `assignment_cost`/`call_exit_cost` are real DEFAULT_COST_MODEL
    futures-leg costs and the two `*_entry_credit` terms are zero-cost
    (FREE_COST_MODEL) option premiums -- exactly `picked.ltp * qty` here,
    since FREE_COST_MODEL charges nothing.
    """
    cfg = _config(session)
    lot_size = 100
    qty = cfg.lots * lot_size

    # --- Day 1: sell a fresh put -------------------------------------------
    day1 = TODAY
    put_expiry = day1 + timedelta(days=10)
    T1 = (put_expiry - day1).days / 365.25
    F1 = 6000.0
    cycle_data_1 = _cycle_data(_consolidating_bars(), F1, _chain(F1, T1), put_expiry, T1, lot_size=lot_size)

    run_cycle(session, cfg, cycle_data_1, day1)
    session.commit()

    position = session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).one()
    put_leg = session.query(MCXOptionsLeg).filter_by(position_id=position.id).one()
    assert put_leg.opt_type == "PE"
    put_strike = float(put_leg.strike)
    put_premium = float(put_leg.premium)
    put_entry_credit = _entry_leg_amount(put_premium, put_leg.lots, lot_size)
    assert float(position.realized_pnl) == pytest.approx(put_entry_credit)

    # --- Day 2: put expiry day, deep ITM -> assigned; covered call written -
    day2 = put_expiry
    call_expiry = day2 + timedelta(days=10)
    T2 = (call_expiry - day2).days / 365.25
    F2 = put_strike - 200.0  # comfortably ITM against the put
    chain_2 = _chain(
        F2, T2,
        ce_strikes=[put_strike, put_strike + 50, put_strike + 100, put_strike + 150, put_strike + 200],
    )
    cycle_data_2 = _cycle_data(_consolidating_bars(), F2, chain_2, call_expiry, T2, lot_size=lot_size)

    run_cycle(session, cfg, cycle_data_2, day2)
    session.commit()
    session.refresh(position)
    session.refresh(put_leg)

    assert put_leg.action == "assigned"
    assert float(position.basis) == put_strike

    call_leg = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, settled_at=None)
        .one()
    )
    assert call_leg.opt_type == "CE"
    call_strike = float(call_leg.strike)
    call_premium = float(call_leg.premium)
    call_entry_credit = _entry_leg_amount(call_premium, call_leg.lots, lot_size)
    assignment_cost = leg_cost(put_strike * qty, "buy", DEFAULT_COST_MODEL)

    expected_after_day2 = put_entry_credit - assignment_cost + call_entry_credit
    assert float(position.realized_pnl) == pytest.approx(expected_after_day2)
    assert float(put_leg.pnl) == pytest.approx(put_entry_credit)

    # --- Day 3: call expiry day, deep ITM -> called away, position closes --
    day3 = call_expiry
    F3 = call_strike + 200.0  # comfortably ITM against the call
    cycle_data_3 = _cycle_data(
        _too_short_bars(), F3, _chain(F3, 1 / 365.25), day3 + timedelta(days=10), 1 / 365.25,
        lot_size=lot_size,
    )

    run_cycle(session, cfg, cycle_data_3, day3)
    session.commit()
    session.refresh(position)
    session.refresh(call_leg)

    assert call_leg.action == "called_away"
    assert position.status == "closed"
    assert position.state == "closed"
    assert float(position.unrealized_pnl) == 0.0
    assert float(call_leg.pnl) == pytest.approx(call_entry_credit)

    exit_mtm = (call_strike - put_strike) * qty  # basis is still the put's assignment strike
    exit_cost = leg_cost(call_strike * qty, "sell", DEFAULT_COST_MODEL)
    expected_final = expected_after_day2 + exit_mtm - exit_cost

    assert float(position.realized_pnl) == pytest.approx(expected_final)

    # Fully independent recomputation from scratch, spelled out term by
    # term, so this number can be checked by hand against the report:
    #   realized_pnl = put_entry_credit - assignment_cost + call_entry_credit
    #                  + (call_strike - put_strike) * qty - exit_cost
    fully_independent = (
        (put_premium * qty)
        - leg_cost(put_strike * qty, "buy", DEFAULT_COST_MODEL)
        + (call_premium * qty)
        + (call_strike - put_strike) * qty
        - leg_cost(call_strike * qty, "sell", DEFAULT_COST_MODEL)
    )
    assert float(position.realized_pnl) == pytest.approx(fully_independent)


# ---------------------------------------------------------------------------
# Independent-review fixes (2026-09-15). Each test below pins one defect found
# by the review documented in docs/technical-debt.md's "MCX options review"
# section -- see each test's own docstring for the failure it prevents.
# ---------------------------------------------------------------------------


def _uptrend_bars(n: int = 300) -> tuple[Bar, ...]:
    return _bars([6000.0 * (1.0015**i) for i in range(n)])


def test_settles_a_leg_whose_expiry_day_was_missed(session):
    """B1: settlement used to require `cycle_expiry == today` exactly, so a
    single missed cycle (VPS restart, an MCX partial-session holiday absent
    from MCX_HOLIDAYS_2026, or a DhanApiError that exhausted the retries)
    left the leg unsettled forever -- `entry_needed` stayed False on every
    later cycle and the commodity silently stopped trading.
    """
    cfg = _config(session)
    missed_expiry = TODAY - timedelta(days=3)
    position, leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=5900.0, cycle_expiry=missed_expiry,
    )
    F = 6050.0  # OTM against the 5900 put
    cycle_data = _cycle_data(
        _too_short_bars(), F, _chain(F, 10 / 365.25), TODAY + timedelta(days=10), 10 / 365.25,
    )

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.settled_at is not None
    assert leg.action == "put_expired_otm"
    assert position.status == "closed"


def test_refuses_entry_when_the_expiry_is_today(session):
    """B2: on an expiry date `fetch_cycle_data` used to hand back today's own
    expiry, so T_years == 0 and `black76_delta` returned its 0/+-1 boundary
    for EVERY strike -- the target-delta pick then tied across the board and
    `min` returned the deepest OTM strike on the chain. The leg it wrote also
    carried `cycle_expiry == today`, which could never settle.
    """
    cfg = _config(session)
    F = 6000.0
    cycle_data = _cycle_data(_consolidating_bars(), F, _chain(F, 0.0), TODAY, 0.0)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    assert session.query(MCXOptionsLeg).all() == []
    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all() == []
    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert "expiry" in selections[0].reason.lower()
    assert selections[0].selected_strike is None


def test_marks_to_market_on_the_assignment_cycle(session):
    """B3: `_mark_to_market` only ran on the "leg live, not due" branch, so
    the cycle a put was ASSIGNED left `unrealized_pnl` at 0 even though the
    freshly-assigned futures position was already underwater by
    (F - strike) * qty.
    """
    cfg = _config(session)
    strike = 6100.0
    position, leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=strike, cycle_expiry=TODAY,
    )
    F = 5900.0  # 200 points ITM against the put
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    chain = _chain(F, T, ce_strikes=[6100, 6150, 6200, 6250, 6300])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    qty = cfg.lots * cycle_data.lot_size
    assert float(position.unrealized_pnl) == pytest.approx((F - strike) * qty)

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert float(selections[0].position_unrealized_pnl) == pytest.approx((F - strike) * qty)


def test_marks_to_market_when_entry_is_skipped_by_regime(session):
    """B3, second path: a held futures position with no open leg whose new
    covered call is skipped by an unfavorable regime must STILL be marked to
    market -- the old code returned early without marking, so both
    `unrealized_pnl` and the selection-log snapshot went stale.
    """
    cfg = _config(session)
    position = MCXOptionsPosition(
        id=uuid.uuid4(), config_id=cfg.id, status="open", state="long_futures", basis=6000.0,
        futures_qty=100, opened_at=datetime.now(timezone.utc) - timedelta(days=1),
        realized_pnl=0, unrealized_pnl=0,
    )
    session.add(position)
    session.flush()

    F = 6250.0
    # An uptrend makes the CALL side trend_unfavorable -- entry is skipped.
    cycle_data = _cycle_data(
        _uptrend_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
    )

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].regime == "trend_unfavorable"
    assert selections[0].selected_strike is None
    assert float(position.unrealized_pnl) == pytest.approx((F - 6000.0) * 100)
    assert float(selections[0].position_unrealized_pnl) == pytest.approx((F - 6000.0) * 100)


def test_zeroes_futures_qty_when_called_away(session):
    """B4: a called-away position kept its `futures_qty`, so the dashboard's
    current-position table read phantom exposure off a closed position.
    """
    cfg = _config(session)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6200.0,
        cycle_expiry=TODAY, basis=6000.0, futures_qty=100,
    )
    F = 6400.0  # ITM against the call -- called away
    cycle_data = _cycle_data(
        _too_short_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
    )

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    assert position.status == "closed"
    assert float(position.futures_qty) == 0.0


def test_covered_call_is_sized_from_the_futures_actually_held(session):
    """B5: the covered call used `config.lots`, not the futures position's
    own size. Editing `lots` on a config while a position was open silently
    turned the "covered" call into a partially NAKED short call.
    """
    cfg = _config(session, lots=1)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6300.0,
        cycle_expiry=TODAY, basis=6000.0, futures_qty=300,  # 3 lots of 100, not cfg.lots=1
    )
    F = 6050.0  # OTM against the 6300 call -- keep futures, write a new call
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    chain = _chain(F, T, ce_strikes=[6100, 6150, 6200, 6250, 6300])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    new_leg = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, settled_at=None)
        .one()
    )
    assert new_leg.opt_type == "CE"
    assert float(new_leg.lots) == 3.0  # 300 / lot_size, NOT cfg.lots


def test_refuses_to_sell_an_in_the_money_put(session):
    """B9: nothing stopped the target-delta picker choosing a put at or above
    the futures price -- a near-certain assignment dressed up as premium
    income. The covered-call side has had a basis floor since day one; the
    put side had no equivalent guard.
    """
    cfg = _config(session)
    F = 6000.0
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    # Every listed put is AT or ABOVE the futures price, i.e. ITM.
    chain = _chain(F, T, pe_strikes=[F, F + 50, F + 100], ce_strikes=[F + 200])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    assert session.query(MCXOptionsLeg).all() == []
    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].selected_strike is None


def test_rerunning_the_same_cycle_does_not_duplicate_the_selection_row(session):
    """B12: three production cycles were run by hand on 2026-09-14 (19:13,
    20:42, 23:59), each writing its own MCXOptionsSelection row for the same
    cycle_date. One cycle_date is one decision -- re-running must REPLACE
    that day's row, not stack a second one behind it.
    """
    cfg = _config(session)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    cycle_data = _cycle_data(_consolidating_bars(), F, _chain(F, T), expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    # The second run saw a live leg not due for settlement -- that's the
    # reason the surviving row must carry.
    assert "not due" in selections[0].reason.lower()
    # ...and it must NOT have opened a second leg or position.
    assert len(session.query(MCXOptionsLeg).all()) == 1
    assert len(session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all()) == 1


def test_raises_loudly_when_a_position_has_two_unsettled_legs(session):
    """B13: `.one_or_none()` raised a bare MultipleResultsFound, which the
    scheduler's blanket `except Exception` turned into a permanent daily skip
    whose only trace was a log line. The invariant violation must name itself.
    """
    cfg = _config(session)
    position, _leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=5900.0,
        cycle_expiry=TODAY + timedelta(days=5),
    )
    # Migration 0026 makes this state unreachable going forward; drop that
    # index here so the test can still reproduce a row set that predates the
    # migration (or that a second, concurrent writer could still race into).
    session.execute(text("DROP INDEX uq_mcx_options_legs_one_unsettled_per_position"))
    session.add(
        MCXOptionsLeg(
            id=uuid.uuid4(), position_id=position.id, cycle_expiry=TODAY + timedelta(days=5),
            opt_type="PE", strike=5800.0, premium=40.0, lots=cfg.lots, action="sell_put",
            opened_at=datetime.now(timezone.utc),
        )
    )
    session.flush()

    F = 6000.0
    cycle_data = _cycle_data(
        _consolidating_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
    )

    with pytest.raises(MCXOptionsStateError, match="unsettled leg"):
        run_cycle(session, cfg, cycle_data, TODAY)


def test_raises_loudly_when_a_config_has_two_open_positions(session):
    """B13: `.first()` silently picked one open position and left the other
    orphaned forever -- never settled, never marked to market.
    """
    cfg = _config(session)
    # See the note in the previous test -- 0026's partial unique index makes
    # this unreachable going forward; the engine-level guard is what catches
    # a row set that predates it.
    session.execute(text("DROP INDEX uq_mcx_options_positions_one_open_per_config"))
    for _ in range(2):
        session.add(
            MCXOptionsPosition(
                id=uuid.uuid4(), config_id=cfg.id, status="open", state="flat", futures_qty=0,
                opened_at=datetime.now(timezone.utc), realized_pnl=0, unrealized_pnl=0,
            )
        )
    session.flush()

    F = 6000.0
    cycle_data = _cycle_data(
        _consolidating_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
    )

    with pytest.raises(MCXOptionsStateError, match="open position"):
        run_cycle(session, cfg, cycle_data, TODAY)


def test_settling_a_leg_with_no_strike_raises_rather_than_asserting():
    """B14: the guards in `_settle_leg` were bare `assert`s, which `python -O`
    strips -- a malformed leg would then compare None against a float and
    blow up somewhere far less legible.

    Exercised against `_settle_leg` directly, on unflushed rows: migration
    0026's `ck_mcx_options_legs_roll_has_no_strike` CHECK now makes a
    strike-less PE leg impossible to even persist, which is the point -- but
    the in-code guard still has to hold for any row that predates it.
    """
    position = MCXOptionsPosition(
        id=uuid.uuid4(), config_id=uuid.uuid4(), status="open", state="flat",
        futures_qty=0, opened_at=datetime.now(timezone.utc), realized_pnl=0, unrealized_pnl=0,
    )
    leg = MCXOptionsLeg(
        id=uuid.uuid4(), position_id=position.id, cycle_expiry=TODAY, opt_type="PE",
        strike=None, premium=50.0, lots=1, action="sell_put",
        opened_at=datetime.now(timezone.utc),
    )
    F = 6000.0
    cycle_data = _cycle_data(
        _too_short_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
    )

    with pytest.raises(MCXOptionsStateError, match="strike"):
        _settle_leg(position, leg, cycle_data, datetime.now(timezone.utc))


def test_settling_a_leg_with_no_premium_raises_rather_than_asserting():
    """The `premium` half of the same guard -- see the test above."""
    position = MCXOptionsPosition(
        id=uuid.uuid4(), config_id=uuid.uuid4(), status="open", state="flat",
        futures_qty=0, opened_at=datetime.now(timezone.utc), realized_pnl=0, unrealized_pnl=0,
    )
    leg = MCXOptionsLeg(
        id=uuid.uuid4(), position_id=position.id, cycle_expiry=TODAY, opt_type="PE",
        strike=5900.0, premium=None, lots=1, action="sell_put",
        opened_at=datetime.now(timezone.utc),
    )
    F = 6000.0
    cycle_data = _cycle_data(
        _too_short_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
    )

    with pytest.raises(MCXOptionsStateError, match="premium"):
        _settle_leg(position, leg, cycle_data, datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Risk/selection flags (migration 0027). Each is a STRATEGY decision for the
# account owner, not a bug fix, so each is default-OFF and each gets a test
# proving both halves: that leaving it unset changes nothing, and that setting
# it does what it says. See docs/pending-actions.md.
# ---------------------------------------------------------------------------


def _long_futures_position(
    session, cfg, *, basis, futures_qty, premium=50.0, lot_size=100,
):
    """A held futures position with no OPEN leg, but with the settled short put
    that got it assigned -- the state the engine is in on a day it would write
    a fresh covered call.

    The settled leg is not decoration: `stop_loss_premium_multiple` is a
    multiple of the premium actually collected on the position, which the
    engine sums from exactly these rows.
    """
    collected = premium * float(cfg.lots) * lot_size
    position = MCXOptionsPosition(
        id=uuid.uuid4(), config_id=cfg.id, status="open", state="long_futures", basis=basis,
        futures_qty=futures_qty, opened_at=datetime.now(timezone.utc) - timedelta(days=2),
        realized_pnl=collected, unrealized_pnl=0,
    )
    session.add(position)
    session.flush()
    session.add(
        MCXOptionsLeg(
            id=uuid.uuid4(), position_id=position.id, cycle_expiry=TODAY - timedelta(days=1),
            opt_type="PE", strike=basis, premium=premium, lots=cfg.lots, action="assigned",
            opened_at=datetime.now(timezone.utc) - timedelta(days=2),
            settled_at=datetime.now(timezone.utc) - timedelta(days=1), assigned=True,
        )
    )
    session.flush()
    return position, collected


def test_stop_loss_is_off_by_default(session):
    """With `stop_loss_premium_multiple` unset the position is held no matter
    how far underwater -- the strategy's documented stop-less design.
    """
    cfg = _config(session)
    position, _collected = _long_futures_position(session, cfg, basis=6000.0, futures_qty=100)
    F = 4000.0  # catastrophically underwater: -200,000 against 5,000 collected
    cycle_data = _cycle_data(
        _consolidating_bars(), F, _chain(F, 10 / 365.25), TODAY + timedelta(days=10), 10 / 365.25,
    )

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    assert position.status == "open"
    assert position.state == "long_futures"
    assert float(position.unrealized_pnl) == pytest.approx((F - 6000.0) * 100)


def test_stop_loss_flattens_the_position_once_enabled(session):
    cfg = _config(session, stop_loss_premium_multiple=2.0)
    position, premium_collected = _long_futures_position(
        session, cfg, basis=6000.0, futures_qty=100
    )
    assert premium_collected == 5000.0  # 50.0 premium x 1 lot x lot_size 100
    F = 5800.0  # -20,000 unrealized, against 2 x 5,000 allowed
    cycle_data = _cycle_data(
        _consolidating_bars(), F, _chain(F, 10 / 365.25), TODAY + timedelta(days=10), 10 / 365.25,
    )

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    assert position.status == "closed"
    assert position.state == "closed"
    assert float(position.futures_qty) == 0.0
    assert float(position.unrealized_pnl) == 0.0
    # The loss is crystallized, net of a real futures exit cost, not discarded.
    exit_cost = leg_cost(F * 100, "sell", DEFAULT_COST_MODEL)
    assert float(position.realized_pnl) == pytest.approx(
        premium_collected + (F - 6000.0) * 100 - exit_cost
    )

    stop_leg = session.query(MCXOptionsLeg).filter_by(action="stop_loss").one()
    assert stop_leg.opt_type == "STOP"
    assert stop_leg.settled_at is not None

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert "stop" in selections[0].reason.lower()


def test_stop_loss_leaves_a_position_inside_the_threshold_alone(session):
    cfg = _config(session, stop_loss_premium_multiple=2.0)
    position, _collected = _long_futures_position(session, cfg, basis=6000.0, futures_qty=100)
    F = 5950.0  # -5,000 unrealized, well inside the 10,000 allowed

    cycle_data = _cycle_data(
        _consolidating_bars(), F, _chain(F, 10 / 365.25), TODAY + timedelta(days=10), 10 / 365.25,
    )
    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    assert position.status == "open"


def test_minimum_credit_gate_is_off_by_default_and_blocks_when_set(session):
    """No premium-richness gate exists today -- the engine sells the target
    delta whether the premium is fat or derisory.
    """
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    chain = _chain(F, T)

    unset = _config(session)
    run_cycle(session, unset, _cycle_data(_consolidating_bars(), F, chain, expiry, T), TODAY)
    session.commit()
    assert session.query(MCXOptionsLeg).all() != []

    # 10% of the strike is far above any plausible OTM put premium here.
    gated = _config(session, min_credit_pct_of_strike=0.10)
    run_cycle(session, gated, _cycle_data(_consolidating_bars(), F, chain, expiry, T), TODAY)
    session.commit()

    selections = session.query(MCXOptionsSelection).filter_by(config_id=gated.id).all()
    assert len(selections) == 1
    assert selections[0].selected_strike is None
    assert "credit" in selections[0].reason.lower()


def test_entry_premium_uses_the_bid_when_that_flag_is_set(session):
    """A seller hits the bid; `ltp` systematically overstates the credit."""
    cfg = _config(session, use_bid_for_entry_premium=True)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    cycle_data = _cycle_data(_consolidating_bars(), F, _chain(F, T), expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    leg = session.query(MCXOptionsLeg).filter_by(action="sell_put").one()
    row = next(
        r for r in cycle_data.option_chain.rows
        if r.opt_type == "PE" and r.strike == float(leg.strike)
    )
    # `_chain_row` quotes bid at 98% of theoretical price -- strictly below ltp.
    assert float(leg.premium) == pytest.approx(row.top_bid_price)
    assert float(leg.premium) < row.ltp


def test_fallback_sigma_overrides_the_module_default_per_commodity(session):
    """DEFAULT_SIGMA is hard-coded at 0.20 for both GOLDM and SILVERM; silver's
    realised vol is materially higher. Only affects strikes whose own quoted
    IV is missing or implausible.
    """
    cfg = _config(session, fallback_sigma=0.80)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    # Strip every row's IV so the fallback is what actually prices the chain.
    chain = _chain(F, T)
    chain = OptionChainSnapshot(
        spot=chain.spot,
        rows=[replace(r, iv=None) for r in chain.rows],
    )
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    run_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    leg = session.query(MCXOptionsLeg).filter_by(action="sell_put").one()
    candidates = (
        session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).one().candidates_considered
    )
    picked = next(c for c in candidates if float(c["strike"]) == float(leg.strike))
    # At sigma=0.80 the same strike carries a much larger |delta| than it
    # would at the 0.20 module default -- proving the override reached the
    # pricing call rather than being silently ignored.
    assert abs(picked["delta"]) == pytest.approx(
        abs(black76_delta("PE", F, float(leg.strike), T, 0.80, 0.0))
    )
