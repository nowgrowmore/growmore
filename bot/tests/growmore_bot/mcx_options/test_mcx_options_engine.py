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
    entry_cycle,
    settle_cycle,
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
    instrument_contract_expiry=None, chains_by_expiry=None, entry_expiries=None,
) -> MCXCycleData:
    """`chains_by_expiry`/`entry_expiries` default to the single primary chain,
    so every pre-ladder test reads as it always did: one expiry, openable.
    A ladder test passes several explicitly.
    """
    chains = chains_by_expiry if chains_by_expiry is not None else {option_expiry: chain}
    return MCXCycleData(
        futures_bars=futures_bars, futures_price=futures_price, option_chain=chain,
        option_expiry=option_expiry, T_years=T_years, lot_size=lot_size,
        instrument_contract_expiry=instrument_contract_expiry,
        chains_by_expiry=chains,
        entry_expiries=(
            entry_expiries if entry_expiries is not None else sorted(chains)
        ),
    )


def _open_position_with_leg(
    session, cfg, *, state, opt_type, strike, cycle_expiry, basis=None, futures_qty=0,
    futures_contract_expiry=None, realized_pnl=0, premium=50.0,
):
    now = datetime.now(timezone.utc)
    position = MCXOptionsPosition(
        id=uuid.uuid4(), config_id=cfg.id, status="open", state=state, basis=basis,
        futures_qty=futures_qty, opened_at=now - timedelta(days=1),
        entry_week_start=TODAY - timedelta(days=TODAY.weekday() + 7),
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
    cfg = _config(session, weekly_new_puts_target=1)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    cycle_data = _cycle_data(_consolidating_bars(), F, _chain(F, T), expiry, T)

    entry_cycle(session, cfg, cycle_data, TODAY)
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
        # `expiry` joined the candidate record when entry became a LADDER
        # across several expiries -- a candidate is only interpretable
        # alongside the board it came from.
        assert set(c.keys()) == {"strike", "delta", "oi", "ltp", "expiry"}

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

    entry_cycle(session, cfg, cycle_data, TODAY)
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

    entry_cycle(session, cfg, cycle_data, TODAY)
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

    settle_cycle(session, cfg, cycle_data, TODAY)
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


def test_put_expires_itm_assigns_in_the_evening_and_is_covered_next_morning(session):
    """The two-phase split in practice. Assignment is an ITM/OTM call the
    exchange makes against the day's SETTLEMENT price, so it happens in the
    evening cycle. The covered call written against the resulting futures
    position is an ORDER, so it waits for the next morning -- which is the
    first moment it could actually be placed.
    """
    cfg = _config(session)
    entry_credit = _entry_leg_amount(50.0, cfg.lots, 100)
    position, leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=6100.0, cycle_expiry=TODAY,
        realized_pnl=entry_credit,
    )
    F = 6050.0  # F < strike -> ITM, assigned
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    chain = _chain(F, T, ce_strikes=[6100, 6150, 6200, 6250, 6300])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    # --- evening: assigned, and nothing opened -------------------------
    settle_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.action == "assigned"
    assert position.state == "long_futures"
    assert float(position.basis) == 6100.0
    assert float(position.futures_qty) == cfg.lots * cycle_data.lot_size
    assert (
        session.query(MCXOptionsLeg).filter_by(position_id=position.id, settled_at=None).all()
        == []
    ), "the evening phase must not open anything"

    # --- next morning: the covered call ---------------------------------
    entry_cycle(session, cfg, cycle_data, TODAY + timedelta(days=1))
    session.commit()

    new_leg = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, settled_at=None)
        .one()
    )
    assert new_leg.opt_type == "CE"
    assert new_leg.action == "sell_call"
    assert float(new_leg.strike) >= 6100.0  # covered-call floor at basis


