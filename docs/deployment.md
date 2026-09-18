# Deployment

Three deployable surfaces, three different mechanisms. None of them is `git pull` on a server.

| What | Where | How |
|---|---|---|
| `bot/` | DigitalOcean VPS, Bangalore | `rsync` + `systemctl restart` (below) |
| Database schema | Neon Postgres | `alembic upgrade head`, run **from the Mac** |
| `dashboard/` | Vercel | automatic preview on push; production promotion via `vercel:deploy prod`, **needs explicit confirmation every time** |

---

## The bot → VPS

**Not git-based.** There is no `.git` on the host; code is rsynced. Do not look for a remote to
push to.

- Host: droplet `growmore-bot`, static IP `139.59.72.81` — registered with Dhan to satisfy its
  Order-API static-IP requirement. Dhan locks a registered IP for 7 days, so do not change it
  casually.
- Access: `ssh -i ~/.ssh/growmore_vps growmore@139.59.72.81` (key-only, root login disabled).
- Code: `/home/growmore/growmore/bot`. Secrets: `/home/growmore/growmore/.env.local` — one level
  **above** `bot/`, not inside it.
- `bot/.venv` is Python 3.12 with the package installed **editable**, so syncing `growmore_bot/` is
  sufficient. Only re-install if `pyproject.toml` dependencies changed.
- Only `growmore_bot/` is deployed. `research/` is **not on the host at all** — provisioner scripts
  run from the Mac against `DATABASE_URL`.

```bash
# 1. back up the current tree on the host
ssh -i ~/.ssh/growmore_vps growmore@139.59.72.81 \
  'cd ~/growmore/bot && tar czf ~/growmore_bot_backup_$(date +%Y%m%d_%H%M%S).tar.gz growmore_bot'

# 2. dry run, and actually read it
cd bot && rsync -avn --exclude='__pycache__' --exclude='*.pyc' \
  -e "ssh -i ~/.ssh/growmore_vps" growmore_bot/ growmore@139.59.72.81:~/growmore/bot/growmore_bot/

# 3. same command without -n, then restart
ssh -i ~/.ssh/growmore_vps growmore@139.59.72.81 'sudo systemctl restart growmore-bot'
```

### Verify, in this order

1. **Before restarting**, check imports and that the host's migration chain agrees with the
   database — a host missing a migration file will disagree with Neon and fail confusingly later:
   ```bash
   ssh ... 'cd ~/growmore/bot && set -a && . ../.env.local && set +a && .venv/bin/python -m alembic current'
   ```
2. `systemctl is-active growmore-bot`.
3. `tail -30 ~/growmore/bot/bot.log`. **Application logs go to `bot.log`, not journald** —
   `journalctl -u growmore-bot` shows only systemd's own start/stop lines, so an empty journal
   after a restart means nothing is wrong. Confirm every expected
   `Added job "start.<locals>._..."` line is present: a newly added cron job silently missing is
   the failure mode to look for.
4. Integrity — compare digests rather than assuming rsync did what the dry run said:
   ```bash
   find growmore_bot -name '*.py' -not -path '*__pycache__*' | sort | xargs md5sum
   ```

### Scheduled jobs the service registers

`_job` (5-min tick), `_wheel_basket_job` (15:45 IST), `_mcx_options_entry_job` (09:15 IST) and
`_mcx_options_job` (23:59 IST). The last two are two halves of one strategy — see
`docs/architecture.md`. A change that touches only one of them will look like it works while
silently breaking the other half.

---

## Schema → Neon

Migrations and config provisioning run **from the Mac**, never from the VPS.

```bash
cd bot && set -a && . ../.env.local && set +a
.venv/bin/python -m alembic upgrade head
```

**Always dry-run a migration's preconditions first** — query for the rows it would delete or the
constraints it would add. Several MCX-options migrations de-duplicate rows before adding a unique
constraint, and `0026` really did delete 4 production rows. Back affected rows up to a file
**outside the repo** first.

---

## The Dhan access token

**Dhan allows one active session per account, and the VPS owns it.** It refreshes its own token on
every service start (24h expiry).

- **Never generate a token on the Mac while the bot is running.** That mints a second session and
  silently kills the VPS's live one. This has happened — see `docs/technical-debt.md`.
- *Using* the same token from both machines is fine; read-only Data API calls coexist.
- To refresh the Mac's copy, copy `DHAN_ACCESS_TOKEN` from the VPS's `.env.local` into the local
  one. Pipe it directly; never print a token to a terminal transcript, and verify by comparing
  `shasum -a 256` of the two values rather than eyeballing them.
- **Never write a backup like `.env.local.bak`** into the repo — `.gitignore` covers `.env.local`
  and `.env.*.local`, and a `.env*.bak*` rule was added in 2026-09-18 after exactly that mistake
  left a live token untracked in the working tree. Keep backups outside the repo regardless.
