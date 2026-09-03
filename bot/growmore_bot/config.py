"""Application configuration, loaded from environment variables / .env files.

Real secrets (Dhan client id / access token, the Neon DATABASE_URL) live only in
gitignored `.env.local` at the repo root or the process environment -- never in this
file and never committed. See bot/README.md for local setup.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env.local, resolved from this file's own location -- NOT relative
# to the current working directory. A relative "​.env.local" here only worked
# if you happened to `cd` to the exact right place first; `python -m
# growmore_bot.main` run from bot/ (the documented way to run it) would
# silently look for bot/.env.local, which doesn't exist, and Settings() would
# fail with "field required" for every secret even though .env.local is
# sitting right there at the repo root.
_REPO_ROOT_ENV_LOCAL = Path(__file__).resolve().parents[2] / ".env.local"


class CommodityPlaceholder(BaseModel):
    """A default-universe commodity entry.

    `security_id` identifies one specific FUTCOM contract month on MCX, looked
    up from Dhan's instrument master (https://images.dhan.co/api-data/api-scrip-master.csv)
    -- these are NEVER guessed. Futures contracts roll: `contract_expiry` records
    which month `security_id` currently points at, so it's obvious when this
    needs to be refreshed (shortly before/at expiry) rather than silently going
    stale. Looked up 2026-09-03 for the then-current front-month contract.

    `lot_size` is the real MCX contract trading unit (looked up from MCX's
    official contract specs / mirrored broker spec pages 2026-09-03, never
    guessed) -- without it, a backtest treats every instrument as "1 raw unit
    of the price series," which makes Sharpe/Max Drawdown incomparable across
    commodities at very different price levels.
    """

    symbol: str
    name: str
    exchange_segment: str = "MCX_COMM"
    security_id: str = "TODO_LOOKUP_DHAN_SECURITY_ID"
    contract_expiry: str | None = None  # ISO date; None only for the not-yet-looked-up default
    lot_size: int = 1


DEFAULT_COMMODITY_UNIVERSE: list[CommodityPlaceholder] = [
    CommodityPlaceholder(
        symbol="GOLDM",
        name="Gold Mini",
        security_id="569003",
        contract_expiry="2026-10-05",
        lot_size=100,  # 100 grams
    ),
    CommodityPlaceholder(
        symbol="SILVERM",
        name="Silver Mini",
        security_id="483080",
        contract_expiry="2026-11-30",
        lot_size=5,  # 5 kg
    ),
    CommodityPlaceholder(
        symbol="CRUDEOILM",
        name="Crude Oil Mini",
        security_id="565900",
        contract_expiry="2026-09-21",
        lot_size=10,  # 10 barrels
    ),
    CommodityPlaceholder(
        symbol="COPPER",
        name="Copper",
        security_id="571298",
        contract_expiry="2026-09-30",
        lot_size=2500,  # 2500 kg
    ),
    CommodityPlaceholder(
        symbol="ZINCMINI",
        name="Zinc Mini",
        security_id="571302",
        contract_expiry="2026-09-30",
        lot_size=1000,  # 1 metric tonne
    ),
    CommodityPlaceholder(
        symbol="NICKEL",
        name="Nickel",
        security_id="571304",
        contract_expiry="2026-09-16",
        # 250 kg effective the Sept-2025 contract revision onward (was 1500 kg
        # before). Applied uniformly across our whole backtest window for now --
        # see docs/technical-debt.md for the pre-revision-date caveat this implies.
        lot_size=250,
    ),
    CommodityPlaceholder(
        symbol="ALUMINI",
        name="Aluminium Mini",
        security_id="571296",
        contract_expiry="2026-09-30",
        lot_size=1000,  # 1 metric tonne
    ),
    CommodityPlaceholder(
        symbol="LEADMINI",
        name="Lead Mini",
        security_id="571299",
        contract_expiry="2026-09-30",
        lot_size=1000,  # 1 metric tonne
    ),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT_ENV_LOCAL),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Dhan credentials / environment ---
    dhan_client_id: str = Field(validation_alias="DHAN_CLIENT_ID")
    dhan_access_token: str = Field(validation_alias="DHAN_ACCESS_TOKEN")
    dhan_env: Literal["sandbox", "production"] = Field(validation_alias="DHAN_ENV")

    # --- Database ---
    database_url: str = Field(validation_alias="DATABASE_URL")

    # --- Trading defaults ---
    default_virtual_capital: float = Field(
        default=500_000, validation_alias="DEFAULT_VIRTUAL_CAPITAL"
    )
    default_polling_interval_seconds: int = Field(
        default=300, validation_alias="DEFAULT_POLLING_INTERVAL_SECONDS"
    )

    # MCX market hours, IST. Weekends-only holiday handling for now -- see
    # growmore_bot/scheduler/market_hours.py for the TODO on a full holiday calendar.
    mcx_market_open: str = "09:00"
    mcx_market_close: str = "23:30"
    mcx_timezone: str = "Asia/Kolkata"

    default_commodity_universe: list[CommodityPlaceholder] = Field(
        default_factory=lambda: list(DEFAULT_COMMODITY_UNIVERSE)
    )


__all__ = ["Settings", "CommodityPlaceholder", "DEFAULT_COMMODITY_UNIVERSE"]
