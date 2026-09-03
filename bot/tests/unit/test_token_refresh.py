"""Tests for growmore_bot.broker.token_refresh -- headless daily access-token
refresh via Dhan's TOTP-based `generateAccessToken` endpoint.

This sidesteps the App ID/Secret consent flow, which needs a public HTTPS
redirect URL we don't have for a bot running on a personal laptop (see
docs/pending-actions.md). No real network calls in these tests: the Dhan
HTTP call is mocked with `responses`; TOTP codes are generated for real via
pyotp against a throwaway test secret, since that part is pure local math.

Note: the exact response field names (`accessToken`, `expiryTime`) are per
Dhan's documented API guide, not yet confirmed against a live call (the
account owner didn't have the PIN/TOTP secret in hand at the time this was
written) -- verify against the real endpoint on first live use.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyotp
import pytest
import responses

from growmore_bot.broker.token_refresh import (
    DhanTokenRefreshError,
    generate_access_token_via_totp,
    refresh_if_needed,
    write_access_token_to_env_file,
)

TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # well-known RFC 4226 example secret, not a real one


def _make_jwt(exp: datetime) -> str:
    import base64
    import json

    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(exp.timestamp())}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesignature"


@responses.activate
def test_generate_access_token_via_totp_sends_correct_totp_and_parses_response():
    expected_totp = pyotp.TOTP(TOTP_SECRET).now()
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"accessToken": "new-token-value", "expiryTime": "2026-09-04T12:00:00Z"},
        status=200,
    )

    token, expiry = generate_access_token_via_totp(
        client_id="1113562866", pin="1234", totp_secret=TOTP_SECRET
    )

    assert token == "new-token-value"
    # Returned raw (not parsed into a datetime) -- Dhan's exact expiryTime
    # format isn't confirmed against a live response yet, see module docstring.
    assert expiry == "2026-09-04T12:00:00Z"

    sent_params = responses.calls[0].request.params
    assert sent_params["dhanClientId"] == "1113562866"
    assert sent_params["pin"] == "1234"
    assert sent_params["totp"] == expected_totp


@responses.activate
def test_generate_access_token_via_totp_raises_clear_error_on_failure():
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"errorMessage": "Invalid TOTP"},
        status=400,
    )

    with pytest.raises(DhanTokenRefreshError, match="Invalid TOTP|400"):
        generate_access_token_via_totp(client_id="1113562866", pin="1234", totp_secret=TOTP_SECRET)


def test_write_access_token_to_env_file_replaces_only_that_line(tmp_path: Path):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "DHAN_CLIENT_ID=1113562866\n"
        "DHAN_ACCESS_TOKEN=old-token-value\n"
        "DHAN_ENV=production\n"
        "\n"
        "# a comment\n"
        "DATABASE_URL=postgresql://x\n"
    )

    write_access_token_to_env_file(env_file, "brand-new-token")

    contents = env_file.read_text()
    assert "DHAN_ACCESS_TOKEN=brand-new-token" in contents
    assert "old-token-value" not in contents
    # Everything else preserved, untouched, in order.
    assert "DHAN_CLIENT_ID=1113562866" in contents
    assert "DHAN_ENV=production" in contents
    assert "# a comment" in contents
    assert "DATABASE_URL=postgresql://x" in contents


def test_write_access_token_to_env_file_errors_if_no_existing_line(tmp_path: Path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("DHAN_CLIENT_ID=123\n")

    with pytest.raises(DhanTokenRefreshError, match="DHAN_ACCESS_TOKEN"):
        write_access_token_to_env_file(env_file, "brand-new-token")


@responses.activate
def test_refresh_if_needed_skips_when_token_has_plenty_of_time_left(tmp_path: Path):
    env_file = tmp_path / ".env.local"
    current_token = _make_jwt(datetime.now(timezone.utc) + timedelta(hours=20))
    env_file.write_text(f"DHAN_ACCESS_TOKEN={current_token}\n")

    refreshed = refresh_if_needed(
        current_token=current_token,
        client_id="1113562866",
        pin="1234",
        totp_secret=TOTP_SECRET,
        env_file=env_file,
        threshold=timedelta(hours=2),
    )

    assert refreshed is False
    assert len(responses.calls) == 0
    assert current_token in env_file.read_text()


@responses.activate
def test_refresh_if_needed_refreshes_when_close_to_expiry(tmp_path: Path):
    env_file = tmp_path / ".env.local"
    current_token = _make_jwt(datetime.now(timezone.utc) + timedelta(minutes=30))
    env_file.write_text(f"DHAN_ACCESS_TOKEN={current_token}\n")
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"accessToken": "freshly-refreshed-token", "expiryTime": "2026-09-04T12:00:00Z"},
        status=200,
    )

    refreshed = refresh_if_needed(
        current_token=current_token,
        client_id="1113562866",
        pin="1234",
        totp_secret=TOTP_SECRET,
        env_file=env_file,
        threshold=timedelta(hours=2),
    )

    assert refreshed is True
    assert "freshly-refreshed-token" in env_file.read_text()


@responses.activate
def test_refresh_if_needed_refreshes_when_already_expired(tmp_path: Path):
    env_file = tmp_path / ".env.local"
    current_token = _make_jwt(datetime.now(timezone.utc) - timedelta(minutes=5))
    env_file.write_text(f"DHAN_ACCESS_TOKEN={current_token}\n")
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"accessToken": "freshly-refreshed-token", "expiryTime": "2026-09-04T12:00:00Z"},
        status=200,
    )

    refreshed = refresh_if_needed(
        current_token=current_token,
        client_id="1113562866",
        pin="1234",
        totp_secret=TOTP_SECRET,
        env_file=env_file,
        threshold=timedelta(hours=2),
    )

    assert refreshed is True
