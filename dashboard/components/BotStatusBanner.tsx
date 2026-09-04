import { formatCurrency, timeAgo, toNumber } from "@/lib/format";
import type { BotStatus } from "@/lib/types";

/** Global armed/disarmed + health strip shown on every page (mounted in the
 * root layout). `status` is null until the scheduler has ticked at least
 * once (bot_status is a singleton upserted every tick, see
 * scheduler/run.py::_update_bot_status). */
export function BotStatusBanner({ status }: { status: BotStatus | null }) {
  if (!status) {
    return (
      <div className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] px-4 py-2 text-xs text-[color:var(--text-muted)]">
        Bot has not reported in yet — no bot_status row.
      </div>
    );
  }

  const staleMs = Date.now() - new Date(status.last_tick_at).getTime();
  const isStale = staleMs > 5 * 60_000; // scheduler ticks far more often than this during market hours

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] px-4 py-2 text-xs">
      <span className="flex items-center gap-1.5 font-medium">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            status.live_trading_enabled ? "bg-[color:var(--critical-text)]" : "bg-[color:var(--text-muted)]"
          }`}
          aria-hidden
        />
        <span
          className={
            status.live_trading_enabled
              ? "text-[color:var(--critical-text)]"
              : "text-[color:var(--text-secondary)]"
          }
        >
          {status.live_trading_enabled ? "LIVE TRADING ARMED" : "Live trading disarmed"}
        </span>
      </span>

      <span
        className={isStale ? "font-medium text-[color:var(--critical-text)]" : "text-[color:var(--text-secondary)]"}
      >
        Last tick: {timeAgo(status.last_tick_at)}
        {isStale ? " — stale" : ""}
      </span>

      {status.available_balance !== null && (
        <span className="text-[color:var(--text-secondary)]">
          Available: <span className="tabular-nums">{formatCurrency(toNumber(status.available_balance))}</span>
        </span>
      )}
      {status.utilized_margin !== null && (
        <span className="text-[color:var(--text-secondary)]">
          Utilized margin: <span className="tabular-nums">{formatCurrency(toNumber(status.utilized_margin))}</span>
        </span>
      )}
    </div>
  );
}
