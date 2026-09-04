"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Silently re-fetches server data on an interval while the tab is visible,
 * so the dashboard stays current without a manual reload. Renders nothing.
 * Paused while the tab is hidden/backgrounded so it never fights a user
 * who's mid-interaction elsewhere, and re-checks immediately on refocus. */
export function AutoRefresh({ intervalMs = 60_000 }: { intervalMs?: number }) {
  const router = useRouter();

  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") {
        router.refresh();
      }
    };

    const interval = setInterval(tick, intervalMs);
    document.addEventListener("visibilitychange", tick);

    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [router, intervalMs]);

  return null;
}
