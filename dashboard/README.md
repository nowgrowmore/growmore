# Growmore dashboard

Next.js (App Router) analytics dashboard for the Growmore MCX paper-trading bot. Reads directly
from the shared Neon Postgres schema; its only write path is toggling `bot_config.enabled` and
editing a config's risk params (`max_position_size`, `daily_loss_limit`, `virtual_capital`).

**Schema/migrations are owned by `bot/` (Alembic).** This app never creates or alters tables — see
`../docs/db-schema.md` for the source of truth on columns, and `../docs/architecture.md` for how
this fits into the rest of the system.

## Install

```bash
pnpm install
```

## Configure

Copy `.env.example` to `.env.local` and set `DATABASE_URL` to your Neon connection string (get it
from the Neon project the bot uses). `.env.local` is gitignored — never commit a real connection
string.

For local test/dev against a local Postgres, `.env.test` (checked in) already points at
`postgresql://postgres:postgres@localhost:5432/growmore_test` — the same local DB the bot's
integration tests use, since both apps share one schema. Override it with a gitignored
`.env.test.local` if you need different local credentials; that file is never created
automatically.

## Run the dev server

```bash
pnpm dev
```

Requires a reachable Postgres with the bot's Alembic migrations applied — this app has no seed/
migration tooling of its own.

## Tests

Unit tests (Vitest + Testing Library) mock the DB layer entirely — no live Postgres connection is
needed:

```bash
pnpm test          # run once
pnpm test:watch    # watch mode
```

Also run before considering any change done:

```bash
pnpm typecheck
pnpm lint
```

### Preview smoke suite (Playwright)

`e2e/preview-smoke.spec.ts` is **not** part of the fast local loop. It only makes sense against a
real deployed Vercel Preview URL (with its own real Neon preview branch per
`../docs/architecture.md`), so it needs `PREVIEW_BASE_URL` set:

```bash
PREVIEW_BASE_URL=https://growmore-dashboard-<hash>.vercel.app pnpm test:e2e:preview
```

Running it without `PREVIEW_BASE_URL` set skips every test rather than failing or trying to boot a
local server.

## Pages

- `/` — Overview: live paper P&L summary cards and a cumulative realized-P&L trend across all open
  positions.
- `/backtests` — sortable/filterable table of backtest runs (Sharpe, max drawdown, win rate, profit
  factor, CAGR) — the review step before enabling a strategy live.
- `/trades` — trade log from `paper_orders`/`paper_positions`.
- `/strategies` — `bot_config` rows with an enable/disable toggle and an editable risk-params form.
  This is the one write path; both actions in `app/strategies/actions.ts` also append to
  `audit_log`.

## Known follow-ups for a human

- **Chart library**: `recharts` was picked as the "simple, themeable" default per the task brief.
  It has not been run through the dataviz skill's `validate_palette.js` / full review pass in this
  session — worth a follow-up pass if charts expand beyond the single equity-curve line chart here.
- **Vercel/Neon linking**: no Vercel project or Neon project is linked yet. Provisioning that (and
  wiring the Vercel↔Neon Preview-branch integration described in `../docs/architecture.md`) is a
  separate step, via the `vercel:*` skills — not done as part of building this app.
- **No live DB in this environment**: all reads/writes were built directly against
  `../docs/db-schema.md` and verified with mocked unit tests only. First run against a real
  Postgres (with the bot's Alembic migrations applied) should be treated as the real integration
  test.
