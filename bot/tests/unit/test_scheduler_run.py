"""Tests for growmore_bot.scheduler.run -- the market-hours-gated tick.

We don't stand up a real APScheduler loop here; we test the pure `tick()`
callback that the scheduler invokes, verifying it calls the paper engine
only when the market is open.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytz

from growmore_bot.scheduler.run import tick

IST = pytz.timezone("Asia/Kolkata")


def test_tick_runs_paper_engine_when_market_open():
    run_all_configs = MagicMock()
    now = IST.localize(datetime(2026, 9, 2, 14, 0))  # Wednesday, inside hours

    tick(run_all_configs=run_all_configs, now=now)

    run_all_configs.assert_called_once()


def test_tick_skips_paper_engine_when_market_closed():
    run_all_configs = MagicMock()
    now = IST.localize(datetime(2026, 9, 2, 2, 0))  # Wednesday, outside hours

    tick(run_all_configs=run_all_configs, now=now)

    run_all_configs.assert_not_called()


def test_tick_skips_on_weekend():
    run_all_configs = MagicMock()
    now = IST.localize(datetime(2026, 9, 6, 14, 0))  # Sunday

    tick(run_all_configs=run_all_configs, now=now)

    run_all_configs.assert_not_called()
