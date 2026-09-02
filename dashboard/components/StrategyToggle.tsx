"use client";

import { useState, useTransition } from "react";

interface StrategyToggleProps {
  configId: string;
  initialEnabled: boolean;
  onToggle: (id: string, enabled: boolean) => Promise<void>;
}

/**
 * Enable/disable switch for one (strategy, instrument) bot_config row.
 * Optimistically flips, then reconciles if the write fails — this is the
 * dashboard's one real write path against the shared schema.
 */
export function StrategyToggle({ configId, initialEnabled, onToggle }: StrategyToggleProps) {
  const [enabled, setEnabled] = useState(initialEnabled);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function handleClick() {
    const next = !enabled;
    setError(null);
    setEnabled(next);
    startTransition(async () => {
      try {
        await onToggle(configId, next);
      } catch {
        setEnabled(!next);
        setError("Failed to save — try again.");
      }
    });
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label={enabled ? "Disable strategy" : "Enable strategy"}
        disabled={isPending}
        onClick={handleClick}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-60 ${
          enabled ? "bg-[color:var(--series-3)]" : "bg-[color:var(--gridline)]"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
            enabled ? "translate-x-6" : "translate-x-1"
          }`}
        />
      </button>
      <span className="text-xs text-[color:var(--text-secondary)]">
        {isPending ? "Saving…" : enabled ? "Enabled" : "Disabled"}
      </span>
      {error ? <span className="text-xs text-[color:var(--critical-text)]">{error}</span> : null}
    </div>
  );
}
