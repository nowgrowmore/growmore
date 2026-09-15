"use client";

import {
  formatCurrency,
  formatIstDate,
  formatIstDateTime,
  formatNumber,
  formatPercent,
  formatPositionAge,
  toNumber,
} from "@/lib/format";
import { rankCandidates, sanitizeCandidates } from "@/lib/mcx-options-candidates";
import { StrategyToggle } from "@/components/StrategyToggle";
import type {
  MCXOptionsCandidateRaw,
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

//: The leg currently outstanding against a position, if any. `state`
//: (flat/long_futures/closed) only tracks FUTURES exposure -- a freshly
//: sold put with no assignment yet is legitimately "flat" there, which
//: otherwise makes the current-position card look empty while a real short
//: option is live (see Trade History for it instead, confusingly). Surface
//: it here so "what's actually outstanding right now" doesn't require
//: cross-referencing the trade-history table.
function openLegFor(legs: MCXOptionsLeg[], positionId: string): MCXOptionsLeg | null {
  return legs.find((l) => l.position_id === positionId && l.settled_at === null) ?? null;
}

function OpenLegSummary({ leg }: { leg: MCXOptionsLeg | null }) {
  if (leg === null) {
    return <span className="text-[color:var(--text-muted)]">—</span>;
  }
  if (leg.opt_type === "ROLL") {
    // Shouldn't normally be seen "open" (a roll settles same-cycle), but
    // guard against the NULL strike/premium a roll leg carries regardless.
    return <span className="text-[color:var(--text-secondary)]">Roll in progress</span>;
  }
  const side = leg.opt_type === "CE" ? "call" : "put";
  return (
    <span className="text-[color:var(--text-secondary)]">
      Short {leg.opt_type} ({side}) @{" "}
      {leg.strike !== null ? formatCurrency(toNumber(leg.strike)) : "—"}
      {leg.premium !== null ? `, premium ${formatCurrency(toNumber(leg.premium))}` : ""}, exp{" "}
      {formatIstDate(leg.cycle_expiry)}
    </span>
  );
}

//: Risk/selection flags (migration 0027) are null/false by default, and the
//: bot treats that as "disabled". Only the ones actually SET are rendered:
//: listing seven "off"s on every config would bury the one that isn't, and an
//: enabled risk flag that shows up nowhere in the UI is exactly the kind of
//: thing that goes unnoticed for months.
function activeRiskFlags(config: MCXOptionsConfig): string[] {
  const flags: string[] = [];
  if (config.stop_loss_premium_multiple !== null) {
    flags.push(`stop-loss at ${formatNumber(toNumber(config.stop_loss_premium_multiple), 1)}× premium`);
  }
  if (config.min_dte_days !== null || config.max_dte_days !== null) {
    flags.push(`DTE ${config.min_dte_days ?? "–"}…${config.max_dte_days ?? "–"}d`);
  }
  if (config.min_credit_pct_of_strike !== null) {
    flags.push(
      `min credit ${formatPercent(toNumber(config.min_credit_pct_of_strike) * 100, 2)} of strike`
    );
  }
  if (config.max_relative_spread !== null) {
    flags.push(`max spread ${formatPercent(toNumber(config.max_relative_spread) * 100, 0)}`);
  }
  if (config.use_bid_for_entry_premium) flags.push("entry priced at bid");
  if (config.fallback_sigma !== null) {
    flags.push(`fallback σ ${formatNumber(toNumber(config.fallback_sigma))}`);
  }
  return flags;
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

//: How many candidates to show inline. The server already trims the payload
//: to a bounded set (see lib/db.ts) using the same ranking helper, so this is
//: the display cap on top of that, not the thing keeping the page small.
const MAX_CANDIDATES_SHOWN = 10;

function CandidatesEvaluated({
  candidates,
  candidatesTotal,
  selectedStrike,
  targetDelta,
}: {
  candidates: MCXOptionsCandidateRaw[] | null;
  candidatesTotal: number | null;
  selectedStrike: string | null;
  targetDelta: string | null;
}) {
  // Never trust the raw JSONB -- see lib/mcx-options-candidates.ts.
  const clean = sanitizeCandidates(candidates);
  if (clean === null) {
    return <span className="text-[color:var(--text-muted)]">—</span>;
  }
  if (clean.length === 0) {
    return <span className="text-[color:var(--text-muted)]">none cleared filters</span>;
  }
  const selected = selectedStrike !== null ? toNumber(selectedStrike) : null;
  const target = targetDelta !== null ? toNumber(targetDelta) : null;

  const shown = rankCandidates(clean, {
    selectedStrike: selected,
    targetDelta: target,
    limit: MAX_CANDIDATES_SHOWN,
  });

  // `candidatesTotal` is the count BEFORE the server trimmed the payload, so
  // "+N more" stays honest about how many strikes were really evaluated.
  const total = candidatesTotal ?? clean.length;
  const hiddenCount = total - shown.length;

  return (
    <div className="flex flex-col gap-1">
      <ul className="flex flex-wrap gap-x-2 gap-y-1">
        {shown.map((c) => {
          const isPicked = selected !== null && c.strike === selected;
          return (
            <li
              key={c.strike}
              className={
                isPicked
                  ? "rounded bg-[color:var(--success-text)]/15 px-1.5 py-0.5 font-semibold text-[color:var(--success-text)]"
                  : "text-[color:var(--text-secondary)]"
              }
            >
              {formatCurrency(c.strike)} (Δ{c.delta.toFixed(2)}, OI {c.oi.toLocaleString()})
            </li>
          );
        })}
      </ul>
      {hiddenCount > 0 && (
        <span className="text-xs text-[color:var(--text-muted)]">
          +{hiddenCount} more (low OI / far from target delta, hidden)
        </span>
      )}
    </div>
  );
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
        // Same predicate the table uses below -- only a long_futures position
        // carries unrealized exposure. Summing over every open position meant
        // a stale value on a flat row could appear in this total with nothing
        // in the table to account for it.
        const totalUnrealized = openPositions
          .filter((p) => p.state === "long_futures")
          .reduce((sum, p) => sum + toNumber(p.unrealized_pnl), 0);
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
                  {/* Delta is a unitless hedge ratio, not a percentage -- this
                      used to render 0.30 as "30%" while the candidate list
                      directly below showed the same quantity as "Δ-0.30". */}
                  Mode: <span className="capitalize">{config.mode}</span> · Lots {config.lots} ·
                  Target delta Δ{formatNumber(toNumber(config.consolidating_target_delta))}{" "}
                  (consolidating) / Δ
                  {formatNumber(toNumber(config.trend_favorable_target_delta))}{" "}
                  (trend favorable) · Min OI {config.min_open_interest} · Roll cost{" "}
                  {formatCurrency(toNumber(config.futures_roll_cost_per_lot))}/lot
                </p>
                <p className="mt-1 text-xs text-[color:var(--text-muted)]">
                  Risk flags:{" "}
                  {activeRiskFlags(config).length === 0
                    ? "none enabled (no stop-loss, no DTE window, no premium floor)"
                    : activeRiskFlags(config).join(" · ")}
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
                  {latestSelection ? formatIstDate(latestSelection.cycle_date) : "—"}
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
                  <table className="w-full min-w-[880px] text-sm">
                    <thead>
                      <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                        <th className="px-3 py-2 font-medium">State</th>
                        <th className="px-3 py-2 font-medium">Open leg</th>
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
                          <td className="px-3 py-2">
                            <OpenLegSummary leg={openLegFor(legs, p.id)} />
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {p.basis !== null ? formatCurrency(toNumber(p.basis)) : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">{toNumber(p.futures_qty)}</td>
                          <td className="px-3 py-2">
                            {formatIstDate(p.futures_contract_expiry)}
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
                Selection log{latestSelection ? ` — ${formatIstDate(latestSelection.cycle_date)}` : ""}
              </h4>
              {selections.length === 0 ? (
                <p className="text-sm text-[color:var(--text-muted)]">No selection cycle recorded yet.</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
                  <table className="w-full min-w-[1250px] text-sm">
                    <thead>
                      <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                        <th className="px-3 py-2 font-medium">Cycle time</th>
                        <th className="px-3 py-2 font-medium">Regime</th>
                        <th className="px-3 py-2 font-medium text-right">Target delta</th>
                        <th className="px-3 py-2 font-medium text-right">Selected strike</th>
                        <th className="px-3 py-2 font-medium">Expiry</th>
                        <th className="px-3 py-2 font-medium text-right">Futures price</th>
                        <th className="px-3 py-2 font-medium">Position</th>
                        <th className="px-3 py-2 font-medium text-right">Basis</th>
                        <th className="px-3 py-2 font-medium text-right">Unrealized P&amp;L</th>
                        <th className="px-3 py-2 font-medium">Candidates evaluated</th>
                        <th className="px-3 py-2 font-medium">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selections.map((s) => (
                        <tr key={s.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                          <td className="px-3 py-2 tabular-nums" title={formatIstDate(s.cycle_date)}>
                            {formatIstDateTime(s.created_at)}
                          </td>
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
                            {s.target_delta !== null
                              ? `Δ${formatNumber(toNumber(s.target_delta))}`
                              : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {s.selected_strike !== null ? formatCurrency(toNumber(s.selected_strike)) : "—"}
                          </td>
                          <td className="px-3 py-2">
                            {formatIstDate(s.option_expiry)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {s.futures_price !== null ? formatCurrency(toNumber(s.futures_price)) : "—"}
                          </td>
                          <td className="px-3 py-2">
                            {s.position_state !== null ? stateLabel(s.position_state) : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {s.position_basis !== null ? formatCurrency(toNumber(s.position_basis)) : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {/* `!== null`, not a truthy test: a genuine 0 is the
                                NORMAL reading for a flat position, and used to
                                render as "—" (absent data) the moment anything
                                coerced these numerics away from postgres.js's
                                truthy "0.00" string. */}
                            {s.position_unrealized_pnl !== null
                              ? formatCurrency(toNumber(s.position_unrealized_pnl), { signDisplay: true })
                              : "—"}
                          </td>
                          <td className="px-3 py-2">
                            <CandidatesEvaluated
                              candidates={s.candidates_considered}
                              candidatesTotal={s.candidates_total ?? null}
                              selectedStrike={s.selected_strike}
                              targetDelta={s.target_delta}
                            />
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
                        <th className="px-3 py-2 font-medium text-right">Lots</th>
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
                          {/* A ROLL leg is a futures contract rollover, not an
                              option: migration 0023 made strike/premium nullable
                              precisely for it. `toNumber(null)` rendered both as
                              "₹0.00", and since a roll has assigned=false,
                              called_away=false and settled_at set, the outcome
                              ladder below fell through to "Expired OTM" -- an
                              invented price and a wrong outcome on a row the
                              page's own intro promises to show properly. */}
                          <td className="px-3 py-2 text-right tabular-nums">
                            {leg.strike !== null ? formatCurrency(toNumber(leg.strike)) : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {leg.premium !== null ? formatCurrency(toNumber(leg.premium)) : "—"}
                          </td>
                          {/* Premium is quoted PER UNIT; realized P&L is booked as
                              premium x lots x lot_size, so without this column the
                              two could never be reconciled by eye. */}
                          <td className="px-3 py-2 text-right tabular-nums">
                            {formatNumber(toNumber(leg.lots), 0)}
                          </td>
                          <td className="px-3 py-2">{formatIstDate(leg.cycle_expiry)}</td>
                          <td className="px-3 py-2 text-[color:var(--text-secondary)]">
                            {leg.opt_type === "ROLL"
                              ? "Rolled"
                              : leg.assigned
                                ? "Assigned"
                                : leg.called_away
                                  ? "Called away"
                                  : leg.settled_at !== null
                                    ? "Expired OTM"
                                    : "Open"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {leg.settled_at === null ? (
                              <span className="text-xs italic text-[color:var(--text-muted)]">
                                open — premium already in Realized P&amp;L
                              </span>
                            ) : leg.pnl !== null ? (
                              formatCurrency(toNumber(leg.pnl), { signDisplay: true })
                            ) : (
                              "—"
                            )}
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
