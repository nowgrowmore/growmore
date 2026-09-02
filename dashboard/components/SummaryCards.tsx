import { formatCurrency } from "@/lib/format";
import type { PnlSummary } from "@/lib/format";

interface SummaryCardsProps {
  summary: PnlSummary;
}

function pnlColorClass(value: number): string {
  if (value > 0) return "text-[color:var(--success-text)]";
  if (value < 0) return "text-[color:var(--critical-text)]";
  return "text-[color:var(--text-primary)]";
}

export function SummaryCards({ summary }: SummaryCardsProps) {
  const cards = [
    {
      label: "Open positions",
      value: String(summary.openPositionCount),
      valueClassName: "text-[color:var(--text-primary)]",
    },
    {
      label: "Unrealized P&L",
      value: formatCurrency(summary.totalUnrealizedPnl, { signDisplay: true }),
      valueClassName: pnlColorClass(summary.totalUnrealizedPnl),
    },
    {
      label: "Realized P&L",
      value: formatCurrency(summary.totalRealizedPnl, { signDisplay: true }),
      valueClassName: pnlColorClass(summary.totalRealizedPnl),
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3" role="list" aria-label="P&L summary">
      {cards.map((card) => (
        <div
          key={card.label}
          role="listitem"
          className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4"
        >
          <div className="text-sm text-[color:var(--text-secondary)]">{card.label}</div>
          <div
            className={`mt-1 text-2xl font-semibold tabular-nums ${card.valueClassName}`}
          >
            {card.value}
          </div>
        </div>
      ))}
    </div>
  );
}
