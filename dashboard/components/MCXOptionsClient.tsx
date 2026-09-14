"use client";

import { formatCurrency, formatPercent, formatPositionAge, toNumber } from "@/lib/format";
import { StrategyToggle } from "@/components/StrategyToggle";
import type {
  MCXOptionsConfig,
  MCXOptionsLeg,
  MCXOptionsPosition,
  MCXOptionsSelection,
} from "@/lib/types";

function stateLabel(state: string): string {
  switch (state) {
    case "flat":
      return "Flat";
    case "long_futures":
      return "Long futures (covered)";
    case "closed":
      return "Closed";
    default:
      return state;
  }
}

function stateBadgeClass(state: string): string {
  switch (state) {
    case "long_futures":
      return "bg-[color:var(--critical-text)]/15 text-[color:var(--critical-text)]";
    case "closed":
      return "bg-[color:var(--success-text)]/15 text-[color:var(--success-text)]";
    default:
      return "bg-[color:var(--gridline)] text-[color:var(--text-secondary)]";
  }
}

function regimeLabel(regime: string | null): string {
  switch (regime) {
    case "consolidating":
      return "Consolidating";
    case "trend_favorable":
      return "Trend favorable";
    case "trend_unfavorable":
      return "Trend unfavorable";
    default:
      return "No opinion";
  }
}

export function MCXOptionsClient({
  configs,
  positionsByConfigId,
  legsByConfigId,
  selectionsByConfigId,
  onToggle,
}: {
  configs: MCXOptionsConfig[];
  positionsByConfigId: Record<string, MCXOptionsPosition[]>;
  legsByConfigId: Record<string, MCXOptionsLeg[]>;
  selectionsByConfigId: Record<string, MCXOptionsSelection[]>;
  onToggle: (id: string, enabled: boolean) => Promise<void>;
}) {
  return (
    <div className="flex flex-col gap-8">
      {configs.map((config) => {
        const positions = positionsByConfigId[config.id] ?? [];
        const legs = legsByConfigId[config.id] ?? [];
        const selections = (selectionsByConfigId[config.id] ?? [])
          .slice()
          .sort((a, b) => (a.cycle_date < b.cycle_date ? 1 : a.cycle_date > b.cycle_date ? -1 : 0));
        const openPositions = positions.filter((p) => p.status === "open");
        const totalRealized = positions.reduce((sum, p) => sum + toNumber(p.realized_pnl), 0);
        const totalUnrealized = openPositions.reduce(
          (sum, p) => sum + toNumber(p.unrealized_pnl),
          0
        );
        const latestSelection = selections[0] ?? null;

        return (
          <section
            key={config.id}
            className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h3 className="text-sm font-semibold">{config.symbol} options wheel</h3>
                <p className="mt-1 text-xs text-[color:var(--text-secondary)]">
                  Mode: <span className="capitalize">{config.mode}</span> · Lots {config.lots} ·
                  Target delta {formatPercent(toNumber(config.consolidating_target_delta) * 100, 0)}
                  {" "}(consolidating) / {formatPercent(
                    toNumber(config.trend_favorable_target_delta) * 100,
                    0
                  )}{" "}
                  (trend favorable) · Min OI {config.min_open_interest}
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
                  {latestSelection ? new Date(latestSelection.cycle_date).toLocaleDateString() : "—"}
                </dd>
              </div>
            </dl>

            <section className="mt-6">
              <h4 className="mb-2 text-xs font-semibold uppercase text-[color:var(--text-secondary)]">
                Current position
              </h4>
              {openPositions.length === 0 ? (
                <p className="text-sm text-[color:var(--text-muted)]">No open position.</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
                  <table className="w-full min-w-[720px] text-sm">
                    <thead>
                      <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                        <th className="px-3 py-2 font-medium">State</th>
                        <th className="px-3 py-2 font-medium text-right">Basis</th>
                        <th className="px-3 py-2 font-medium text-right">Futures qty</th>
                        <th className="px-3 py-2 font-medium">Contract expiry</th>
                        <th className="px-3 py-2 font-medium text-right">Unrealized P&amp;L</th>
                        <th className="px-3 py-2 font-medium text-right">Age</th>
                      </tr>
                    </thead>
                    <tbody>
                      {openPositions.map((p) => (
                        <tr key={p.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
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
                          <td className="px-3 py-2 text-right tabular-nums">{toNumber(p.futures_qty)}</td>
                          <td className="px-3 py-2">
                            {p.futures_contract_expiry
                              ? new Date(p.futures_contract_expiry).toLocaleDateString()
                              : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {p.state === "long_futures"
                              ? formatCurrency(toNumber(p.unrealized_pnl), { signDisplay: true })
                              : "—"}
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
                Selection log{latestSelection ? ` — ${new Date(latestSelection.cycle_date).toLocaleDateString()}` : ""}
              </h4>
              {selections.length === 0 ? (
                <p className="text-sm text-[color:var(--text-muted)]">No selection cycle recorded yet.</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
                  <table className="w-full min-w-[720px] text-sm">
                    <thead>
                      <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                        <th className="px-3 py-2 font-medium">Date</th>
                        <th className="px-3 py-2 font-medium">Regime</th>
                        <th className="px-3 py-2 font-medium text-right">Target delta</th>
                        <th className="px-3 py-2 font-medium text-right">Selected strike</th>
                        <th className="px-3 py-2 font-medium">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selections.map((s) => (
                        <tr key={s.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                          <td className="px-3 py-2 tabular-nums">{new Date(s.cycle_date).toLocaleDateString()}</td>
                          <td className="px-3 py-2">
                            <span
                              className={`rounded px-2 py-0.5 text-xs font-medium ${
                                s.selected_strike !== null
                                  ? "bg-[color:var(--success-text)]/15 text-[color:var(--success-text)]"
                                  : "bg-[color:var(--gridline)] text-[color:var(--text-muted)]"
                              }`}
                            >
                              {regimeLabel(s.regime)}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {s.target_delta ? formatPercent(toNumber(s.target_delta) * 100, 0) : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {s.selected_strike ? formatCurrency(toNumber(s.selected_strike)) : "—"}
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
                        <th className="px-3 py-2 font-medium">Type</th>
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
                          <td className="px-3 py-2 font-medium">{leg.opt_type}</td>
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
