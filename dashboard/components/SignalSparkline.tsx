import { formatCurrency } from "@/lib/format";
import type { SignalHistoryRow } from "@/lib/types";

/** A short "HOLD HOLD HOLD BUY HOLD"-style strip -- one dot per recent tick,
 * oldest to newest, so the current state's recent context is visible at a
 * glance instead of just the single latest signal. */
export function SignalSparkline({ history }: { history: SignalHistoryRow[] }) {
  if (history.length === 0) return null;

  return (
    <div className="flex items-center gap-1" aria-label="Recent signal history">
      {history.map((row) => (
        <div
          key={row.id}
          title={`${row.action} at ${new Date(row.checked_at).toLocaleString()} (LTP ${formatCurrency(Number(row.ltp))})`}
          className={`h-2 w-2 rounded-full ${
            row.action === "BUY"
              ? "bg-[color:var(--success-text)]"
              : row.action === "SELL"
                ? "bg-[color:var(--critical-text)]"
                : "bg-[color:var(--text-muted)]"
          }`}
        />
      ))}
    </div>
  );
}
