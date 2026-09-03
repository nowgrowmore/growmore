"""Headless daily Dhan access-token refresh via TOTP.

Dhan access tokens expire every 24 hours. The App ID/Secret consent flow
that would normally automate re-issuing one needs a public HTTPS redirect
URL to receive the browser-login callback -- not available for a bot
running on a personal laptop (see docs/pending-actions.md). Dhan's
documented alternative is a headless endpoint that takes the account's
trading PIN plus a live TOTP code instead of a browser login:

    POST https://auth.dhan.co/app/generateAccessToken
        ?dhanClientId={id}&pin={pin}&totp={totp}

This module only ever calls that one endpoint (never Dhan's Order API) and
never logs or prints the PIN, TOTP secret, or resulting access token --
only whether a refresh happened and a token's expiry time.

CLI usage (see also bot/README.md):
    python -m growmore_bot.broker.token_refresh

Requires DHAN_PIN and DHAN_TOTP_SECRET in the repo-root .env.local, in
addition to the existing DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN. DHAN_TOTP_SECRET
is the raw base32 seed shown alongside the QR code when TOTP was set up on
Dhan (web.dhan.co -> Profile -> DhanHQ Trading APIs -> Set-up TOTP) -- NOT
a live 6-digit code, which changes every 30 seconds and can't be stored.

Not yet verified against a live call: the account owner didn't have the
PIN/TOTP secret in hand when this was written, so the response field names
below (accessToken, expiryTime) are per Dhan's documented API guide, not
confirmed against a real response. Verify on first live use and adjust the
parsing in generate_access_token_via_totp() if the real shape differs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pyotp
import requests

from growmore_bot.broker.dhan_client import _decode_jwt_exp

logger = logging.getLogger(__name__)

GENERATE_ACCESS_TOKEN_URL = "https://auth.dhan.co/app/generateAccessToken"
DEFAULT_REFRESH_THRESHOLD = timedelta(hours=2)


class DhanTokenRefreshError(RuntimeError):
    """Raised when the headless token-refresh call fails, or the env file
    can't be updated."""


def generate_access_token_via_totp(client_id: str, pin: str, totp_secret: str) -> tuple[str, str]:
    """Call Dhan's headless generateAccessToken endpoint. Returns
    (access_token, expiry_time_raw) -- the second value is whatever string
    Dhan sends back for informational logging, not parsed into a datetime
    (its exact format isn't confirmed yet, and the JWT `exp` claim on the
    token itself is what actually matters for refresh timing).
    """
    totp_code = pyotp.TOTP(totp_secret).now()
    response = requests.post(
        GENERATE_ACCESS_TOKEN_URL,
        params={"dhanClientId": client_id, "pin": pin, "totp": totp_code},
        timeout=30,
    )
    body = response.json()
    if response.status_code != 200 or "accessToken" not in body:
        raise DhanTokenRefreshError(
            f"Dhan token refresh failed (status {response.status_code}): "
            f"{body.get('errorMessage', body)}"
        )
    return body["accessToken"], body.get("expiryTime", "")


def write_access_token_to_env_file(env_file: Path, new_token: str) -> None:
    """Replace the DHAN_ACCESS_TOKEN=... line in `env_file` in place,
    preserving every other line exactly (comments, blank lines, order).
    """
    lines = env_file.read_text().splitlines(keepends=True)
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("DHAN_ACCESS_TOKEN="):
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = f"DHAN_ACCESS_TOKEN={new_token}{newline}"
            replaced = True
            break
    if not replaced:
        raise DhanTokenRefreshError(
            f"No existing DHAN_ACCESS_TOKEN= line found in {env_file} -- refusing to guess "
            "where to add one."
        )
    env_file.write_text("".join(lines))


def refresh_if_needed(
    current_token: str,
    client_id: str,
    pin: str,
    totp_secret: str,
    env_file: Path,
    threshold: timedelta = DEFAULT_REFRESH_THRESHOLD,
    now: Optional[datetime] = None,
) -> bool:
    """Refresh the access token (and update `env_file`) if it's within
    `threshold` of expiring, or already expired. Returns True if a refresh
    happened, False if the current token still has enough time left.
    """
    now = now or datetime.now(timezone.utc)
    exp = _decode_jwt_exp(current_token)
    if exp is not None and now < exp - threshold:
        return False

    new_token, expiry_raw = generate_access_token_via_totp(client_id, pin, totp_secret)
    write_access_token_to_env_file(env_file, new_token)
    logger.info("Dhan access token refreshed, new expiry: %s", expiry_raw)
    return True


__all__ = [
    "DhanTokenRefreshError",
    "generate_access_token_via_totp",
    "write_access_token_to_env_file",
    "refresh_if_needed",
    "DEFAULT_REFRESH_THRESHOLD",
]


def main() -> int:
    """CLI entrypoint: check the current token from the repo-root .env.local
    and refresh it if needed. Never prints the PIN/TOTP secret/token itself.
    """
    from growmore_bot.config import _REPO_ROOT_ENV_LOCAL, Settings

    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    if not settings.dhan_pin or not settings.dhan_totp_secret:
        print(
            "DHAN_PIN and/or DHAN_TOTP_SECRET are not set in the repo-root .env.local -- "
            "add them (see bot/growmore_bot/broker/token_refresh.py) before running this."
        )
        return 1

    try:
        refreshed = refresh_if_needed(
            current_token=settings.dhan_access_token,
            client_id=settings.dhan_client_id,
            pin=settings.dhan_pin,
            totp_secret=settings.dhan_totp_secret,
            env_file=_REPO_ROOT_ENV_LOCAL,
        )
    except DhanTokenRefreshError as e:
        print(f"Token refresh failed: {e}")
        return 1

    print("Token refreshed." if refreshed else "Token still valid, no refresh needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