def test_call_expires_otm_stays_long_futures_and_is_re_covered_next_morning(session):
    cfg = _config(session)
    prior_realized = 1234.0
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6200.0,
        cycle_expiry=TODAY, basis=6000.0, futures_qty=100, realized_pnl=prior_realized,
    )
    F = 6100.0  # below the 6200 call -> expires OTM, keep the futures
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    chain = _chain(F, T, ce_strikes=[6150, 6200, 6250, 6300])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    settle_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.action == "call_expired_otm"
    assert position.state == "long_futures"
    assert position.status == "open"
    assert float(position.unrealized_pnl) == pytest.approx((F - 6000.0) * 100)

    entry_cycle(session, cfg, cycle_data, TODAY + timedelta(days=1))
    session.commit()

    new_leg = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, settled_at=None)
        .one()
    )
    assert new_leg.opt_type == "CE"
    assert float(new_leg.strike) >= 6000.0


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

    settle_cycle(session, cfg, cycle_data, TODAY)
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

    settle_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert leg.settled_at is None  # not due yet -- untouched
    assert float(position.unrealized_pnl) == pytest.approx((F - 6000.0) * 100)

    positions = session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all()
    assert len(positions) == 1  # no second position/leg created

    # A quiet position writes NO selection row at all now. The old
    # "position already has an open leg, not due for settlement today"
    # heartbeat was pure noise -- one row per config per day forever,
    # describing nothing that happened.
    assert session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all() == []


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

    settle_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    # Same as above: nothing happened, so nothing is logged.
    assert session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all() == []


def test_no_strike_clears_oi_filter_skips_entry(session):
    cfg = _config(session, weekly_new_puts_target=1, min_open_interest=100_000)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    chain = _chain(F, T, oi=100.0)
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    entry_cycle(session, cfg, cycle_data, TODAY)
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

    entry_cycle(session, cfg, cycle_data, TODAY)
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

    entry_cycle(session, cfg, cycle_data, TODAY)
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


def test_a_roll_and_a_due_settlement_both_happen_in_one_evening_cycle(session):
    """A futures contract rolling out from under a position must not stop that
    position's own leg settling the same evening -- the roll is applied first,
    then the settle runs against the now-current contract.
    """
    cfg = _config(session)
    old_expiry = date(2026, 9, 30)
    new_expiry = date(2026, 10, 31)
    position, leg = _open_position_with_leg(
        session, cfg, state="long_futures", opt_type="CE", strike=6300.0,
        cycle_expiry=TODAY, basis=6000.0, futures_qty=100,
        futures_contract_expiry=old_expiry,
    )
    F = 6100.0  # OTM against the 6300 call
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    cycle_data = _cycle_data(
        _consolidating_bars(), F, _chain(F, T), expiry, T,
        instrument_contract_expiry=new_expiry,
    )

    settle_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)
    session.refresh(leg)

    assert position.futures_contract_expiry == new_expiry
    assert leg.action == "call_expired_otm"
    roll_leg = session.query(MCXOptionsLeg).filter_by(opt_type="ROLL").one()
    assert roll_leg.cycle_expiry == new_expiry

    # Both events are described on the one settlement row for this position.
    selection = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).one()
    assert "roll" in selection.reason.lower()
    assert "call_expired_otm" in selection.reason


