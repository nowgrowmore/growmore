# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## What this repo is

A personal-investor MCX commodity trading system: a Python trading engine (`bot/`) that backtests
strategies and runs **paper trading** against Dhan's API, plus a Next.js dashboard (`dashboard/`) on
Vercel/Neon for analytics. See `docs/architecture.md` for the full picture.

## Non-negotiables

- **No real order placement without an explicit live-mode flag.** The bot must default to paper
  trading. Any code path that could call Dhan's Order API must be gated behind a config flag that is
  off by default, and every such call must be logged to `audit_log`.
- **TDD.** Write a failing test before writing the implementation, for every new behavior. This
  matters especially here: a bug in strategy/backtest/risk logic costs real money once live.
- **Run the full local test suite before marking any task done**: `pytest` in `bot/`, and
  `pnpm test` (or `npm test`) in `dashboard/`. CI's diff-scoped runs are not a substitute for the
  full local run.
- **Never commit secrets.** Dhan API keys/tokens and Neon direct connection strings live only in
  gitignored `.env.local` / `.env.test.local`. `.env.test` (checked in) holds only safe local
  defaults. If you ever see a real credential about to be written to a tracked file, stop and flag it.
- **Deploys go through the `vercel:*` skills**, not ad-hoc CLI guesses. Preview deploys happen
  automatically via Vercel's GitHub integration on push — don't try to replicate that manually.
  **Production promotion (`vercel:deploy prod`) requires explicit user confirmation every time** —
  never run it unprompted, even if a task seems to imply "ship it."
- **Keep the docs set current** whenever a change affects it: `docs/architecture.md` (Mermaid
  diagrams), `docs/db-schema.md` (schema/ER), `docs/technical-debt.md` (known shortcuts/deferred
  work), `docs/pending-actions.md` (plain-language items only the account owner can decide/do).

## Scope discipline

- `bot/` and `dashboard/` are independent deployables sharing one Postgres schema. Don't blur that
  boundary — schema changes go through Alembic migrations in `bot/`, and the dashboard only reads
  (plus writes to `bot_config` for enable/disable toggles).
- Only touch files relevant to the task at hand. Don't refactor unrelated code while fixing a bug.
- Stop and report back after 3 repeated failures of the same test/command rather than thrashing.

## Compliance context (why some things are gated)

- SEBI requires every API-placed order to carry an exchange-assigned Algo-ID starting 2026-04-01.
  This project is paper-trading only for now — that requirement is tracked in
  `docs/technical-debt.md` as a gate for a future live-trading phase, not implemented.
- Dhan mandates a static IP for its Order APIs. The bot currently runs on the owner's local machine
  (no static IP) and only calls read-only Data APIs (quotes/historical) — this is intentional, not
  an oversight, and is also tracked in `docs/technical-debt.md`.

## Testing conventions

- Python: `pytest`, unit tests mock the Dhan HTTP layer entirely (no network calls in unit tests);
  integration tests run against a local/dockerized Postgres with Alembic migrations applied.
- Dashboard: unit tests (Vitest) against local/dockerized Postgres; `test:e2e:preview` (Playwright)
  runs only against a real deployed Vercel Preview URL and its real Neon preview branch — not part
  of the fast local loop.
