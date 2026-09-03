"""Tests for growmore_bot.broker.token_refresh -- headless daily access-token
refresh via Dhan's TOTP-based `generateAccessToken` endpoint.

This sidesteps the App ID/Secret consent flow, which needs a public HTTPS
redirect URL we don't have for a bot running on a personal laptop (see
docs/pending-actions.md). No real network calls in these tests: the Dhan
HTTP call is mocked with `responses`; TOTP codes are generated for real via
pyotp against a throwaway test secret, since that part is pure local math.

Note: the response field names (`accessToken`, `expiryTime`) were confirmed
against a real live call 2026-09-03. That same live testing also surfaced a
real, observed failure mode worth testing for: a valid TOTP code can still
be rejected ("Invalid TOTP") if the code expires in the second or two
between being generated and Dhan's server validating the request -- a
timing fluke, not a wrong secret. Retried immediately with a freshly
generated code, it succeeded. `refresh_if_needed` retries for exactly this
reason.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyotp
import pytest
import responses

from growmore_bot.broker.token_refresh import (
    DhanTokenRefreshError,
    generate_access_token_via_totp,
    is_new_trading_day,
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


@responses.activate
def test_refresh_if_needed_retries_a_transient_invalid_totp_and_then_succeeds(tmp_path: Path):
    # Regression: observed for real 2026-09-03 -- a valid TOTP code can still
    # be rejected if it expires between being generated and the server
    # validating the request. The very next attempt (fresh code) succeeded.
    env_file = tmp_path / ".env.local"
    current_token = _make_jwt(datetime.now(timezone.utc) - timedelta(minutes=5))
    env_file.write_text(f"DHAN_ACCESS_TOKEN={current_token}\n")
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"message": "Invalid TOTP", "status": "error"},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"accessToken": "token-after-retry", "expiryTime": "2026-09-04T12:00:00Z"},
        status=200,
    )

    sleeps = []
    refreshed = refresh_if_needed(
        current_token=current_token,
        client_id="1113562866",
        pin="1234",
        totp_secret=TOTP_SECRET,
        env_file=env_file,
        threshold=timedelta(hours=2),
        max_attempts=3,
        sleep=sleeps.append,
    )

    assert refreshed is True
    assert "token-after-retry" in env_file.read_text()
    assert len(responses.calls) == 2
    assert len(sleeps) == 1  # slept once, between the two attempts


@responses.activate
def test_refresh_if_needed_force_true_ignores_threshold(tmp_path: Path):
    # SEBI's retail algo API guidance expects an automatic session reset
    # before each trading day -- force=True is how the scheduler triggers
    # that, independent of how much validity the current token has left.
    env_file = tmp_path / ".env.local"
    current_token = _make_jwt(datetime.now(timezone.utc) + timedelta(hours=20))
    env_file.write_text(f"DHAN_ACCESS_TOKEN={current_token}\n")
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"accessToken": "forced-fresh-session-token", "expiryTime": "2026-09-04T12:00:00Z"},
        status=200,
    )

    refreshed = refresh_if_needed(
        current_token=current_token,
        client_id="1113562866",
        pin="1234",
        totp_secret=TOTP_SECRET,
        env_file=env_file,
        threshold=timedelta(hours=2),
        force=True,
    )

    assert refreshed is True
    assert "forced-fresh-session-token" in env_file.read_text()


def test_is_new_trading_day_true_when_never_reset():
    assert is_new_trading_day(None, date(2026, 9, 4)) is True


def test_is_new_trading_day_false_on_same_day():
    assert is_new_trading_day(date(2026, 9, 4), date(2026, 9, 4)) is False


def test_is_new_trading_day_true_on_later_day():
    assert is_new_trading_day(date(2026, 9, 3), date(2026, 9, 4)) is True


def test_is_new_trading_day_false_if_somehow_earlier():
    assert is_new_trading_day(date(2026, 9, 5), date(2026, 9, 4)) is False


@responses.activate
def test_refresh_if_needed_gives_up_after_max_attempts(tmp_path: Path):
    env_file = tmp_path / ".env.local"
    current_token = _make_jwt(datetime.now(timezone.utc) - timedelta(minutes=5))
    env_file.write_text(f"DHAN_ACCESS_TOKEN={current_token}\n")
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"message": "Invalid TOTP", "status": "error"},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"message": "Invalid TOTP", "status": "error"},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://auth.dhan.co/app/generateAccessToken",
        json={"message": "Invalid TOTP", "status": "error"},
        status=200,
    )

    with pytest.raises(DhanTokenRefreshError, match="Invalid TOTP"):
        refresh_if_needed(
            current_token=current_token,
            client_id="1113562866",
            pin="1234",
            totp_secret=TOTP_SECRET,
            env_file=env_file,
            threshold=timedelta(hours=2),
            max_attempts=3,
            sleep=lambda _: None,
        )

    assert len(responses.calls) == 3
    # The original (still-expired) token must be left untouched on failure.
    assert current_token in env_file.read_text()