def test_full_scenario_put_sold_assigned_covered_call_called_away(session):
    """End-to-end through the whole state machine, now across the two-phase
    schedule: mornings open, evenings settle. Final realized P&L is
    hand-computed independently of the implementation, using the same
    formulas `research/mcx_options/engine.py` uses::

        realized_pnl = put_entry_credit
                      - assignment_cost
                      + call_entry_credit
                      + (call_strike - basis) * qty
                      - call_exit_cost
    """
    cfg = _config(session, weekly_new_puts_target=1)
    lot_size = 100
    qty = cfg.lots * lot_size

    # --- Day 1 morning: sell a fresh put ---------------------------------
    day1 = TODAY
    put_expiry = day1 + timedelta(days=10)
    T1 = (put_expiry - day1).days / 365.25
    F1 = 6000.0
    cycle_1 = _cycle_data(
        _consolidating_bars(), F1, _chain(F1, T1), put_expiry, T1, lot_size=lot_size
    )

    entry_cycle(session, cfg, cycle_1, day1)
    session.commit()

    position = session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).one()
    put_leg = session.query(MCXOptionsLeg).filter_by(position_id=position.id).one()
    assert put_leg.opt_type == "PE"
    put_strike = float(put_leg.strike)
    put_premium = float(put_leg.premium)
    put_entry_credit = _entry_leg_amount(put_premium, put_leg.lots, lot_size)
    assert float(position.realized_pnl) == pytest.approx(put_entry_credit)

    # --- Day 2 evening: put expiry day, deep ITM -> assigned -------------
    day2 = put_expiry
    call_expiry = day2 + timedelta(days=10)
    T2 = (call_expiry - day2).days / 365.25
    F2 = put_strike - 200.0
    chain_2 = _chain(
        F2, T2,
        ce_strikes=[put_strike, put_strike + 50, put_strike + 100, put_strike + 150],
    )
    cycle_2 = _cycle_data(_consolidating_bars(), F2, chain_2, call_expiry, T2, lot_size=lot_size)

    settle_cycle(session, cfg, cycle_2, day2)
    session.commit()
    session.refresh(position)
    session.refresh(put_leg)

    assert put_leg.action == "assigned"
    assert float(position.basis) == put_strike
    assignment_cost = leg_cost(put_strike * qty, "buy", DEFAULT_COST_MODEL)
    assert float(position.realized_pnl) == pytest.approx(put_entry_credit - assignment_cost)

    # --- Day 3 morning: the covered call ---------------------------------
    day3 = day2 + timedelta(days=1)
    entry_cycle(session, cfg, cycle_2, day3)
    session.commit()
    session.refresh(position)

    call_leg = (
        session.query(MCXOptionsLeg)
        .filter_by(position_id=position.id, settled_at=None)
        .one()
    )
    assert call_leg.opt_type == "CE"
    call_strike = float(call_leg.strike)
    call_premium = float(call_leg.premium)
    call_entry_credit = _entry_leg_amount(call_premium, call_leg.lots, lot_size)
    expected_after_call = put_entry_credit - assignment_cost + call_entry_credit
    assert float(position.realized_pnl) == pytest.approx(expected_after_call)

    # --- Day 4 evening: call expiry day, deep ITM -> called away ---------
    day4 = call_expiry
    F3 = call_strike + 200.0
    cycle_3 = _cycle_data(
        _too_short_bars(), F3, _chain(F3, 1 / 365.25), day4 + timedelta(days=10),
        1 / 365.25, lot_size=lot_size,
    )

    settle_cycle(session, cfg, cycle_3, day4)
    session.commit()
    session.refresh(position)
    session.refresh(call_leg)

    assert call_leg.action == "called_away"
    assert position.status == "closed"
    assert float(position.unrealized_pnl) == 0.0
    assert float(position.futures_qty) == 0.0

    # Fully independent recomputation, term by term.
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

    settle_cycle(session, cfg, cycle_data, TODAY)
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
    cfg = _config(session, weekly_new_puts_target=1)
    F = 6000.0
    cycle_data = _cycle_data(_consolidating_bars(), F, _chain(F, 0.0), TODAY, 0.0)

    entry_cycle(session, cfg, cycle_data, TODAY)
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

    settle_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    qty = cfg.lots * cycle_data.lot_size
    assert float(position.unrealized_pnl) == pytest.approx((F - strike) * qty)

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert float(selections[0].position_unrealized_pnl) == pytest.approx((F - strike) * qty)


