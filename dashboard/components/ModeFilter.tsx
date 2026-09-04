"use client";

interface ModeFilterProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
}

/** Small segmented control for switching between paper/live (and
 * optionally "all") views. Purely client-side filtering of already-fetched
 * data -- no server round trip, no URL state. */
export function ModeFilter<T extends string>({ value, onChange, options }: ModeFilterProps<T>) {
  return (
    <div
      role="tablist"
      className="inline-flex rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-0.5"
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={`rounded-md px-3 py-1 text-sm font-medium transition-colors ${
              active
                ? "bg-[color:var(--gridline)] text-[color:var(--text-primary)]"
                : "text-[color:var(--text-secondary)] hover:text-[color:var(--text-primary)]"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
