# Technical Debt / Known Limitations

- **(Fixed 2026-09-04) Systemic repeated-signal bug across all crossing-based strategies.** The
  scheduler rebuilds a fresh strategy instance every tick and warms it up from history ending
  yesterday (`_warm_up_strategy`) — so a strategy's "previous value" for crossing/threshold-recovery
  detection was always yesterday's close, not the last time it was actually checked. A signal meant
  to fire exactly once at a real crossing instead re-fired on every tick for the rest of the day the
  live value stayed past the threshold. Found via the real Aluminium Mini live position's unrealized
  P&L staying stuck at 0 — the strategy kept re-signalling BUY (correctly rejected by the
  `max_position_size=1` guard, but that rejection path skips mark-to-market) instead of reporting
  HOLD. This was a real risk for any config with `max_position_size > 1` — a repeated erroneous BUY
  would have placed additional real orders each tick, not just been rejected. Fixed with
  `Strategy.get_state_snapshot()`/`load_state_snapshot()` — `macd_trend`, `rsi_mean_reversion`,
  `sma_crossover`, and `bollinger_reversion` now persist/restore their crossing reference via a new
  `bot_signal_state.crossing_state` column, restored by the scheduler right after warm-up.
  `donchian_breakout` deliberately left alone — not currently used by any config, and has a
  genuinely different (non-crossing, threshold-breach) design that shouldn't be changed without its
  own separate review (changing it would also retroactively alter the semantics behind the already-
  published `docs/backtest-results.md` sweep, which used the old behavior).
- **(Investigated + throttled 2026-09-04) Repeated `max_position_size_rejected` audit_log entries
  are a real signal, not a recurrence of the fixed repeated-signal bug above.** Noticed via the
  dashboard's audit log showing the same rejection for ALUMINI (live) and COPPER (paper) roughly
  every 5-minute tick for ~50 minutes around MCX's 2026-09-04 market open. Root-caused by checking
  each strategy's own crossing state (`bot_signal_state.crossing_state`) directly against the real
  DB: both were genuinely, repeatedly crossing their threshold (MACD/signal spread, RSI vs. 30) due
  to real intraday price noise right at the boundary — each rejection really was a fresh BUY signal,
  correctly rejected because a position was already open. `RsiMeanReversionStrategy`/`MacdTrendStrategy`
  don't consult `position_state` at all (by design — signal generation is separate from position
  sizing, per `Strategy.on_bar`'s contract), so they have no way to know not to re-signal BUY while
  already holding; the `max_position_size` guard is exactly the intended safety net for this. Not a
  bug, but genuinely low marginal audit-log value once already recorded once recently — throttled to
  one audit_log write per 30 minutes per config via a new
  `bot_signal_state.last_max_position_rejection_logged_at` column (`bot.log`'s own warning is
  unaffected, still written every single tick).
- **(2026-09-04) First real live-trading attempt: rejected safely, root-caused, fixed.** With the
  account owner's explicit go-ahead, Aluminium Mini's MACD (12,26,9) config was switched to
  `mode="live"` and `LIVE_TRADING_ENABLED=true` was set on the VPS. The very next tick placed a real
  BUY order attempt — Dhan rejected it with `DH-905 Invalid IP`. **Root cause**: the droplet has real
  IPv6 connectivity, and `api.dhan.co` resolves to both IPv4 and IPv6 — the actual outbound HTTPS
  connection went out over IPv6, which doesn't match the IPv4 (`139.59.72.81`) registered with Dhan.
  **Fixed** by disabling IPv6 system-wide on the droplet (`/etc/sysctl.d/99-disable-ipv6.conf`),
  verified afterward with `curl -w 'local_ip=%{local_ip}'` against Dhan's own API host showing the
  correct registered IP. A second, more serious bug surfaced by the same failure: the order client's
  own `live_order_failed` audit_log entry was being silently discarded, because nothing caught the
  exception before it reached `session_scope()`'s rollback-on-any-exception handler — the one moment
  an audit trail matters most (a failed real order) left no trace at all. Fixed by catching
  order-placement failures at each call site in `live/engine.py` instead of letting them propagate.
  **No real order was ever actually placed** — Dhan rejected it before any money moved.
  `LIVE_TRADING_ENABLED` was immediately set back to `false` once the failure was seen, pending both
  fixes; re-arming is a deliberate separate step, not automatic.
