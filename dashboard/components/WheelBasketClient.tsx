"use client";

import { formatCurrency, formatPercent, formatPositionAge, toNumber } from "@/lib/format";
import { StrategyToggle } from "@/components/StrategyToggle";
import type {
  WheelBasketConfig,
  WheelBasketLeg,
  WheelBasketPosition,
  WheelBasketSelection,
} from "@/lib/types";

function stateLabel(state: string): string {
  switch (state) {
    case "short_put":
      return "Short put";
    case "holding_shares":
      return "Holding (uncovered)";
    case "short_call":
      return "Holding + short call";
    default:
      return state;
  }
}

function stateBadgeClass(state: string): string {
  switch (state) {
    case "holding_shares":
      return "bg-[color:var(--critical-text)]/15 text-[color:var(--critical-text)]";
    case "short_call":
      return "bg-[color:var(--success-text)]/15 text-[color:var(--success-text)]";
    default:
      return "bg-[color:var(--gridline)] text-[color:var(--text-secondary)]";
  }
}

export function WheelBasketClient({
  configs,
  positionsByConfigId,
  legsByConfigId,
  selectionsByConfigId,
  onToggle,
}: {
  configs: WheelBasketConfig[];
  positionsByConfigId: Record<string, WheelBasketPosition[]>;
  legsByConfigId: Record<string, WheelBasketLeg[]>;
  selectionsByConfigId: Record<string, WheelBasketSelection[]>;
  onToggle: (id: string, enabled: boolean) => Promise<void>;
}) {
  return (
    <div className="flex flex-col gap-8">
      {configs.map((config) => {
        const positions = positionsByConfigId[config.id] ?? [];
        const legs = legsByConfigId[config.id] ?? [];
        const selections = selectionsByConfigId[config.id] ?? [];
        const openPositions = positions.filter((p) => p.status === "open");
        const totalRealized = positions.reduce((sum, p) => sum + toNumber(p.realized_pnl), 0);
        const totalUnrealized = openPositions.reduce(
          (sum, p) => sum + toNumber(p.unrealized_pnl),
          0
        );
        const latestCycleDate = selections.reduce<string | null>(
          (max, s) => (max === null || s.cycle_date > max ? s.cycle_date : max),
          null
        );
        const latestSelections = selections
          .filter((s) => s.cycle_date === latestCycleDate)
          .sort((a, b) => toNumber(b.score ?? "0") - toNumber(a.score ?? "0"));

        return (
          <section
            key={config.id}
            className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h3 className="text-sm font-semibold">IV-rich wheel basket</h3>
                <p className="mt-1 text-xs text-[color:var(--text-secondary)]">
                  Mode: <span className="capitalize">{config.mode}</span> · Capital pool{" "}
                  {formatCurrency(toNumber(config.total_virtual_capital))} · Top{" "}
                  {formatPercent(toNumber(config.top_iv_frac) * 100, 0)} by IV rank · Rotation
                  margin {formatPercent(toNumber(config.rotation_hysteresis_pct) * 100, 0)}
                </p>
              </div>
              <StrategyToggle configId={config.id} initialEnabled={config.enabled} onToggle={onToggle} />
            </div>

            <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div>
                <dt className="text-xs text-[color:var(--text-muted)]">Open positions</dt>
                <dd className="text-lg font-semibold tabular-nums">{openPositions.length}</dd>
              </div>
              <div>
                <dt className="text-xs text-[color:var(--text-muted)]">Realized P&amp;L</dt>
                <dd className="text-lg font-semibold tabular-nums">
                  {formatCurrency(totalRealized, { signDisplay: true })}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[color:var(--text-muted)]">Unrealized P&amp;L</dt>
                <dd className="text-lg font-semibold tabular-nums">
                  {formatCurrency(totalUnrealized, { signDisplay: true })}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[color:var(--text-muted)]">Last cycle</dt>
                <dd className="text-lg font-semibold tabular-nums">
                  {latestCycleDate ? new Date(latestCycleDate).toLocaleDateString() : "—"}
                </dd>
              </div>
            </dl>

            <section className="mt-6">
              <h4 className="mb-2 text-xs font-semibold uppercase text-[color:var(--text-secondary)]">
                Current holdings
              </h4>
              {openPositions.length === 0 ? (
                <p className="text-sm text-[color:var(--text-muted)]">No open positions.</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
                  <table className="w-full min-w-[720px] text-sm">
                    <thead>
                      <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                        <th className="px-3 py-2 font-medium">Symbol</th>
                        <th className="px-3 py-2 font-medium">State</th>
                        <th className="px-3 py-2 font-medium text-right">Basis</th>
                        <th className="px-3 py-2 font-medium text-right">Shares</th>
                        <th className="px-3 py-2 font-medium text-right">Unrealized P&amp;L</th>
                        <th className="px-3 py-2 font-medium text-right">Age</th>
                      </tr>
                    </thead>
                    <tbody>
                      {openPositions.map((p) => (
                        <tr key={p.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                          <td className="px-3 py-2 font-medium">{p.symbol}</td>
                          <td className="px-3 py-2">
                            <span
                              className={`rounded px-2 py-0.5 text-xs font-medium ${stateBadgeClass(p.state)}`}
                            >
                              {stateLabel(p.state)}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {p.basis ? formatCurrency(toNumber(p.basis)) : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">{toNumber(p.shares)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {formatCurrency(toNumber(p.unrealized_pnl), { signDisplay: true })}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-[color:var(--text-secondary)]">
                            {formatPositionAge(p.opened_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="mt-6">
              <h4 className="mb-2 text-xs font-semibold uppercase text-[color:var(--text-secondary)]">
                Selection log{latestCycleDate ? ` — ${new Date(latestCycleDate).toLocaleDateString()}` : ""}
              </h4>
              {latestSelections.length === 0 ? (
                <p className="text-sm text-[color:var(--text-muted)]">No selection cycle recorded yet.</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
                  <table className="w-full min-w-[720px] text-sm">
                    <thead>
                      <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                        <th className="px-3 py-2 font-medium">Symbol</th>
                        <th className="px-3 py-2 font-medium text-right">IV percentile</th>
                        <th className="px-3 py-2 font-medium text-right">RSI</th>
                        <th className="px-3 py-2 font-medium">MACD</th>
                        <th className="px-3 py-2 font-medium">Selected</th>
                        <th className="px-3 py-2 font-medium">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {latestSelections.map((s) => (
                        <tr key={s.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                          <td className="px-3 py-2 font-medium">{s.symbol}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {s.iv_percentile ? formatPercent(toNumber(s.iv_percentile) * 100, 0) : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {s.rsi ? toNumber(s.rsi).toFixed(0) : "—"}
                          </td>
                          <td className="px-3 py-2">
                            {s.macd_bullish === null ? "—" : s.macd_bullish ? "Bullish" : "Bearish"}
                          </td>
                          <td className="px-3 py-2">
                            <span
                              className={`rounded px-2 py-0.5 text-xs font-medium ${
                                s.selected
                                  ? "bg-[color:var(--success-text)]/15 text-[color:var(--success-text)]"
                                  : "bg-[color:var(--gridline)] text-[color:var(--text-muted)]"
                              }`}
                            >
                              {s.selected ? "Selected" : "Rejected"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-[color:var(--text-secondary)]">{s.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="mt-6">
              <h4 className="mb-2 text-xs font-semibold uppercase text-[color:var(--text-secondary)]">
                Trade history
              </h4>
              {legs.length === 0 ? (
                <p className="text-sm text-[color:var(--text-muted)]">No legs written yet.</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
                  <table className="w-full min-w-[780px] text-sm">
                    <thead>
                      <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                        <th className="px-3 py-2 font-medium">Symbol</th>
                        <th className="px-3 py-2 font-medium">Action</th>
                        <th className="px-3 py-2 font-medium text-right">Strike</th>
                        <th className="px-3 py-2 font-medium text-right">Premium</th>
                        <th className="px-3 py-2 font-medium">Expiry</th>
                        <th className="px-3 py-2 font-medium">Outcome</th>
                        <th className="px-3 py-2 font-medium text-right">P&amp;L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {legs.map((leg) => (
                        <tr key={leg.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                          <td className="px-3 py-2 font-medium">{leg.symbol}</td>
                          <td className="px-3 py-2 capitalize">{leg.action.replace(/_/g, " ")}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {formatCurrency(toNumber(leg.strike))}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {formatCurrency(toNumber(leg.premium))}
                          </td>
                          <td className="px-3 py-2">{new Date(leg.cycle_expiry).toLocaleDateString()}</td>
                          <td className="px-3 py-2 text-[color:var(--text-secondary)]">
                            {leg.assigned
                              ? "Assigned"
                              : leg.called_away
                                ? "Called away"
                                : leg.settled_at
                                  ? "Expired OTM"
                                  : "Open"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {leg.pnl ? formatCurrency(toNumber(leg.pnl), { signDisplay: true }) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </section>
        );
      })}
    </div>
  );
}
