"use client";

import { useState, useTransition } from "react";

interface RiskParamsFormProps {
  configId: string;
  maxPositionSize: number;
  dailyLossLimit: number;
  dailyLossLimitEnabled: boolean;
  action: (id: string, formData: FormData) => Promise<void>;
}

/** Inline editable risk params for one bot_config row. */
export function RiskParamsForm({
  configId,
  maxPositionSize,
  dailyLossLimit,
  dailyLossLimitEnabled,
  action,
}: RiskParamsFormProps) {
  const [isPending, startTransition] = useTransition();
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(formData: FormData) {
    setSaved(false);
    setError(null);
    startTransition(async () => {
      try {
        await action(configId, formData);
        setSaved(true);
      } catch {
        setError("Failed to save risk params.");
      }
    });
  }

  return (
    <form action={handleSubmit} className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col text-xs text-[color:var(--text-secondary)]">
        Max position size
        <input
          type="number"
          step="any"
          name="maxPositionSize"
          defaultValue={maxPositionSize}
          className="mt-1 w-32 rounded border border-[color:var(--border-hairline)] bg-transparent px-2 py-1 text-sm tabular-nums"
        />
      </label>
      <label className="flex flex-col text-xs text-[color:var(--text-secondary)]">
        Daily loss limit
        <input
          type="number"
          step="any"
          name="dailyLossLimit"
          defaultValue={dailyLossLimit}
          className="mt-1 w-32 rounded border border-[color:var(--border-hairline)] bg-transparent px-2 py-1 text-sm tabular-nums"
        />
      </label>
      <label className="flex items-center gap-1.5 pb-1.5 text-xs text-[color:var(--text-secondary)]">
        <input
          type="checkbox"
          name="dailyLossLimitEnabled"
          defaultChecked={dailyLossLimitEnabled}
          className="h-3.5 w-3.5 rounded border-[color:var(--border-hairline)]"
        />
        Daily loss limit guard
      </label>
      <button
        type="submit"
        disabled={isPending}
        className="rounded bg-[color:var(--series-1)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
      >
        {isPending ? "Saving…" : "Save"}
      </button>
      {saved ? (
        <span className="text-xs text-[color:var(--success-text)]">Saved</span>
      ) : null}
      {error ? <span className="text-xs text-[color:var(--critical-text)]">{error}</span> : null}
    </form>
  );
}
