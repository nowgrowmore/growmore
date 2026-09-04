import { getAuditLog } from "@/lib/db";

export const dynamic = "force-dynamic";

function formatEventType(eventType: string): string {
  return eventType.replace(/_/g, " ");
}

export default async function AuditPage() {
  const entries = await getAuditLog(200);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Audit log</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Every action that could plausibly matter for a future compliance review — strategy
        enable/disable, risk-guard trips, and more — most recent first.
      </p>

      {entries.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">No audit log entries yet.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {entries.map((entry) => (
            <div
              key={entry.id}
              className="flex flex-col gap-1 rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-[color:var(--gridline)] px-1.5 py-0.5 text-xs font-medium uppercase text-[color:var(--text-secondary)]">
                  {formatEventType(entry.event_type)}
                </span>
                <span className="text-xs text-[color:var(--text-muted)]">
                  {new Date(entry.ts).toLocaleString()}
                </span>
              </div>
              <pre className="overflow-x-auto rounded bg-[color:var(--gridline)]/40 p-2 text-xs text-[color:var(--text-secondary)]">
                {JSON.stringify(entry.payload, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