def test_marks_to_market_even_when_the_covered_call_is_blocked_by_regime(session):
    """B3, second path: a held futures position whose new covered call is
    skipped by an unfavourable regime must STILL be marked to market -- the
    old code returned early without marking, so both `unrealized_pnl` and the
    selection-log snapshot went stale.

    Note a single regime read can never block BOTH sides: an uptrend makes the
    CALL side unfavourable and the PUT side favourable, and vice versa. So
    this cycle legitimately skips the covered call while still selling the
    week's new puts, and the assertions below say exactly that.
    """
    cfg = _config(session)
    position = MCXOptionsPosition(
        id=uuid.uuid4(), config_id=cfg.id, status="open", state="long_futures", basis=6000.0,
        futures_qty=100, opened_at=datetime.now(timezone.utc) - timedelta(days=1),
        # Carried in from LAST week -- an assigned position does not consume
        # this week's new-put quota.
        entry_week_start=TODAY - timedelta(days=TODAY.weekday() + 7),
        realized_pnl=0, unrealized_pnl=0,
    )
    session.add(position)
    session.flush()

    F = 6250.0
    cycle_data = _cycle_data(
        _uptrend_bars(), F, _chain(F, 5 / 365.25), TODAY + timedelta(days=5), 5 / 365.25,
    )

    entry_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    # The mark happened despite this position writing no new leg.
    assert float(position.unrealized_pnl) == pytest.approx((F - 6000.0) * 100)

    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    covered_call_row = next(r for r in selections if r.position_id == position.id)
    assert covered_call_row.regime == "trend_unfavorable"
    assert covered_call_row.selected_strike is None
    assert float(covered_call_row.position_unrealized_pnl) == pytest.approx((F - 6000.0) * 100)

    # ...while the put side of the same uptrend is favourable, so the week's
    # new puts were still sold.
    put_rows = [r for r in selections if r.position_id != position.id]
    assert len(put_rows) == cfg.weekly_new_puts_target
    assert all(r.regime == "trend_favorable" for r in put_rows)


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

    settle_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    session.refresh(position)

    assert position.status == "closed"
    assert float(position.futures_qty) == 0.0


def test_covered_call_is_sized_from_the_futures_actually_held(session):
    """B5: the covered call used `config.lots`, not the futures position's own
    size. Editing `lots` while a position is open silently turned the
    "covered" call into a partially NAKED short call.
    """
    cfg = _config(session, lots=1)
    position = MCXOptionsPosition(
        id=uuid.uuid4(), config_id=cfg.id, status="open", state="long_futures", basis=6000.0,
        futures_qty=300, opened_at=datetime.now(timezone.utc) - timedelta(days=1),
        entry_week_start=TODAY - timedelta(days=TODAY.weekday() + 7),
        realized_pnl=0, unrealized_pnl=0,
    )
    session.add(position)
    session.flush()

    F = 6050.0
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    chain = _chain(F, T, ce_strikes=[6100, 6150, 6200, 6250, 6300])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    entry_cycle(session, cfg, cycle_data, TODAY)
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
    cfg = _config(session, weekly_new_puts_target=1)
    F = 6000.0
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    # Every listed put is AT or ABOVE the futures price, i.e. ITM.
    chain = _chain(F, T, pe_strikes=[F, F + 50, F + 100], ce_strikes=[F + 200])
    cycle_data = _cycle_data(_consolidating_bars(), F, chain, expiry, T)

    entry_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    assert session.query(MCXOptionsLeg).all() == []
    selections = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(selections) == 1
    assert selections[0].selected_strike is None


def test_rerunning_a_morning_cycle_neither_double_sells_nor_duplicates_rows(session):
    """Three production cycles were run by hand on 2026-09-14. With a weekly
    target, a re-run must see the week's quota already filled and do nothing
    at all -- no extra puts, and no second set of selection rows.
    """
    cfg = _config(session, weekly_new_puts_target=2)
    expiry = TODAY + timedelta(days=15)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    cycle_data = _cycle_data(_consolidating_bars(), F, _chain(F, T), expiry, T)

    entry_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    first_positions = session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).count()
    first_rows = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).count()
    assert first_positions == 2

    entry_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).count() == first_positions
    assert session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).count() == first_rows


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
        settle_cycle(session, cfg, cycle_data, TODAY)


