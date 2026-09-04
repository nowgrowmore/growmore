"""Tests for growmore_bot.scheduler.run.run_all_enabled_configs and its
cumulative-daily-P&L helper.

Uses a real (in-memory SQLite) session rather than mocking the ORM query
chain -- this is exactly the kind of wiring where deep mocks would have
hidden real bugs found while first setting up real paper trading:
(1) three of the five strategies (RSI, MACD, Bollinger) were missing from
`strategy_builders` entirely, so a bot_config using any of them was silently
skipped every tick; (2) `cumulative_daily_pnl` was hardcoded to 0.0, so the
daily_loss_limit guard never actually tripped; (3) the most severe one --
a brand-new strategy instance is built every tick (deliberately, to keep
the scheduler stateless), which meant an indicator needing N bars of
history (e.g. MACD needs 13+) was fed exactly one live price per tick and
discarded, so it could NEVER accumulate enough history to produce a real
signal, no matter how long the bot ran. Fixed by `_warm_up_strategy`
replaying real historical daily bars into the fresh strategy before it
ever sees the live quote.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.broker.dhan_client import Quote
from growmore_bot.persistence.models import (
    Base,
    BotConfig,
    Instrument,
    PaperOrder,
    PaperPosition,
    Strategy,
)
from growmore_bot.scheduler.run import (
    _cumulative_daily_pnl,
    _warm_up_strategy,
    run_all_enabled_configs,
)

MCX_TZ = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_strategy_instrument_config(session, strategy_name, params, enabled=True, mode="paper"):
    strategy = Strategy(id=uuid.uuid4(), name=strategy_name, version="v1", params=params)
    instrument = Instrument(
        id=uuid.uuid4(), symbol="GOLDM", exchange_segment="MCX_COMM",
        security_id="1", name="Gold Mini", lot_size=100,
    )
    config = BotConfig(
        id=uuid.uuid4(), strategy_id=strategy.id, instrument_id=instrument.id,
        enabled=enabled, virtual_capital=250_000, max_position_size=10, daily_loss_limit=5_000,
        mode=mode,
    )
    session.add_all([strategy, instrument, config])
    session.commit()
    return strategy, instrument, config


@pytest.mark.parametrize(
    "strategy_name,params",
    [
        ("sma_crossover", {"fast_period": 5, "slow_period": 20}),
        ("donchian_breakout", {"period": 10}),
        ("rsi_mean_reversion", {"period": 7, "oversold": 30, "overbought": 70}),
        ("macd_trend", {"fast_period": 5, "slow_period": 13, "signal_period": 5}),
        ("bollinger_reversion", {"period": 20, "num_std": 2.0}),
        ("always_flip", {}),
    ],
)
def test_all_five_strategies_are_actually_runnable(session, strategy_name, params):
    # Regression: only sma_crossover/donchian_breakout used to be wired into
    # strategy_builders -- the other 3 (added this session) were silently
    # skipped with just a log warning, never actually ticking.
    _make_strategy_instrument_config(session, strategy_name, params)
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)

    run_all_enabled_configs(session, dhan_client)

    dhan_client.get_quote.assert_called_once()


def test_unknown_strategy_name_is_skipped_without_calling_dhan(session):
    _make_strategy_instrument_config(session, "some_future_strategy", {})
    dhan_client = MagicMock()

    run_all_enabled_configs(session, dhan_client)

    dhan_client.get_quote.assert_not_called()


def test_cumulative_daily_pnl_sums_only_todays_sells_for_this_pair(session):
    strategy, instrument, config = _make_strategy_instrument_config(
        session, "macd_trend", {"fast_period": 5, "slow_period": 13, "signal_period": 5}
    )
    other_strategy, other_instrument, _ = _make_strategy_instrument_config(
        session, "sma_crossover", {"fast_period": 5, "slow_period": 20}
    )

    now = datetime.now(MCX_TZ)
    today_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)
    yesterday = today_9am - timedelta(days=1)

    position = PaperPosition(
        id=uuid.uuid4(), strategy_id=strategy.id, instrument_id=instrument.id,
        status="closed", quantity=0, avg_entry_price=150000, realized_pnl=-2000,
        unrealized_pnl=0, opened_at=yesterday, closed_at=today_9am,
    )
    other_position = PaperPosition(
        id=uuid.uuid4(), strategy_id=other_strategy.id, instrument_id=other_instrument.id,
        status="closed", quantity=0, avg_entry_price=150000, realized_pnl=9999,
        unrealized_pnl=0, opened_at=yesterday, closed_at=today_9am,
    )
    session.add_all([position, other_position])
    session.flush()

    session.add_all([
        # Today, this pair: counted.
        PaperOrder(id=uuid.uuid4(), paper_position_id=position.id, side="sell",
                   quantity=1, simulated_fill_price=148000, filled_at=today_9am, pnl=-2000),
        # Yesterday, this pair: NOT counted (different day).
        PaperOrder(id=uuid.uuid4(), paper_position_id=position.id, side="sell",
                   quantity=1, simulated_fill_price=149000, filled_at=yesterday, pnl=-1000),
        # Today, a DIFFERENT strategy/instrument pair: NOT counted.
        PaperOrder(id=uuid.uuid4(), paper_position_id=other_position.id, side="sell",
                   quantity=1, simulated_fill_price=160000, filled_at=today_9am, pnl=9999),
        # Today, this pair, but a buy fill (pnl=None): NOT counted.
        PaperOrder(id=uuid.uuid4(), paper_position_id=position.id, side="buy",
                   quantity=1, simulated_fill_price=150000, filled_at=today_9am, pnl=None),
    ])
    session.commit()

    result = _cumulative_daily_pnl(session, strategy.id, instrument.id, now)

    assert result == pytest.approx(-2000)


@pytest.fixture(autouse=True)
def _no_real_instrument_master_network_calls(monkeypatch):
    # run_all_enabled_configs fetches Dhan's real instrument master over the
    # network when a config is past its close-out cutoff (see
    # test_past_close_out_cutoff_attempts_automatic_rollover for the one test
    # that overrides this to verify the attempt itself) -- never allow that
    # in the rest of this file's tests, which mock the Dhan client entirely.
    from growmore_bot.scheduler import run as run_module

    monkeypatch.setattr(
        run_module,
        "fetch_instrument_master_csv",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected real instrument-master fetch")),
        raising=False,
    )


def test_past_close_out_cutoff_force_closes_open_position_and_skips_strategy(session):
    # GOLDM (bullion): close-out cutoff is 8 trading days before expiry --
    # see contract_rollover.py. Use an expiry far enough in the past that
    # "now" is comfortably past that cutoff.
    strategy, instrument, config = _make_strategy_instrument_config(
        session, "macd_trend", {"fast_period": 5, "slow_period": 13, "signal_period": 5}
    )
    instrument.symbol = "GOLDM"
    instrument.contract_expiry = date(2026, 1, 9)  # long past -- any "now" in this test is after it
    session.add(instrument)

    position = PaperPosition(
        id=uuid.uuid4(), strategy_id=strategy.id, instrument_id=instrument.id,
        status="open", quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, opened_at=datetime.now(MCX_TZ) - timedelta(days=1), closed_at=None,
    )
    session.add(position)
    session.commit()

    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)

    run_all_enabled_configs(session, dhan_client, now=datetime.now(MCX_TZ))

    session.refresh(position)
    assert position.status == "closed"
    assert float(position.quantity) == pytest.approx(0)
    # Strategy warm-up/evaluation must be skipped entirely for a config
    # past its close-out cutoff -- no historical fetch, no fresh entries.
    dhan_client.get_historical_ohlc.assert_not_called()


def test_past_close_out_cutoff_attempts_automatic_rollover(session, monkeypatch):
    strategy, instrument, config = _make_strategy_instrument_config(session, "always_flip", {})
    instrument.symbol = "GOLDM"
    instrument.contract_expiry = date(2026, 1, 9)
    session.add(instrument)
    session.commit()

    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)

    from growmore_bot.scheduler import run as run_module

    monkeypatch.setattr(
        run_module, "fetch_instrument_master_csv", lambda: "FAKE_CSV", raising=False
    )
    roll_calls = []

    def _fake_roll(session_arg, dhan_client_arg, instrument_arg, csv_text):
        roll_calls.append((instrument_arg.symbol, csv_text))
        return False  # no real CSV to roll against in this test -- just verify it's attempted

    monkeypatch.setattr(run_module, "roll_to_next_contract", _fake_roll, raising=False)

    run_all_enabled_configs(session, dhan_client, now=datetime.now(MCX_TZ))

    assert roll_calls == [("GOLDM", "FAKE_CSV")]


def test_past_close_out_cutoff_blocks_new_entries_when_no_open_position(session):
    strategy, instrument, config = _make_strategy_instrument_config(
        session, "always_flip", {}
    )
    instrument.symbol = "GOLDM"
    instrument.contract_expiry = date(2026, 1, 9)
    session.add(instrument)
    session.commit()

    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)

    run_all_enabled_configs(session, dhan_client, now=datetime.now(MCX_TZ))

    # always_flip would BUY given the chance (no open position) -- but a
    # config past its close-out cutoff must never open a fresh position.
    assert session.query(PaperPosition).count() == 0
    dhan_client.get_quote.assert_not_called()


def test_live_mode_config_skipped_entirely_when_live_trading_globally_disabled(session):
    # Both gates (bot_config.mode="live" AND the global kill switch) must be
    # open -- a live config must never silently fall back to paper trading.
    _make_strategy_instrument_config(session, "always_flip", {}, mode="live")
    dhan_client = MagicMock()

    run_all_enabled_configs(session, dhan_client, live_trading_enabled=False)

    dhan_client.get_quote.assert_not_called()
    assert session.query(PaperPosition).count() == 0


def test_live_mode_config_places_a_real_order_when_fully_enabled(session):
    from growmore_bot.broker.dhan_order_client import PlacedOrder
    from growmore_bot.persistence.models import LiveOrder, LivePosition

    _make_strategy_instrument_config(session, "always_flip", {}, mode="live")
    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.return_value = PlacedOrder(order_id="ORD1", order_status="TRANSIT")

    run_all_enabled_configs(
        session, dhan_client, order_client=order_client, live_trading_enabled=True
    )

    order_client.place_market_order.assert_called_once()
    assert session.query(LivePosition).count() == 1
    assert session.query(LiveOrder).count() == 1
    # Never touches paper tables for a live-mode config.
    assert session.query(PaperPosition).count() == 0


def test_live_mode_config_past_close_out_cutoff_places_a_real_closing_order(session):
    from growmore_bot.broker.dhan_order_client import PlacedOrder
    from growmore_bot.persistence.models import LiveOrder, LivePosition

    strategy, instrument, config = _make_strategy_instrument_config(
        session, "macd_trend", {"fast_period": 5, "slow_period": 13, "signal_period": 5}, mode="live"
    )
    instrument.symbol = "GOLDM"
    instrument.contract_expiry = date(2026, 1, 9)
    session.add(instrument)

    position = LivePosition(
        id=uuid.uuid4(), strategy_id=strategy.id, instrument_id=instrument.id,
        status="open", quantity=1, avg_entry_price=150000, realized_pnl=0,
        unrealized_pnl=0, opened_at=datetime.now(MCX_TZ) - timedelta(days=1), closed_at=None,
    )
    session.add(position)
    session.commit()

    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=155000, open=155000, high=155000, low=155000, close=155000)
    order_client = MagicMock()
    order_client.place_market_order.return_value = PlacedOrder(order_id="ORD2", order_status="TRANSIT")

    run_all_enabled_configs(
        session, dhan_client, now=datetime.now(MCX_TZ), order_client=order_client,
        live_trading_enabled=True,
    )

    order_client.place_market_order.assert_called_once_with(
        instrument, transaction_type="SELL", quantity=1
    )
    session.refresh(position)
    assert position.status == "closed"
    assert session.query(LiveOrder).count() == 1
    dhan_client.get_historical_ohlc.assert_not_called()


def test_run_all_enabled_configs_restores_persisted_crossing_state(session, monkeypatch):
    # Regression: found live 2026-09-04 -- without restoring the crossing
    # reference from the last LIVE tick, a fresh warmed-up strategy always
    # compares against yesterday's close, so a signal meant to fire once
    # re-fires every tick. This verifies the WIRING: the persisted
    # bot_signal_state.crossing_state actually reaches
    # Strategy.load_state_snapshot() before the live quote is evaluated.
    from growmore_bot.persistence.models import BotSignalState
    from growmore_bot.strategies.always_flip import AlwaysFlipStrategy

    strategy, instrument, config = _make_strategy_instrument_config(session, "always_flip", {})
    session.add(
        BotSignalState(
            id=uuid.uuid4(), bot_config_id=config.id, last_signal="BUY",
            checked_at=datetime.now(MCX_TZ), ltp=100, indicators={},
            crossing_state={"some_reference": True},
        )
    )
    session.commit()

    dhan_client = MagicMock()
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)

    received: list[dict] = []
    original_load = AlwaysFlipStrategy.load_state_snapshot

    def _spy_load(self, snapshot):
        received.append(snapshot)
        return original_load(self, snapshot)

    monkeypatch.setattr(AlwaysFlipStrategy, "load_state_snapshot", _spy_load)

    run_all_enabled_configs(session, dhan_client)

    assert received == [{"some_reference": True}]


def test_warm_up_strategy_replays_historical_bars_in_order():
    seen_closes = []

    class _SpyStrategy:
        def on_bar(self, bar, position_state):
            seen_closes.append(bar.close)
            from growmore_bot.strategies.base import Signal, SignalAction

            return Signal(action=SignalAction.HOLD)

    bars = [SimpleNamespace(close=c) for c in [10, 20, 30]]
    dhan_client = MagicMock()
    dhan_client.get_historical_ohlc.return_value = bars
    instrument = SimpleNamespace(id=uuid.uuid4())

    _warm_up_strategy(_SpyStrategy(), dhan_client, instrument, datetime.now(MCX_TZ))

    assert seen_closes == [10, 20, 30]
    # Warm-up must stop at yesterday -- today's live quote is fed separately
    # as today's still-forming bar (see PaperTradingEngine).
    call_kwargs = dhan_client.get_historical_ohlc.call_args.kwargs
    assert call_kwargs["to_date"] < datetime.now(MCX_TZ).date().isoformat()


def test_warm_up_strategy_swallows_fetch_errors():
    class _NeverCalledStrategy:
        def on_bar(self, bar, position_state):
            raise AssertionError("on_bar should never be called if the fetch failed")

    dhan_client = MagicMock()
    dhan_client.get_historical_ohlc.side_effect = RuntimeError("network error")
    instrument = SimpleNamespace(id=uuid.uuid4())

    # Must not raise -- a warm-up hiccup on one tick shouldn't crash the scheduler.
    _warm_up_strategy(_NeverCalledStrategy(), dhan_client, instrument, datetime.now(MCX_TZ))


def test_run_all_enabled_configs_actually_produces_a_signal_after_warm_up(session):
    # This is the end-to-end regression test for the critical bug: before
    # the fix, this would NEVER produce a trade no matter how it was run,
    # because a fresh, empty strategy was evaluated against exactly one
    # live price per tick.
    strategy, instrument, config = _make_strategy_instrument_config(
        session, "sma_crossover", {"fast_period": 2, "slow_period": 3}
    )

    dhan_client = MagicMock()
    # Constant history -> fast==slow (not >), so _prev_fast_above_slow ends up False.
    dhan_client.get_historical_ohlc.return_value = [
        SimpleNamespace(close=10) for _ in range(4)
    ]
    # A sharp live move flips fast (SMA2(10,100)=55) above slow (SMA3(10,10,100)=40)
    # -- a fresh cross from False to True, i.e. a real BUY signal.
    dhan_client.get_quote.return_value = Quote(ltp=100, open=100, high=100, low=100, close=100)

    run_all_enabled_configs(session, dhan_client)

    positions = session.query(PaperPosition).filter_by(
        strategy_id=strategy.id, instrument_id=instrument.id
    ).all()
    assert len(positions) == 1
    assert positions[0].status == "open"
    assert float(positions[0].avg_entry_price) == pytest.approx(100)
