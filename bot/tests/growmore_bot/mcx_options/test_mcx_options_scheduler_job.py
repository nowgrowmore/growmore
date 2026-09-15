"""Tests for growmore_bot.mcx_options.scheduler_job.run_mcx_options_configs.

Mirrors tests/growmore_bot/wheel_basket/test_scheduler_job.py's shape: Dhan
is faked/mocked entirely, `live_data.fetch_cycle_data` and `run_cycle` are
patched so this test proves only the orchestration wiring -- one cycle per
enabled config, a per-config try/except so one commodity's failure never
aborts another's cycle, disabled configs and configs with no matching
Instrument row are both skipped without ever touching Dhan.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.broker.dhan_client import DhanApiError
from growmore_bot.mcx_options import scheduler_job
from growmore_bot.mcx_options.scheduler_job import run_mcx_options_configs
from growmore_bot.persistence.models import Base, Instrument, MCXOptionsConfig, Strategy

TODAY = date(2026, 9, 14)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _strategy(session) -> Strategy:
    strategy = Strategy(id=uuid.uuid4(), name="mcx_options_wheel", version="1.0", params={})
    session.add(strategy)
    session.flush()
    return strategy


def _instrument(session, symbol: str) -> Instrument:
    inst = Instrument(
        id=uuid.uuid4(), symbol=symbol, exchange_segment="MCX_COMM",
        security_id="1", name=symbol, lot_size=10,
    )
    session.add(inst)
    session.flush()
    return inst


def _config(session, strategy: Strategy, symbol: str, enabled: bool = True) -> MCXOptionsConfig:
    cfg = MCXOptionsConfig(
        id=uuid.uuid4(), strategy_id=strategy.id, enabled=enabled, mode="paper", symbol=symbol,
        lots=1, consolidating_target_delta=0.30, trend_favorable_target_delta=0.50,
        min_open_interest=0, updated_at=datetime.now(timezone.utc),
    )
    session.add(cfg)
    session.commit()
    return cfg


def test_runs_one_cycle_per_enabled_config(session):
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _instrument(session, "SILVERM")
    _config(session, strategy, "GOLDM")
    _config(session, strategy, "SILVERM")
    dhan_client = MagicMock()

    with patch("growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data") as fetch, \
         patch("growmore_bot.mcx_options.scheduler_job.run_cycle") as run_cycle_mock, \
         patch("growmore_bot.mcx_options.scheduler_job.time.sleep"):
        fetch.return_value = MagicMock()
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    assert fetch.call_count == 2
    assert run_cycle_mock.call_count == 2
    called_symbols = {call.args[1].symbol for call in run_cycle_mock.call_args_list}
    assert called_symbols == {"GOLDM", "SILVERM"}
    for call in run_cycle_mock.call_args_list:
        assert call.kwargs["today"] == TODAY


def test_disabled_configs_are_skipped(session):
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _config(session, strategy, "GOLDM", enabled=False)
    dhan_client = MagicMock()

    with patch("growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data") as fetch, \
         patch("growmore_bot.mcx_options.scheduler_job.run_cycle") as run_cycle_mock:
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    fetch.assert_not_called()
    run_cycle_mock.assert_not_called()


def test_one_configs_failure_does_not_abort_the_others(session):
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _instrument(session, "SILVERM")
    _config(session, strategy, "GOLDM")
    _config(session, strategy, "SILVERM")
    dhan_client = MagicMock()

    def _fetch(client, instrument, today):
        if instrument.symbol == "GOLDM":
            raise ValueError("boom -- simulated Dhan failure for GOLDM")
        return MagicMock()

    with patch(
        "growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data", side_effect=_fetch,
    ), patch("growmore_bot.mcx_options.scheduler_job.run_cycle") as run_cycle_mock, \
       patch("growmore_bot.mcx_options.scheduler_job.time.sleep"):
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    assert run_cycle_mock.call_count == 1
    assert run_cycle_mock.call_args.args[1].symbol == "SILVERM"


def test_a_configs_run_cycle_failure_does_not_abort_the_others(session):
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _instrument(session, "SILVERM")
    _config(session, strategy, "GOLDM")
    _config(session, strategy, "SILVERM")
    dhan_client = MagicMock()

    def _run_cycle(session, config, cycle_data, today):
        if config.symbol == "GOLDM":
            raise RuntimeError("boom -- simulated engine failure for GOLDM")

    with patch("growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data") as fetch, \
         patch("growmore_bot.mcx_options.scheduler_job.run_cycle", side_effect=_run_cycle) as run_cycle_mock, \
         patch("growmore_bot.mcx_options.scheduler_job.time.sleep"):
        fetch.return_value = MagicMock()
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    assert run_cycle_mock.call_count == 2


def test_missing_instrument_row_is_skipped_without_calling_dhan(session):
    strategy = _strategy(session)
    _config(session, strategy, "GOLDM")  # no matching Instrument row
    dhan_client = MagicMock()

    with patch("growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data") as fetch, \
         patch("growmore_bot.mcx_options.scheduler_job.run_cycle") as run_cycle_mock:
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    fetch.assert_not_called()
    run_cycle_mock.assert_not_called()


def test_no_enabled_configs_means_no_dhan_calls_at_all(session):
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _config(session, strategy, "GOLDM", enabled=False)
    dhan_client = MagicMock()

    run_mcx_options_configs(session, dhan_client, today=TODAY)

    dhan_client.get_historical_ohlc.assert_not_called()
    dhan_client.get_option_chain.assert_not_called()


def test_fetch_is_retried_on_dhan_api_error_and_then_succeeds(session):
    """Confirmed 2026-09-15 production incident: back-to-back per-commodity
    Dhan calls can trip a burst/rate limit, surfacing as an opaque
    `DhanApiError` with no diagnostic detail. A single transient failure
    should be retried (with a backoff delay) rather than losing the whole
    commodity's cycle for the day.
    """
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _config(session, strategy, "GOLDM")
    dhan_client = MagicMock()

    calls = {"n": 0}
    successful_cycle_data = MagicMock()

    def _fetch(client, instrument, today):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DhanApiError(
                "{'error_code': None, 'error_type': None, 'error_message': None}"
            )
        return successful_cycle_data

    with patch(
        "growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data", side_effect=_fetch,
    ), patch("growmore_bot.mcx_options.scheduler_job.run_cycle") as run_cycle_mock, \
       patch("growmore_bot.mcx_options.scheduler_job.time.sleep") as sleep_mock:
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    assert calls["n"] == 2
    run_cycle_mock.assert_called_once()
    assert run_cycle_mock.call_args.args[2] is successful_cycle_data
    sleep_mock.assert_any_call(scheduler_job._FETCH_RETRY_DELAY_SECONDS)


def test_fetch_persistent_dhan_api_error_is_retried_then_skipped_without_aborting_others(session):
    """A commodity whose Dhan calls fail every retry attempt must still end
    up caught by the existing per-config try/except (skip this commodity,
    keep going) rather than propagating -- and must not prevent the other
    enabled commodity's cycle from running.
    """
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _instrument(session, "SILVERM")
    _config(session, strategy, "GOLDM")
    _config(session, strategy, "SILVERM")
    dhan_client = MagicMock()

    goldm_attempts = {"n": 0}

    def _fetch(client, instrument, today):
        if instrument.symbol == "GOLDM":
            goldm_attempts["n"] += 1
            raise DhanApiError("persistent burst-limit failure")
        return MagicMock()

    with patch(
        "growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data", side_effect=_fetch,
    ), patch("growmore_bot.mcx_options.scheduler_job.run_cycle") as run_cycle_mock, \
       patch("growmore_bot.mcx_options.scheduler_job.time.sleep"):
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    assert goldm_attempts["n"] == scheduler_job._FETCH_MAX_ATTEMPTS
    assert run_cycle_mock.call_count == 1
    assert run_cycle_mock.call_args.args[1].symbol == "SILVERM"


def test_inter_config_delay_when_more_than_one_enabled_config(session):
    """Spacing out each config's Dhan calls directly reduces how often the
    burst/rate limit gets tripped in the first place (the confirmed incident
    was two commodities' calls running back-to-back with zero delay).
    """
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _instrument(session, "SILVERM")
    _config(session, strategy, "GOLDM")
    _config(session, strategy, "SILVERM")
    dhan_client = MagicMock()

    with patch("growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data") as fetch, \
         patch("growmore_bot.mcx_options.scheduler_job.run_cycle"), \
         patch("growmore_bot.mcx_options.scheduler_job.time.sleep") as sleep_mock:
        fetch.return_value = MagicMock()
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    sleep_mock.assert_any_call(scheduler_job._INTER_CONFIG_DELAY_SECONDS)


def test_no_inter_config_delay_with_a_single_enabled_config(session):
    strategy = _strategy(session)
    _instrument(session, "GOLDM")
    _config(session, strategy, "GOLDM")
    dhan_client = MagicMock()

    with patch("growmore_bot.mcx_options.scheduler_job.live_data.fetch_cycle_data") as fetch, \
         patch("growmore_bot.mcx_options.scheduler_job.run_cycle"), \
         patch("growmore_bot.mcx_options.scheduler_job.time.sleep") as sleep_mock:
        fetch.return_value = MagicMock()
        run_mcx_options_configs(session, dhan_client, today=TODAY)

    sleep_mock.assert_not_called()
