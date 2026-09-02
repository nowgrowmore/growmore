# growmore-bot

Python trading engine for the MCX-commodity paper-trading bot: pulls quotes /
historical OHLC from Dhan's Data API (`dhanhq` SDK), backtests strategies,
and runs a paper-trading loop with simulated fills. **It never calls Dhan's
Order API** -- see `docs/architecture.md` and `CLAUDE.md` at the repo root
for the full rationale (no static IP yet, no SEBI Algo-ID handling yet).

## Install

From this directory (`bot/`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python >=3.10.

## Configuration

The bot reads its configuration from environment variables (see
`growmore_bot/config.py`):

- `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `DHAN_ENV` (`sandbox`|`production`)
- `DATABASE_URL` (the shared Neon Postgres connection string)

**Real credentials live only in the repo-root `.env.local`, which already
exists and is gitignored.** Do not print or commit its contents. `Settings()`
loads from `.env.local` automatically when run from the `bot/` directory with
a real `.env.local` present, or you can `export` the variables yourself.

`.env.test` (in this directory) is checked into git and holds only safe
placeholder values (a fake Dhan token, a local Postgres URL) so the test
suite can run without real secrets.

`growmore_bot/config.py` also ships default trading parameters: ₹5,00,000
virtual capital, a 5-minute polling interval, MCX market hours
(09:00-23:30 IST), and a default commodity universe (Gold Mini, Silver Mini,
Crude Oil Mini, Natural Gas). **The Dhan security IDs for these instruments
are placeholders (`TODO_LOOKUP_DHAN_SECURITY_ID`)** -- MCX contracts roll by
expiry month, so real security IDs must be looked up from Dhan's instrument/
scrip master (https://images.dhan.co/api-data/api-scrip-master.csv) and
inserted into the `instruments` table before the bot can fetch real data.
Don't guess these.

## Running tests

```bash
pytest
```

Unit tests (`tests/unit/`) never make real network or database calls --
Dhan's HTTP layer is mocked with `responses`, and DB-touching tests use an
in-memory SQLite engine or a fully mocked SQLAlchemy session.

Integration tests (`tests/integration/`) need a real Postgres reachable via
`DATABASE_URL`; they run Alembic migrations against it, execute a small
backtest against a fixture CSV, and check the resulting rows. If Postgres
isn't reachable, they **skip** with a clear message rather than failing the
whole suite.

### Local Postgres for integration tests

Simplest option, Docker:

```bash
docker run -d --name growmore-test-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
createdb -h localhost -U postgres growmore_test
```

Then either rely on the default in `.env.test`
(`postgresql+psycopg://postgres:postgres@localhost:5432/growmore_test`) or export
your own `DATABASE_URL` before running `pytest`.

## Database migrations (Alembic)

Migrations live in `growmore_bot/persistence/migrations/`. `bot/` owns the
schema; the dashboard only reads it (plus writes `bot_config` toggles).

```bash
# apply all migrations
alembic upgrade head

# create a new migration after changing growmore_bot/persistence/models.py
alembic revision -m "describe the change"
```

`DATABASE_URL` from the environment takes precedence over the placeholder in
`alembic.ini`.

## Running the bot locally

```bash
python -m growmore_bot.main
# or, after `pip install -e .`:
growmore-bot
```

This starts an APScheduler polling loop (default every 5 minutes) that only
does anything during MCX market hours (09:00-23:30 IST, weekdays --
`growmore_bot/scheduler/market_hours.py` has a TODO to add a real holiday
calendar). Each enabled row in `bot_config` gets fetched a live quote and fed
to its strategy; a live signal simulates a fill at the fetched LTP. This is
**paper trading only** -- no real orders are ever placed.

## Running a backtest

```bash
python -m growmore_bot.backtest.run_all --from-date 2023-01-01 --to-date 2024-01-01
```

Runs every built-in strategy against every row in the `instruments` table,
persists results to `backtest_runs` / `backtest_trades` /
`equity_curve_points`, and prints a table ranked by Sharpe ratio (descending)
with drawdowns over the guardrail (`--max-drawdown-guardrail`, default 50%)
flagged for a human to review before enabling anything in `bot_config`.

## Linting / type-checking

```bash
ruff check .
mypy growmore_bot
```
