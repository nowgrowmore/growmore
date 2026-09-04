"use client";

/** A row of pill toggles, one per strategy name present in the data --
 * lets a page with many bot_config rows or trades across several strategy
 * families narrow down to just the ones of interest, instead of scrolling
 * through everything. Every option is selected by default (equivalent to
 * "no filter"); toggling one off hides just that strategy.
 */
export interface StrategyNameOption {
  value: string;
  label: string;
}

export function StrategyNameFilter({
  options,
  selected,
  onToggle,
  onSelectAll,
  onSelectNone,
}: {
  options: StrategyNameOption[];
  selected: Set<string>;
  onToggle: (value: string) => void;
  onSelectAll: () => void;
  onSelectNone: () => void;
}) {
  if (options.length <= 1) return null;
  const allSelected = selected.size === options.length;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs font-medium text-[color:var(--text-muted)]">Strategy:</span>
      {options.map((opt) => {
        const isOn = selected.has(opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onToggle(opt.value)}
            aria-pressed={isOn}
            className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
              isOn
                ? "border-[color:var(--series-1)] bg-[color:var(--series-1)]/10 text-[color:var(--series-1)]"
                : "border-[color:var(--border-hairline)] text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
      <button
        type="button"
        onClick={allSelected ? onSelectNone : onSelectAll}
        className="ml-1 text-xs text-[color:var(--series-1)] hover:underline"
      >
        {allSelected ? "Select none" : "Select all"}
      </button>
    </div>
  );
}
