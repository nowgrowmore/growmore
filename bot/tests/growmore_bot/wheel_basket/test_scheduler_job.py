"""Tests for growmore_bot.wheel_basket.scheduler_job.run_wheel_basket_configs.

The Dhan client and universe are both faked -- no real network or CSV file
read. Proves the orchestration wiring: builds one CandidateData per
universe row, runs one cycle per enabled config, and (mirroring
run_strategies.py/leaderboard_sim.py's own "one stock must not lose the
run" convention) a single symbol failing to fetch never aborts the whole
cycle.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.broker.dhan_client import OptionChainRow, OptionChainSnapshot, Quote
from growmore_bot.persistence.models import Base, Strategy, WheelBasketConfig, WheelBasketPosition
from growmore_bot.wheel_basket.scheduler_job import run_wheel_basket_configs
from growmore_bot.wheel_basket.universe import UniverseRow


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _config(session, enabled=True):
    strategy = Strategy(id=uuid.uuid4(), name="wheel_basket_iv", version="1.0", params={})
    session.add(strategy)
    session.flush()
    cfg = WheelBasketConfig(
        id=uuid.uuid4(), strategy_id=strategy.id, enabled=enabled, mode="paper",
        total_virtual_capital=1_000_000.0, top_iv_frac=0.5, rotation_hysteresis_pct=0.10,
        call_basis_buffer_tiers=[], updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.commit()
    return cfg


def _dhan_client(iv_by_symbol):
    """A stub whose option chain IV depends on which security_id was asked
    for, and whose historical bars are flat (RSI/MACD irrelevant here).
    """
    client = MagicMock()
    client.get_expiry_list.return_value = ["2026-09-24"]
    client.get_historical_ohlc.return_value = []

    def _quote(instrument):
        return Quote(ltp=100.0, open=100, high=100, low=100, close=100)

    def _chain(instrument, expiry):
        iv = iv_by_symbol[instrument.symbol]
        return OptionChainSnapshot(
            spot=100.0,
            rows=[
                OptionChainRow(strike=100.0, opt_type="PE", ltp=2.0, iv=iv, oi=10, volume=5),
                OptionChainRow(strike=100.0, opt_type="CE", ltp=2.0, iv=iv, oi=10, volume=5),
            ],
        )

    client.get_quote.side_effect = _quote
    client.get_option_chain.side_effect = _chain
    return client


def test_runs_one_cycle_per_enabled_config_over_the_whole_universe(session, monkeypatch):
    config = _config(session)
    universe = [
        UniverseRow(symbol="LOWIV", security_id="1", lot_size=100),
        UniverseRow(symbol="HIGHIV", security_id="2", lot_size=100),
    ]
    monkeypatch.setattr(
        "growmore_bot.wheel_basket.scheduler_job.load_universe", lambda: universe
    )
    client = _dhan_client({"LOWIV": 0.20, "HIGHIV": 0.60})

    run_wheel_basket_configs(session, client)
    session.commit()

    positions = session.query(WheelBasketPosition).filter_by(config_id=config.id).all()
    assert len(positions) == 1
    assert positions[0].symbol == "HIGHIV"


def test_disabled_configs_are_skipped(session, monkeypatch):
    _config(session, enabled=False)
    universe = [UniverseRow(symbol="ANY", security_id="1", lot_size=100)]
    monkeypatch.setattr(
        "growmore_bot.wheel_basket.scheduler_job.load_universe", lambda: universe
    )
    client = _dhan_client({"ANY": 0.5})

    run_wheel_basket_configs(session, client)
    session.commit()

    assert session.query(WheelBasketPosition).count() == 0


def test_a_symbol_that_fails_to_fetch_does_not_abort_the_whole_cycle(session, monkeypatch):
    config = _config(session)
    universe = [
        UniverseRow(symbol="BROKEN", security_id="1", lot_size=100),
        UniverseRow(symbol="FINE", security_id="2", lot_size=100),
    ]
    monkeypatch.setattr(
        "growmore_bot.wheel_basket.scheduler_job.load_universe", lambda: universe
    )
    client = _dhan_client({"FINE": 0.5})  # BROKEN deliberately not in the dict -> KeyError

    run_wheel_basket_configs(session, client)
    session.commit()

    positions = session.query(WheelBasketPosition).filter_by(config_id=config.id).all()
    assert len(positions) == 1
    assert positions[0].symbol == "FINE"


def test_no_enabled_configs_means_no_dhan_calls_at_all(session, monkeypatch):
    universe = [UniverseRow(symbol="ANY", security_id="1", lot_size=100)]
    monkeypatch.setattr(
        "growmore_bot.wheel_basket.scheduler_job.load_universe", lambda: universe
    )
    client = MagicMock()

    run_wheel_basket_configs(session, client)

    client.get_expiry_list.assert_not_called()