def test_settles_several_concurrent_positions_independently(session):
    """Migration 0028 removed 0026's "one open position per config" index --
    a laddered book is now the point. Each position settles on its own leg,
    and one position's outcome must not touch another's.
    """
    cfg = _config(session)
    otm, _otm_leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=5900.0, cycle_expiry=TODAY,
    )
    itm, _itm_leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=6100.0, cycle_expiry=TODAY,
    )
    not_due, not_due_leg = _open_position_with_leg(
        session, cfg, state="flat", opt_type="PE", strike=5800.0,
        cycle_expiry=TODAY + timedelta(days=7),
    )

    F = 6000.0  # above 5900 (OTM), below 6100 (ITM)
    cycle_data = _cycle_data(
        _too_short_bars(), F, _chain(F, 7 / 365.25), TODAY + timedelta(days=7), 7 / 365.25
    )

    settle_cycle(session, cfg, cycle_data, TODAY)
    session.commit()
    for position in (otm, itm, not_due):
        session.refresh(position)
    session.refresh(not_due_leg)

    assert otm.status == "closed" and otm.state == "flat"
    assert itm.status == "open" and itm.state == "long_futures"
    assert float(itm.basis) == 6100.0
    assert not_due.status == "open"
    assert not_due_leg.settled_at is None, "a leg not yet due must be untouched"

    # One settlement row per position that actually did something -- the
    # untouched third position contributes nothing.
    rows = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(rows) == 2
    assert all(r.attempt_seq < 0 for r in rows), "settlement rows are negatively sequenced"


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

    settle_cycle(session, cfg, cycle_data, TODAY)
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

    settle_cycle(session, cfg, cycle_data, TODAY)
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
    settle_cycle(session, cfg, cycle_data, TODAY)
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
    entry_cycle(session, unset, _cycle_data(_consolidating_bars(), F, chain, expiry, T), TODAY)
    session.commit()
    assert session.query(MCXOptionsLeg).all() != []

    # 10% of the strike is far above any plausible OTM put premium here.
    gated = _config(session, min_credit_pct_of_strike=0.10)
    entry_cycle(session, gated, _cycle_data(_consolidating_bars(), F, chain, expiry, T), TODAY)
    session.commit()

    selections = session.query(MCXOptionsSelection).filter_by(config_id=gated.id).all()
    assert selections
    assert all(s.selected_strike is None for s in selections)
    assert any("credit" in s.reason.lower() for s in selections)


def test_entry_premium_uses_the_bid_when_that_flag_is_set(session):
    """A seller hits the bid; `ltp` systematically overstates the credit."""
    cfg = _config(session, weekly_new_puts_target=1, use_bid_for_entry_premium=True)
    expiry = TODAY + timedelta(days=10)
    T = (expiry - TODAY).days / 365.25
    F = 6000.0
    cycle_data = _cycle_data(_consolidating_bars(), F, _chain(F, T), expiry, T)

    entry_cycle(session, cfg, cycle_data, TODAY)
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
    cfg = _config(session, weekly_new_puts_target=1, fallback_sigma=0.80)
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

    entry_cycle(session, cfg, cycle_data, TODAY)
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


# ---------------------------------------------------------------------------
# The weekly put ladder (migration 0028). 2 NEW puts per instrument per week,
# on top of whatever is open, retried every trading morning until met.
# ---------------------------------------------------------------------------


def _ladder_cycle_data(F=6000.0, near_days=15, far_days=45, lot_size=100):
    near = TODAY + timedelta(days=near_days)
    far = TODAY + timedelta(days=far_days)
    # A deliberately DENSE board: these tests are about the ladder's own
    # caps and filters, so the fixture must never be what runs out first.
    chains = {
        near: _chain(F, near_days / 365.25,
                     pe_strikes=[F - 100 * i for i in range(1, 13)]),
        far: _chain(F, far_days / 365.25,
                    pe_strikes=[F - 150 * i for i in range(1, 13)]),
    }
    return _cycle_data(
        _consolidating_bars(), F, chains[near], near, near_days / 365.25,
        lot_size=lot_size, chains_by_expiry=chains, entry_expiries=[near, far],
    )


def test_sells_the_weekly_target_of_two_new_puts(session):
    cfg = _config(session, weekly_new_puts_target=2)

    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY)
    session.commit()

    positions = session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all()
    assert len(positions) == 2
    legs = session.query(MCXOptionsLeg).all()
    assert len(legs) == 2
    assert all(leg.opt_type == "PE" and leg.action == "sell_put" for leg in legs)
    # Two DIFFERENT contracts -- a ladder, not one position written twice.
    assert len({(leg.cycle_expiry, float(leg.strike)) for leg in legs}) == 2
    # Both tagged with the week they belong to, which is what the target counts.
    week_start = TODAY - timedelta(days=TODAY.weekday())
    assert all(p.entry_week_start == week_start for p in positions)