- **(Built 2026-09-04, still OFF) Real order placement now exists but is gated behind two
  independent switches, both required.** `growmore_bot/broker/dhan_order_client.py` is the only
  module allowed to call Dhan's Order API (schema verified against the installed `dhanhq` SDK's own
  source — `exchangeSegment="MCX_COMM"`, `productType="MARGIN"` for carry-forward, `orderType=
  "MARKET"` — not guessed, and not the conflicting "MCX_FO" a scraped doc page claimed). Every call
  requires `Settings().live_trading_enabled` (env `LIVE_TRADING_ENABLED`, off by default) AND the
  specific `bot_config.mode == "live"` (new column, defaults `"paper"` for every existing row, no
  dashboard UI to change it — must be set directly in the database, deliberately, so it's never an
  accidental click). `growmore_bot/live/engine.py` mirrors `PaperTradingEngine`'s interface and risk
  guards (max_position_size, daily_loss_limit, pre-expiry close-out) exactly, persisting to new
  `live_positions`/`live_orders` tables (never `paper_positions`/`paper_orders`, so real and
  simulated data can never mix). Full TDD coverage, all mocked (no real order was ever placed while
  building this). Known gaps, left as gaps rather than silently papered over:
  1. **(Fixed 2026-09-04) Fill reconciliation now corrects position-level P&L too, not just the
     order's own record.** A `MARKET` order's placement response only carries an order ID and an
     initial status (e.g. `"TRANSIT"`), not a confirmed fill price.
     `LiveTradingEngine.reconcile_pending_orders()` polls Dhan's `get_order_by_id` once per tick for
     every `live_orders` row still in a non-terminal status and corrects that row's
     `order_status`/`fill_price` to Dhan's real values. When the real fill price differs from the
     approximate live-quote LTP used at placement, `_retroactively_correct_position` now ripples that
     correction into the position too: for a SELL, the order's own `pnl`/old fill price let the
     avg_entry_price used at that sale be back-solved exactly, so the corrected pnl (and the delta
     applied to `realized_pnl`) doesn't need to know the position's *current* avg_entry_price, which
     may have moved since. For a BUY, avg_entry_price is only recomputed when it's safe to do exactly
     — the position is still open and has never had a sell against it — as the quantity-weighted
     average of every buy order's (corrected) fill price; once any sell has happened, exactly
     correcting cost basis needs per-lot tracking this engine doesn't have, so it's logged for manual
     review instead of guessed at. Also worth noting: unlike `place_market_order`'s request schema
     (verified against the `dhanhq` SDK's own source), the response field names this relies on
     (`orderStatus`, `averageTradedPrice`) are only documented, not independently verified against a
     live response beyond the one real order placed so far.
  2. **(Fixed 2026-09-04) Daily-loss-limit trip on a live config now attempts to auto-close the real
     position, and retries with backoff if that fails.** `LiveTradingEngine._trip_daily_loss_guard`
     places a real closing SELL for whatever's open before disabling the config, and audit-logs
     `auto_close_attempted`/`auto_close_succeeded`/`auto_close_pnl` either way. If the closing order
     itself fails (caught, never raised), the config is marked `pending_auto_close` with a geometric
     backoff schedule (5, 10, 20, 40... minutes, capped at 60, never gives up on its own) —
     `run_all_enabled_configs` retries it every tick via `LiveTradingEngine.retry_pending_auto_close`,
     DELIBERATELY outside the `enabled=True` filter that gates everything else, since the whole point
     is retrying a position close for a config that's already disabled. A successful retry never
     re-enables the config for fresh trades, only flattens the position; each attempt (success or
     failure) writes its own `live_auto_close_retry_succeeded`/`live_auto_close_retry_failed` audit
     entry. Applied the original (non-retrying) auto-close fix to `PaperTradingEngine` too, for
     consistency/realism, even though a failed *simulated* close carries no real risk.
  3. **Dashboard doesn't read `live_positions`/`live_orders` or expose `bot_config.mode` at all
     yet** — not urgent while `live_trading_enabled` stays False, but needed before this is ever
     actually used, so real activity is visible somewhere.
  Still blocked on the items below (static IP) before this could ever safely be turned on for real —
  see `docs/pending-actions.md` for the activation checklist. The 2FA/OAuth session-requirements
  question is now resolved (see below, under SEBI Algo-ID) — not a blocker.
- **(Done 2026-09-04) Bot moved off the local machine, onto a DigitalOcean VPS** — droplet
  `growmore-bot` (1 vCPU/1GB, Ubuntu 24.04, Bangalore `blr1`, public IP `139.59.72.81`), hardened
  (key-only SSH as a non-root `growmore` user, root login + password auth disabled, `ufw` allowing
  only SSH, `fail2ban` enabled), running the bot as a systemd service (`growmore-bot.service`,
  auto-restarts on crash/reboot). The laptop's own bot process was stopped at the same time — running
  two instances against the shared Neon database simultaneously would double up paper trades. Still
  paper-trading only; nothing about trading behavior changed, this only solves the hosting/IP problem.
  **(Done 2026-09-04) IP registered with Dhan** — the droplet's IP (`139.59.72.81`) is now whitelisted
  on the Dhan account, so the static-IP requirement is actually satisfied, not just theoretically
  possible. Locked for 7 days from registration (~2026-09-11) per Dhan's own policy.
- **No SEBI Algo-ID handling.** Not needed for paper trading (we never call the Order API). Verified
  2026-09-04 that this is a much smaller lift than first assumed: SEBI's framework exempts self-built
  "White Box" strategies (logic transparent to the owner, not sold to others — this bot qualifies)
  from formal exchange strategy registration as long as order rate stays under **10 orders/second per
  exchange per client** — this bot polls every 5 minutes, nowhere near that threshold, so no
  multi-week exchange-approval process is expected to apply before live trading. What DOES still
  apply regardless of the exemption: a static IP whitelisted with the broker (see the item above —
  same underlying requirement, now confirmed to be part of this SEBI framework too, not just a
  Dhan-specific policy), 2FA on every session, and broker-side order tagging/audit logging (the bot
  already keeps its own `audit_log` table and `bot.log`, which should cover the "keep audit-ready
  logs" expectation). **(Resolved 2026-09-04)** the 2FA/OAuth question specifically, checked directly
  against Dhan's own authentication docs: Dhan documents TWO sanctioned ways to get an access token —
  a full OAuth App ID/Secret browser-consent flow (needs a public HTTPS redirect URL we don't have),
  or **programmatic generation via client ID + PIN + a live TOTP code** — exactly what
  `token_refresh.py` already does. This isn't a workaround around Dhan's 2FA requirement, it IS Dhan's
  own documented headless 2FA mechanism (the PIN+TOTP pair together constitute the two factors). Dhan's
  docs also confirm there's no additional per-API-call session requirement beyond the 24h token
  itself, and static IP whitelisting only gates Order Placement APIs specifically (not Data APIs) —
  matches everything already built. One new operational detail worth remembering: once static IPs are
  registered with Dhan, **they can't be changed for 7 days** — worth confirming the VPS provider/IP
  before registering it, not after. **(Fixed 2026-09-04)** the "automatic session reset before each
  trading day" expectation specifically: `scheduler/run.py`'s `start()` now forces a fresh Dhan
  session via `token_refresh.refresh_if_needed(force=True)` once per IST calendar day
  (`token_refresh.is_new_trading_day`), independent of how much validity the current token has left —
  previously it only ever refreshed reactively, within 2 hours of expiry.
- **Dhan sandbox not used for market data.** Confirmed via Dhan docs that sandbox fills all orders
  at a fixed ₹100 and does not provide real quotes — unsuitable for realistic paper-trade
  simulation. We use the production Data API (read-only) for real prices instead; see
  `docs/pending-actions.md` for the credential this requires.
- **Single point of failure.** The bot is a single process with no failover/redundancy. If it
  crashes mid-session, in-flight paper positions are whatever was last persisted — no reconciliation
  logic exists yet.
- **REST polling, not WebSocket.** Live quotes are fetched via polling (default ~5min) rather than
  Dhan's WebSocket feed. Sufficient for the target "not HFT" cadence; revisit if strategies need
  finer-grained data.
- **Backtests use REST-fetched historical data with no local caching layer yet** beyond what's
  persisted per run — repeated backtests re-fetch from Dhan. Acceptable at current data volumes
  (2-4 instruments); revisit if this expands.
- **No reconciliation between paper positions and any real broker state** — by design, since no
  real orders are placed, but this means the moment live trading is introduced, a reconciliation
  layer must be built from scratch.
- **(Fixed 2026-09-03, but worth remembering) A fresh strategy instance used to be built every
  scheduler tick with zero warm-up.** `run_all_enabled_configs` deliberately keeps the scheduler
  stateless — position state comes from `paper_positions`, not memory — but that meant every
  strategy's indicator state (fast/slow EMAs, RSI averages, Donchian channel, etc.) was also
  rebuilt from nothing every 5 minutes and fed exactly one live price before being discarded. Since
  every one of the 5 strategies needs multiple bars of history before it can even compute a value
  (MACD needs 13+), **the bot could have run indefinitely — sleep or no sleep — and never generated
  a single real trade signal.** Found while running it for real for the first time. Fixed by
  `_warm_up_strategy` (`bot/growmore_bot/scheduler/run.py`): each tick now fetches ~150 days of
  real historical daily bars and replays them into the fresh strategy before evaluating the live
  quote as today's still-forming bar. This also means the bot's readiness is independent of process
  uptime — a restart after any length of sleep warms up identically from real history.
  **Known follow-up, not yet done**: this refetches ~150 days of daily bars from Dhan on every tick
  for every enabled config — correct, but wasteful (the historical portion barely changes
  intraday). Caching per calendar day would cut this dramatically; not needed at today's scale
  (one bot_config, ticks every 5 minutes), worth doing before enabling several strategies at once.
- **Preview deployments share the same Neon database as production, and it now holds real data.**
  The Vercel↔Neon marketplace integration was installed without enabling "create a branch per
  preview deployment," so `DATABASE_URL` resolves to the same database for
  Development/Preview/Production. This stopped being a purely theoretical risk on 2026-09-03: the
  real 5-year backtest run (6 backtest_runs, 184 backtest_trades, 6,754 equity_curve_points) is now
  persisted in that one shared database. A preview deploy that writes test data, or a migration
  applied against the wrong environment, can now corrupt or delete real results. Fix — enable
  per-branch Neon databases — before running more backtests or enabling paper trading.
- **Historical data actually does go back the full 5 years Dhan advertises**, per the current
  front-month contract's security ID (verified 2026-09-03: 1,263 daily bars, 2021-09 to 2026-09,
  for Gold Mini's security ID `569003`) — an earlier note here wrongly concluded it was capped at
  ~1 year, from only having tested a 365-day request. The large single-day moves that initially
  looked like a possible data-splice artifact (checked 2026-09-03) turned out to be real, confirmed
  market events (the Jan 30, 2026 gold/silver crash and the Apr 2026 oil crash) — not a data quality
  issue. `default_commodity_universe` in `bot/growmore_bot/config.py` holds real security IDs for the
  current front-month contracts (looked up 2026-09-03) instead of placeholders; these **will need
  updating at each contract roll** regardless of how far back history goes.
- **Market-hours gating (`bot/growmore_bot/scheduler/market_hours.py`) is close to the real MCX
  calendar but not complete.** Weekday + a holiday list + a seasonal close-time shift are now
  handled; two gaps remain (#3 below is fixed, #4 is not):
  1. **(Fixed 2026-09-03) No MCX holiday calendar.** `MCX_HOLIDAYS_2026` now hardcodes the 5
     unambiguous full-closure 2026 holidays (New Year's Day, Republic Day, Good Friday, Gandhi
     Jayanti, Christmas), sourced from Groww's MCX 2026 holiday list, checked 2026-09-03. Deliberately
     does NOT include partial-session holidays (e.g. Holi, Ganesh Chaturthi — some sources describe
     these as "morning closed, evening open," but the exact session boundaries aren't clearly
     documented, and encoding them wrong risks blocking real trading hours, which is worse than the
     status quo of polling needlessly on those days — harmless, since there's no real data moving on
     Dhan's side either). **This list needs a fresh lookup every year** — same maintenance pattern as
     `config.DEFAULT_COMMODITY_UNIVERSE`'s contract expiries — and Dhan's API was never checked for
     a native exchange-holiday endpoint that might avoid hand-maintaining this.
  2. **(Fixed 2026-09-03) The 11:30 PM close didn't account for MCX's seasonal shift to 11:55 PM
     IST.** Now computed per-year directly from the real rule (verified against ICICI Direct's
     coverage of the 2026-03-09 change): MCX closes non-agri commodities at 23:30 IST while the US
     observes daylight saving time (2nd Sunday of March through the day before the 1st Sunday of
     November), 23:55 IST the rest of the year — purely to preserve the same overlap window with US
     markets. `_nth_sunday_of_month()` computes the boundary dates from the DST rule itself, not a
     hardcoded per-year date, so this doesn't need annual maintenance the way the holiday list does.
  3. **(Fixed 2026-09-03) No handling of a contract's last trading day / delivery risk.**
     `is_market_open()` still doesn't know about `contract_expiry`, but this no longer matters for
     the risk it actually posed: `growmore_bot/scheduler/contract_rollover.py` now computes a
     close-out cutoff per instrument (verified against Dhan's real Risk Management Policy — bullion
     gets force-squared-off ~8 trading days before expiry, base metals ~6, both well ahead of Dhan's
     own forced square-off; Crude Oil Mini is cash-settled and gets no forced close-out, matching
     Dhan's own behavior) and `run_all_enabled_configs` force-closes any open position and skips
     strategy evaluation entirely (no fresh entries) for a config past that cutoff. **(Fixed
     2026-09-04) Rolling to the next contract month is now automatic**, attempted on every tick a
     config is past its cutoff: `growmore_bot/broker/instrument_master.py` downloads Dhan's real
     public instrument master CSV (schema confirmed against a live file 2026-09-04) and
     `contract_rollover.roll_to_next_contract()` finds the immediate next MCX FUTCOM contract for
     that symbol, **validates it with a real live quote request before committing anything**, and
     only then updates that one `instruments` row's `security_id`/`contract_expiry` in place (writing
     an audit_log `contract_rolled` entry) — `bot_config` keeps the same `instrument_id` FK
     throughout, so existing enabled configs resume trading automatically, no other change needed.
     Refuses to guess and falls back to the pre-existing manual process (someone looks up the next
     `security_id` by hand and updates the row) whenever the match is ambiguous, the fetch fails, or
     the candidate's quote looks implausible — logged as a warning either way. Known minor
     inefficiency: refetches the ~200k-line CSV every tick while stuck in this fallback state, which
     only happens during the few days between hitting cutoff and a successful roll — not worth
     caching given how rarely and briefly this occurs.
  4. **No handling of MCX special/shortened sessions** (e.g. Muhurat trading, circular-announced
     early closures for specific events) — these aren't predictable from a weekday+holiday-list
     check alone and would need to be sourced from MCX's own circulars if this ever matters for a
     specific date.
  None of this affects backtesting (Dhan's historical data already only contains real trading days),
  only the live scheduler's day/time gating. #1, #2, and #3 are now fixed; #4 remains and is lower
  priority (rare, and mostly relevant once real order placement exists).
- **(Fixed 2026-09-03) `unrealized_pnl` was never marked to market for open
  positions.** `PaperTradingEngine._handle_buy` wrote `unrealized_pnl=0` once
  at position open and `_handle_sell` reset it to `0` again on close
  (`bot/growmore_bot/paper/engine.py`), but nothing ever recomputed it in
  between -- so every open position showed ₹0.00 unrealized P&L on the
  dashboard no matter how far the real price had moved, while realized P&L
  (only ever written on a closing sell fill) was correct. Found via the
  dashboard looking visibly wrong while an `always_flip` demo position was
  open. Fixed by marking the open position to market against the tick's real
  quote on every HOLD (the common case, previously not touched at all), and
  by recomputing it after any partial buy/sell that changes quantity or
  average entry price (`PaperTradingEngine._mark_to_market`).
- **(Resolved 2026-09-04 by decision, not by fixing production) Production dashboard has no access
  control, and that's no longer being worked around — `main` is just never promoted.** The dashboard
  shows trading data and has a real write path (enable/disable strategies, edit risk limits via
  `bot_config`). Vercel Authentication (SSO) protects Preview deployments on the Hobby plan (confirmed
  via a real incognito-mode test — an unauthenticated request gets challenged); extending that same
  protection to Production requires a paid Vercel plan — attempted via API and confirmed blocked:
  `"Vercel Authentication is not available on your plan for production deployments"` (the
  `beautifulforce` team is on Hobby). Rather than upgrade to Pro for this, the decision (2026-09-04) is
  to keep the dashboard permanently on the `live` branch's Preview URL — stable across pushes, already
  protected by the same Vercel Authentication — and never promote to `main`/production at all. Access
  is gated by `beautifulforce` Vercel team membership; see `docs/pending-actions.md` to confirm who's
  on it. `CLAUDE.md`'s "production promotion requires explicit confirmation every time" rule stays in
  force as a backstop regardless.
