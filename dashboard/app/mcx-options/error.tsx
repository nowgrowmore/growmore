"use client";

// There was no error boundary ANYWHERE under app/ before the independent code
// review of 2026-09-15, so anything that threw while rendering this route --
// most plausibly a malformed `candidates_considered` JSONB entry, or the
// "cached plan must not change result type" Postgres error that really did
// take this page down on 2026-09-14 after migrations 0023/0024 -- escalated
// to the root and blanked the entire app shell, navigation included.
//
// Scoped deliberately to /mcx-options rather than added at the root: this is
// the route the review was about, and a route-level boundary keeps the rest
// of the dashboard reachable when this one page fails.

export default function MCXOptionsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">MCX Options</h2>
      <div className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4">
        <p className="text-sm font-medium text-[color:var(--critical-text)]">
          This page failed to render.
        </p>
        <p className="mt-2 max-w-2xl text-sm text-[color:var(--text-secondary)]">
          The rest of the dashboard is unaffected. The bot itself is not impacted by a
          dashboard rendering error — it writes its ledger directly to Postgres and never
          reads this page.
        </p>
        {error.digest && (
          <p className="mt-2 text-xs text-[color:var(--text-muted)]">
            Error digest: <code className="rounded bg-[color:var(--gridline)] px-1">{error.digest}</code>
          </p>
        )}
        <button
          type="button"
          onClick={reset}
          className="mt-4 rounded border border-[color:var(--border-hairline)] px-3 py-1.5 text-sm font-medium hover:bg-[color:var(--gridline)]"
        >
          Try again
        </button>
      </div>
    </div>
  );
}
