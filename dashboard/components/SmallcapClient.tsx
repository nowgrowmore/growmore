"use client";

import { useState } from "react";
import { formatCurrency, formatPercent, toNumber } from "@/lib/format";
import { EquityChart } from "@/components/EquityChart";
import { ModeFilter } from "@/components/ModeFilter";
import type {
  PortfolioBacktestRun,
  PortfolioEquityCurvePoint,
  PortfolioRebalanceHolding,
} from "@/lib/types";

type UniverseFilter = "all" | "smallcap250" | "midcap150";

const UNIVERSE_OPTIONS: { value: UniverseFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "smallcap250", label: "Smallcap 250" },
  { value: "midcap150", label: "Midcap 150" },
];

function formatVariant(variant: string): string {
  return variant.replace(/_/g, " + ");
}

function formatUniverse(universe: string): string {
  if (universe === "smallcap250") return "Smallcap 250";
  if (universe === "midcap150") return "Midcap 150";
  return universe;
}

export function SmallcapClient({
  runs,
  equityCurveByRunId,
  holdingsByRunId,
}: {
  runs: PortfolioBacktestRun[];
  equityCurveByRunId: Record<string, PortfolioEquityCurvePoint[]>;
  holdingsByRunId: Record<string, PortfolioRebalanceHolding[]>;
}) {
  const [universe, setUniverse] = useState<UniverseFilter>("all");
  const filtered = universe === "all" ? runs : runs.filter((r) => r.universe === universe);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(filtered[0]?.id ?? null);

  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? filtered[0] ?? null;
  const selectedCurve = selectedRun ? equityCurveByRunId[selectedRun.id] ?? [] : [];
  const selectedHoldings = selectedRun ? holdingsByRunId[selectedRun.id] ?? [] : [];

  const latestRebalanceDate =
    selectedHoldings.length === 0
      ? null
      : selectedHoldings.reduce(
          (max, h) => (h.rebalance_date > max ? h.rebalance_date : max),
          selectedHoldings[0].rebalance_date
        );
  const latestHoldings = selectedHoldings.filter((h) => h.rebalance_date === latestRebalanceDate);

  return (
    <div className="flex flex-col gap-6">
      <ModeFilter value={universe} onChange={setUniverse} options={UNIVERSE_OPTIONS} />

      <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
        <table className="w-full min-w-[880px] text-sm">
          <thead>
            <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
              <th className="px-3 py-2 font-medium">Universe</th>
              <th className="px-3 py-2 font-medium">Variant</th>
              <th className="px-3 py-2 font-medium text-right">Rebalances</th>
              <th className="px-3 py-2 font-medium text-right">CAGR</th>
              <th className="px-3 py-2 font-medium text-right">Sharpe</th>
              <th className="px-3 py-2 font-medium text-right">Max drawdown</th>
              <th className="px-3 py-2 font-medium text-right">Win rate</th>
              <th className="px-3 py-2 font-medium text-right">Quality coverage</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((run) => {
              const active = run.id === selectedRun?.id;
              return (
                <tr
                  key={run.id}
                  onClick={() => setSelectedRunId(run.id)}
                  className={`cursor-pointer border-b border-[color:var(--border-hairline)] last:border-0 ${
                    active ? "bg-[color:var(--gridline)]/50" : "hover:bg-[color:var(--gridline)]/25"
                  }`}
                >
                  <td className="px-3 py-2">{formatUniverse(run.universe)}</td>
                  <td className="px-3 py-2 capitalize">{formatVariant(run.variant)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{run.rebalance_count}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatPercent(run.cagr_pct ? Number(run.cagr_pct) : null)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {run.sharpe_ratio ? Number(run.sharpe_ratio).toFixed(2) : "—"}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatPercent(run.max_drawdown_pct ? Number(run.max_drawdown_pct) : null)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatPercent(run.win_rate_pct ? Number(run.win_rate_pct) : null)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatPercent(
                      run.quality_coverage_pct ? Number(run.quality_coverage_pct) : null
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selectedRun && (
        <>
          <section>
            <h3 className="mb-3 text-sm font-semibold">
              {formatUniverse(selectedRun.universe)} — {formatVariant(selectedRun.variant)} equity curve
            </h3>
            <div className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4">
              <EquityChart
                points={selectedCurve.map((p) => ({ ts: p.ts, equity: toNumber(p.equity) }))}
              />
            </div>
            <p className="mt-2 text-xs text-[color:var(--text-muted)]">
              Started at {formatCurrency(toNumber(selectedRun.initial_capital))}, ended at{" "}
              {formatCurrency(toNumber(selectedRun.final_equity))}.
            </p>
          </section>

          <section>
            <h3 className="mb-3 text-sm font-semibold">
              Current holdings (as of {latestRebalanceDate ? new Date(latestRebalanceDate).toLocaleDateString() : "—"})
            </h3>
            {latestHoldings.length === 0 ? (
              <p className="text-sm text-[color:var(--text-muted)]">No holdings recorded.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
                <table className="w-full min-w-[400px] text-sm">
                  <thead>
                    <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                      <th className="px-3 py-2 font-medium">Symbol</th>
                      <th className="px-3 py-2 font-medium text-right">Weight</th>
                      <th className="px-3 py-2 font-medium text-right">Composite score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {latestHoldings
                      .sort((a, b) => toNumber(b.weight) - toNumber(a.weight))
                      .map((h) => (
                        <tr key={h.symbol} className="border-b border-[color:var(--border-hairline)] last:border-0">
                          <td className="px-3 py-2 font-medium">{h.symbol}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {formatPercent(toNumber(h.weight) * 100)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-[color:var(--text-secondary)]">
                            {h.composite_score ? Number(h.composite_score).toFixed(3) : "—"}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
