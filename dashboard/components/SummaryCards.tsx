import { formatCurrency, formatPercent } from "@/lib/format";
import type { PnlSummary } from "@/lib/format";

interface SummaryCardsProps {
  summary: PnlSummary;
}

function pnlColorClass(value: number): string {
  if (value > 0) return "text-[color:var(--success-text)]";
  if (value < 0) return "text-[color:var(--critical-text)]";
  return "text-[color:var(--text-primary)]";
}

function renderGroup(cards: { label: string; value: string; valueClassName: string }[]) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4" role="list" aria-label="P&L summary">
      {cards.map((card) => (
        <div
          key={card.label}
          role="listitem"
          className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4"
        >
          <div className="text-sm text-[color:var(--text-secondary)]">{card.label}</div>
          <div className={`mt-1 text-2xl font-semibold tabular-nums ${card.valueClassName}`}>
            {card.value}
          </div>
        </div>
      ))}
    </div>
  );
}

export function SummaryCards({ summary }: SummaryCardsProps) {
  const topRow = [
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
    {
      label: "Net P&L",
      value: formatCurrency(summary.netPnl, { signDisplay: true }),
      valueClassName: pnlColorClass(summary.netPnl),
    },
  ];

  const performanceRow = [
    {
      label: "Closed trades",
      value: String(summary.closedTradeCount),
      valueClassName: "text-[color:var(--text-primary)]",
    },
    {
      label: "Win rate",
      value: summary.winRatePct === null ? "—" : formatPercent(summary.winRatePct),
      valueClassName: "text-[color:var(--text-primary)]",
    },
    {
      label: "Best trade",
      value: summary.bestTrade === null ? "—" : formatCurrency(summary.bestTrade, { signDisplay: true }),
      valueClassName: summary.bestTrade === null ? "text-[color:var(--text-muted)]" : pnlColorClass(summary.bestTrade),
    },
    {
      label: "Worst trade",
      value: summary.worstTrade === null ? "—" : formatCurrency(summary.worstTrade, { signDisplay: true }),
      valueClassName:
        summary.worstTrade === null ? "text-[color:var(--text-muted)]" : pnlColorClass(summary.worstTrade),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      {renderGroup(topRow)}
      {renderGroup(performanceRow)}
    </div>
  );
}
