"use client";

import { useState } from "react";
import Link from "next/link";
import { computePctChangeToday, formatCurrency, formatPercent, summarizePositions, toNumber } from "@/lib/format";
import { SummaryCards } from "@/components/SummaryCards";
import { ModeFilter } from "@/components/ModeFilter";
import { StrategyNameFilter } from "@/components/StrategyNameFilter";
import { EquityChart, type EquityChartPoint } from "@/components/EquityChart";
import { STRATEGY_INFO } from "@/lib/strategy-info";
import type { LivePosition, PaperPosition } from "@/lib/types";

type Mode = "paper" | "live";

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
];

function OpenPositionsTable({ positions }: { positions: (PaperPosition | LivePosition)[] }) {
  if (positions.length === 0) {
    return <p className="text-sm text-[color:var(--text-muted)]">No open positions.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
      <table className="w-full min-w-[820px] text-sm">
        <thead>
          <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
            <th className="px-3 py-2 font-medium">Strategy</th>
            <th className="px-3 py-2 font-medium">Instrument</th>
            <th className="px-3 py-2 font-medium">Contract expiry</th>
            <th className="px-3 py-2 font-medium text-right">Qty (lots)</th>
            <th className="px-3 py-2 font-medium text-right">Avg entry</th>
            <th className="px-3 py-2 font-medium text-right">Current price</th>
            <th className="px-3 py-2 font-medium text-right">Today</th>
            <th className="px-3 py-2 font-medium text-right">Notional exposure</th>
            <th className="px-3 py-2 font-medium text-right">Unrealized P&amp;L</th>
            <th className="px-3 py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const unrealized = toNumber(p.unrealized_pnl);
            const lotSize = toNumber(p.instrument_lot_size) || 1;
            const notional = toNumber(p.avg_entry_price) * toNumber(p.quantity) * lotSize;
            const pctChange = computePctChangeToday(p.current_ltp, p.instrument_prev_close);
            return (
              <tr key={p.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                <td className="px-3 py-2">{p.strategy_name}</td>
                <td className="px-3 py-2">{p.instrument_symbol}</td>
                <td className="px-3 py-2 text-[color:var(--text-secondary)]">
                  {p.contract_expiry
                    ? new Date(p.contract_expiry).toLocaleDateString("en-GB", {
                        day: "numeric",
                        month: "short",
                        year: "numeric",
                      })
                    : "—"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{p.quantity}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {formatCurrency(toNumber(p.avg_entry_price))}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {p.current_ltp ? formatCurrency(toNumber(p.current_ltp)) : "—"}
                </td>
                <td
                  className={`px-3 py-2 text-right tabular-nums text-xs font-medium ${
                    pctChange === null
                      ? "text-[color:var(--text-muted)]"
                      : pctChange >= 0
                        ? "text-[color:var(--success-text)]"
                        : "text-[color:var(--critical-text)]"
                  }`}
                >
                  {pctChange === null
                    ? "—"
                    : `${pctChange >= 0 ? "▲" : "▼"} ${formatPercent(Math.abs(pctChange))}`}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-[color:var(--text-secondary)]">
                  {formatCurrency(notional)}
                </td>
                <td
                  className={`px-3 py-2 text-right tabular-nums ${
                    unrealized >= 0 ? "text-[color:var(--success-text)]" : "text-[color:var(--critical-text)]"
                  }`}
                >
                  {formatCurrency(unrealized, { signDisplay: true })}
                </td>
                <td className="px-3 py-2 text-right">
                  <Link
                    href={`/trades?position=${p.id}`}
                    className="text-xs font-medium text-[color:var(--series-1)] hover:underline"
                  >
                    Fills
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function buildPnlTrend(positions: (PaperPosition | LivePosition)[]): EquityChartPoint[] {
  const closedByTime = positions
    .filter((p) => p.status === "closed" && p.closed_at)
    .sort((a, b) => new Date(a.closed_at!).getTime() - new Date(b.closed_at!).getTime());
  let running = 0;
  return closedByTime.map((p) => {
    running += toNumber(p.realized_pnl);
    return { ts: p.closed_at as string, equity: running };
  });
}

export function OverviewClient({
  paperPositions,
  livePositions,
}: {
  paperPositions: PaperPosition[];
  livePositions: LivePosition[];
}) {
  const [mode, setMode] = useState<Mode>("live");

  // Union of both modes' strategy names -- switching Paper/Live never hides
  // a strategy's toggle just because the other mode happened to have it.
  const strategyNameOptions = Array.from(
    new Set([...paperPositions, ...livePositions].map((p) => p.strategy_name).filter(Boolean))
  )
    .filter((name): name is string => name !== undefined)
    .sort()
    .map((name) => ({ value: name, label: STRATEGY_INFO[name]?.label ?? name }));
  const [selectedStrategyNames, setSelectedStrategyNames] = useState<Set<string>>(
    () => new Set(strategyNameOptions.map((o) => o.value))
  );
  const toggleStrategyName = (name: string) => {
    setSelectedStrategyNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const positions = (mode === "paper" ? paperPositions : livePositions).filter(
    (p) => !p.strategy_name || selectedStrategyNames.has(p.strategy_name)
  );
  const summary = summarizePositions(positions);
  const openPositions = positions.filter((p) => p.status === "open");
  const pnlTrend = buildPnlTrend(positions);
  const hasAnyLiveActivity = livePositions.length > 0;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <ModeFilter value={mode} onChange={setMode} options={MODE_OPTIONS} />
          <StrategyNameFilter
            options={strategyNameOptions}
            selected={selectedStrategyNames}
            onToggle={toggleStrategyName}
            onSelectAll={() => setSelectedStrategyNames(new Set(strategyNameOptions.map((o) => o.value)))}
            onSelectNone={() => setSelectedStrategyNames(new Set())}
          />
        </div>
        {mode === "live" && (
          <span className="rounded bg-[color:var(--critical-text)]/15 px-1.5 py-0.5 text-xs font-medium uppercase text-[color:var(--critical-text)]">
            real money
          </span>
        )}
      </div>

      {mode === "live" && !hasAnyLiveActivity ? (
        <p className="text-sm text-[color:var(--text-muted)]">
          No real orders have ever been placed — live trading is currently disabled
          (<code className="rounded bg-[color:var(--gridline)] px-1">LIVE_TRADING_ENABLED=false</code>).
          This view will populate automatically the moment that changes.
        </p>
      ) : (
        <>
          <section>
            <h2 className="mb-3 text-base font-semibold">
              {mode === "paper" ? "Live paper P&L" : "Real (live) P&L"}
            </h2>
            <SummaryCards summary={summary} />
          </section>

          <section>
            <h2 className="mb-3 text-base font-semibold">Cumulative realized P&amp;L</h2>
            <div className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4">
              <EquityChart points={pnlTrend} />
            </div>
          </section>

          <section>
            <h2 className="mb-3 text-base font-semibold">
              Open {mode === "paper" ? "paper" : "real"} positions
            </h2>
            <OpenPositionsTable positions={openPositions} />
          </section>
        </>
      )}
    </div>
  );
}
