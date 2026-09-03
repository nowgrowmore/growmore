"""Tests for growmore_bot.config.Settings.

TDD: written before growmore_bot/config.py exists.
"""
from __future__ import annotations

import pytest


def _base_env(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("DHAN_ENV", "sandbox")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/growmore_test")


def test_settings_loads_required_fields_from_env(monkeypatch):
    from growmore_bot.config import Settings

    _base_env(monkeypatch)
    settings = Settings()

    assert settings.dhan_client_id == "test-client-id"
    assert settings.dhan_access_token == "test-access-token"
    assert settings.dhan_env == "sandbox"
    assert settings.database_url == "postgresql://postgres:postgres@localhost:5432/growmore_test"


def test_settings_missing_required_field_raises(monkeypatch):
    from growmore_bot.config import Settings

    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DHAN_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_settings_rejects_invalid_dhan_env(monkeypatch):
    from growmore_bot.config import Settings

    _base_env(monkeypatch)
    monkeypatch.setenv("DHAN_ENV", "not-a-real-env")

    with pytest.raises(Exception):
        Settings()


def test_settings_trading_defaults(monkeypatch):
    from growmore_bot.config import Settings

    _base_env(monkeypatch)
    settings = Settings()

    assert settings.default_virtual_capital == 500_000
    assert settings.default_polling_interval_seconds == 300
    assert settings.mcx_market_open == "09:00"
    assert settings.mcx_market_close == "23:30"
    assert settings.mcx_timezone == "Asia/Kolkata"


def test_settings_default_commodity_universe_has_real_security_ids(monkeypatch):
    from growmore_bot.config import Settings

    _base_env(monkeypatch)
    settings = Settings()

    # Natural Gas intentionally excluded from the default universe for now.
    symbols = {c.symbol for c in settings.default_commodity_universe}
    assert symbols == {"GOLDM", "SILVERM", "CRUDEOILM"}
    for commodity in settings.default_commodity_universe:
        # Looked up from Dhan's instrument master 2026-09-03 -- never guessed.
        assert commodity.security_id != "TODO_LOOKUP_DHAN_SECURITY_ID"
        assert commodity.security_id.isdigit()
        assert commodity.contract_expiry is not None
        assert commodity.exchange_segment == "MCX_COMM"


def test_settings_overridable_polling_interval(monkeypatch):
    from growmore_bot.config import Settings

    _base_env(monkeypatch)
    monkeypatch.setenv("DEFAULT_POLLING_INTERVAL_SECONDS", "60")
    settings = Settings()

    assert settings.default_polling_interval_seconds == 60