def test_does_nothing_at_all_once_the_weekly_target_is_met(session):
    """The core of the log-noise fix: a morning with nothing due writes no
    selection row whatsoever.
    """
    cfg = _config(session, weekly_new_puts_target=2)
    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY)
    session.commit()
    rows_after_round = session.query(MCXOptionsSelection).count()

    # Wednesday of the same week.
    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY + timedelta(days=2))
    session.commit()

    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).count() == 2
    assert session.query(MCXOptionsSelection).count() == rows_after_round


def test_a_later_morning_retries_the_shortfall_the_regime_blocked(session):
    """Monday's round is blocked by a downtrend; Tuesday's is not, and picks
    up the whole week's quota -- with no separate "is today the first trading
    day" branch anywhere, because the weekly COUNT is what gates entry.
    """
    cfg = _config(session, weekly_new_puts_target=2)

    blocked = _ladder_cycle_data()
    blocked = replace(blocked, futures_bars=_downtrend_bars())
    entry_cycle(session, cfg, blocked, TODAY)
    session.commit()
    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).count() == 0
    blocked_rows = session.query(MCXOptionsSelection).all()
    assert len(blocked_rows) == 1
    assert "trend_unfavorable" in blocked_rows[0].reason

    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY + timedelta(days=1))
    session.commit()

    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).count() == 2
    retry_rows = [
        r for r in session.query(MCXOptionsSelection).all()
        if r.cycle_date == TODAY + timedelta(days=1)
    ]
    assert retry_rows and all("retry" in r.reason for r in retry_rows)


def test_a_new_week_reopens_the_quota_on_top_of_what_is_already_open(session):
    """"2 new every week, on top of whatever is open" -- last week's puts are
    still live and do not reduce this week's allowance.
    """
    cfg = _config(session, weekly_new_puts_target=2)
    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY)
    session.commit()

    next_monday = TODAY + timedelta(days=7)
    entry_cycle(session, cfg, _ladder_cycle_data(), next_monday)
    session.commit()

    positions = session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).all()
    assert len(positions) == 4
    assert sum(1 for p in positions if p.entry_week_start == next_monday - timedelta(days=next_monday.weekday())) == 2


def test_max_concurrent_positions_caps_the_accumulation(session):
    """The hard ceiling on "2 more every week, forever". Without it a month of
    rounds builds 6-8 concurrent short puts per instrument, most expiring on
    the same date -- roughly a crore and a half of commodity assigning on one
    morning at production prices.
    """
    cfg = _config(session, weekly_new_puts_target=2, max_concurrent_positions=3)

    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY)
    session.commit()
    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY + timedelta(days=7))
    session.commit()

    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id, status="open").count() == 3


def test_max_positions_per_expiry_pushes_the_second_put_to_another_expiry(session):
    cfg = _config(session, weekly_new_puts_target=2, max_positions_per_expiry=1)

    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY)
    session.commit()

    legs = session.query(MCXOptionsLeg).all()
    assert len(legs) == 2
    assert len({leg.cycle_expiry for leg in legs}) == 2


def test_strike_separation_applies_across_weeks_not_just_within_a_batch(session):
    """The half that matters once puts accumulate: a new put landing on top of
    one sold two weeks ago is concentration, not a ladder.
    """
    cfg = _config(
        session, weekly_new_puts_target=1, min_strike_separation_pct=0.05,  # 5% of 6000 = 300
    )
    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY)
    session.commit()
    first = session.query(MCXOptionsLeg).one()

    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY + timedelta(days=7))
    session.commit()

    legs = session.query(MCXOptionsLeg).all()
    assert len(legs) == 2
    second = next(leg for leg in legs if leg.id != first.id)
    assert abs(float(second.strike) - float(first.strike)) >= 300


