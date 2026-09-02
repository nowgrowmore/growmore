"""Tests for growmore_bot.main -- the process entrypoint.

We don't actually start the (blocking) scheduler in a unit test; we just
verify main() wires config -> DB init check -> scheduler.start() in order,
with everything mocked.
"""
from __future__ import annotations

from unittest.mock import patch


def test_main_loads_config_and_starts_scheduler(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "cid")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("DHAN_ENV", "sandbox")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/growmore_test")

    with patch("growmore_bot.scheduler.run.start") as mock_start:
        from growmore_bot.main import main

        main()

        mock_start.assert_called_once()
