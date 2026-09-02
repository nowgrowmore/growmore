"""Process entrypoint: load config, sanity-check it, start the scheduler.

Run with `python -m growmore_bot.main` or the `growmore-bot` console script
(see pyproject.toml). Requires DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN / DHAN_ENV /
DATABASE_URL to be set -- see bot/README.md for local setup via
repo-root .env.local.
"""
from __future__ import annotations

import logging

from growmore_bot.config import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    settings = Settings()  # raises clearly if required env vars are missing
    logger.info(
        "growmore-bot starting (env=%s, polling every %ss)",
        settings.dhan_env,
        settings.default_polling_interval_seconds,
    )

    from growmore_bot.scheduler import run as scheduler_run

    scheduler_run.start(poll_interval_seconds=settings.default_polling_interval_seconds)


if __name__ == "__main__":
    main()