def test_records_one_selection_row_per_attempt_all_on_the_same_cycle_date(session):
    cfg = _config(session, weekly_new_puts_target=2)

    entry_cycle(session, cfg, _ladder_cycle_data(), TODAY)
    session.commit()

    rows = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(rows) == 2
    assert {r.cycle_date for r in rows} == {TODAY}
    # Distinct, positive attempt numbers -- entry rows; settlement rows are
    # negatively sequenced so the two phases never collide on the same date.
    assert sorted(r.attempt_seq for r in rows) == [1, 2]
    # Each row is attributable to the position it produced.
    assert all(r.position_id is not None for r in rows)


def test_reports_the_shortfall_when_fewer_puts_qualify_than_the_target(session):
    """A half-filled week is a correct outcome, and the log has to say why --
    never quietly relax a filter to hit the number.
    """
    cfg = _config(session, weekly_new_puts_target=2)
    near = TODAY + timedelta(days=15)
    chains = {near: _chain(6000.0, 15 / 365.25, pe_strikes=[5900.0])}
    cycle_data = _cycle_data(
        _consolidating_bars(), 6000.0, chains[near], near, 15 / 365.25,
        chains_by_expiry=chains, entry_expiries=[near],
    )

    entry_cycle(session, cfg, cycle_data, TODAY)
    session.commit()

    assert session.query(MCXOptionsPosition).filter_by(config_id=cfg.id).count() == 1
    rows = session.query(MCXOptionsSelection).filter_by(config_id=cfg.id).all()
    assert len(rows) == 2
    assert any("no strike qualified" in r.reason for r in rows)


def test_a_month_of_weekly_rounds_accumulates_a_bounded_laddered_book(session):
    """The arithmetic that motivated the caps, run for real.

    "2 new puts every week, on top of whatever is open" compounds: monthly
    expiries are ~4 weeks apart, so a month of rounds would otherwise build
    6-8 concurrent short puts per instrument, most of them expiring on the
    SAME date. At the futures prices recorded in production on 2026-09-14
    that is roughly 1.6 crore of commodity assigning on one morning.

    This walks four weekly rounds plus the daily retries between them and
    asserts the book stays inside every brake -- and, just as importantly,
    that the brakes are what bound it rather than a shortage of candidates.
    """
    cfg = _config(
        session,
        weekly_new_puts_target=2,
        max_concurrent_positions=6,
        max_positions_per_expiry=3,
        min_strike_separation_pct=0.005,
    )
    week_starts = []

    for week in range(4):
        monday = TODAY + timedelta(days=7 * week)
        week_starts.append(monday)
        for weekday in range(5):  # Mon-Fri: the round plus its daily retries
            day = monday + timedelta(days=weekday)
            entry_cycle(session, cfg, _ladder_cycle_data(), day)
            session.commit()

    open_positions = (
        session.query(MCXOptionsPosition).filter_by(config_id=cfg.id, status="open").all()
    )

    # 1. The concurrency ceiling held.
    assert len(open_positions) <= 6

    # 2. No expiry is carrying more than its share -- the brake that actually
    #    addresses "everything assigns on the same morning".
    by_expiry: dict = {}
    for position in open_positions:
        leg = (
            session.query(MCXOptionsLeg)
            .filter_by(position_id=position.id, settled_at=None)
            .one()
        )
        by_expiry[leg.cycle_expiry] = by_expiry.get(leg.cycle_expiry, 0) + 1
    assert by_expiry, "the month should have opened something"
    assert max(by_expiry.values()) <= 3

    # 3. The ceiling is what bound it, not a lack of candidates: the first
    #    three weeks alone would have wanted 6 puts.
    assert len(open_positions) == 6

    # 4. Each week's quota was respected exactly -- never more than 2 opened
    #    in any one week, however many retry mornings ran.
    for monday in week_starts:
        opened = (
            session.query(MCXOptionsPosition)
            .filter_by(config_id=cfg.id, entry_week_start=monday)
            .count()
        )
        assert opened <= 2

    # 5. Once the ceiling is reached, quiet mornings stay quiet: no selection
    #    row is written on a day where nothing was due.
    last_day = week_starts[-1] + timedelta(days=4)
    assert (
        session.query(MCXOptionsSelection)
        .filter_by(config_id=cfg.id, cycle_date=last_day)
        .count()
        == 0
    )
