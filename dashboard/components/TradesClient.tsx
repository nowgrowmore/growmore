"use client";

import { useState } from "react";
import { formatCurrency, toNumber } from "@/lib/format";
import { toCsv } from "@/lib/csv";
import { ModeFilter } from "@/components/ModeFilter";
import { StrategyNameFilter } from "@/components/StrategyNameFilter";
import { ExportCsvButton } from "@/components/ExportCsvButton";
import { STRATEGY_INFO } from "@/lib/strategy-info";

type Mode = "all" | "paper" | "live";

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "all", label: "All" },
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
];

export interface UnifiedTradeRow {
  id: string;
  positionId: string;
  tradeType: "paper" | "live";
  filled_at: string;
  strategy_name?: string;
  instrument_symbol?: string;
  exchange_segment?: string;
  contract_expiry?: string | null;
  side: "buy" | "sell";
  quantity: string;
  instrument_lot_size?: number | string;
  fill_price: string;
  pnl: string | null;
  close_reason?: string | null;
  position_status?: "open" | "closed";
  broker_order_id?: string;
  order_status?: string;
}

function formatExpiry(expiry: string | null | undefined): string {
  if (!expiry) return "—";
  return new Date(expiry).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

const CLOSE_REASON_LABELS: Record<string, string> = {
  strategy_signal: "Strategy signal",
  end_of_day: "End of day flatten",
  expiry: "Contract expiry",
  daily_loss_limit: "Daily loss limit",
};

function formatCloseReason(side: "buy" | "sell", reason: string | null | undefined): string {
  if (side !== "sell") return "—";
  if (!reason) return "—";
  return CLOSE_REASON_LABELS[reason] ?? reason;
}

export function TradesClient({
  rows,
  positionFilter,
}: {
  rows: UnifiedTradeRow[];
  positionFilter?: string;
}) {
  const [mode, setMode] = useState<Mode>("live");

  const strategyNameOptions = Array.from(new Set(rows.map((r) => r.strategy_name).filter(Boolean)))
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

  const filtered = (mode === "all" ? rows : rows.filter((r) => r.tradeType === mode))
    .filter((r) => (positionFilter ? r.positionId === positionFilter : true))
    .filter((r) => !r.strategy_name || selectedStrategyNames.has(r.strategy_name));

  const csv = toCsv(filtered, [
    { header: "Type", value: (r) => r.tradeType },
    { header: "Filled at", value: (r) => r.filled_at },
    { header: "Strategy", value: (r) => r.strategy_name },
    { header: "Instrument", value: (r) => r.instrument_symbol },
    { header: "Exchange segment", value: (r) => r.exchange_segment },
    { header: "Contract expiry", value: (r) => r.contract_expiry },
    { header: "Side", value: (r) => r.side },
    { header: "Quantity (lots)", value: (r) => r.quantity },
    { header: "Lot size", value: (r) => r.instrument_lot_size },
    { header: "Fill price", value: (r) => r.fill_price },
    { header: "Realized P&L", value: (r) => r.pnl },
    { header: "Close reason", value: (r) => formatCloseReason(r.side, r.close_reason) },
    { header: "Position status", value: (r) => r.position_status },
    { header: "Broker order ID", value: (r) => r.broker_order_id },
    { header: "Broker order status", value: (r) => r.order_status },
  ]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <ExportCsvButton csv={csv} filename={`growmore-trades-${mode}.csv`} />
      </div>
      {positionFilter && (
        <div className="flex items-center gap-2 rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] px-3 py-2 text-sm">
          <span className="text-[color:var(--text-secondary)]">Showing fills for one position only.</span>
          <a href="/trades" className="font-medium text-[color:var(--series-1)] hover:underline">
            Clear filter
          </a>
        </div>
      )}
      <ModeFilter value={mode} onChange={setMode} options={MODE_OPTIONS} />
      <StrategyNameFilter
        options={strategyNameOptions}
        selected={selectedStrategyNames}
        onToggle={toggleStrategyName}
        onSelectAll={() => setSelectedStrategyNames(new Set(strategyNameOptions.map((o) => o.value)))}
        onSelectNone={() => setSelectedStrategyNames(new Set())}
      />

      {filtered.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">
          {rows.length === 0 ? "No trades recorded yet." : `No ${mode} trades recorded yet.`}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
          <table className="w-full min-w-[1040px] text-sm">
            <thead>
              <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Filled at</th>
                <th className="px-3 py-2 font-medium">Strategy</th>
                <th className="px-3 py-2 font-medium">Instrument</th>
                <th className="px-3 py-2 font-medium">Contract expiry</th>
                <th className="px-3 py-2 font-medium">Side</th>
                <th className="px-3 py-2 font-medium text-right">Qty (lots)</th>
                <th className="px-3 py-2 font-medium text-right">Lot size</th>
                <th className="px-3 py-2 font-medium text-right">Fill price</th>
                <th className="px-3 py-2 font-medium text-right">Notional value</th>
                <th className="px-3 py-2 font-medium text-right">Realized P&amp;L</th>
                <th className="px-3 py-2 font-medium">Close reason</th>
                <th className="px-3 py-2 font-medium">Position</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((order) => {
                const lotSize = toNumber(order.instrument_lot_size) || 1;
                const fillPrice = toNumber(order.fill_price);
                const notional = fillPrice * toNumber(order.quantity) * lotSize;
                const pnl = order.pnl === null ? null : toNumber(order.pnl);
                return (
                  <tr
                    key={`${order.tradeType}-${order.id}`}
                    className="border-b border-[color:var(--border-hairline)] last:border-0"
                  >
                    <td className="px-3 py-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-medium uppercase ${
                          order.tradeType === "live"
                            ? "bg-[color:var(--critical-text)]/15 text-[color:var(--critical-text)]"
                            : "bg-[color:var(--gridline)] text-[color:var(--text-secondary)]"
                        }`}
                      >
                        {order.tradeType}
                      </span>
                    </td>
                    <td className="px-3 py-2">{new Date(order.filled_at).toLocaleString()}</td>
                    <td className="px-3 py-2">{order.strategy_name}</td>
                    <td className="px-3 py-2">
                      {order.instrument_symbol}
                      <span className="ml-1 text-xs text-[color:var(--text-muted)]">
                        ({order.exchange_segment})
                      </span>
                    </td>
                    <td className="px-3 py-2 text-[color:var(--text-secondary)]">
                      {formatExpiry(order.contract_expiry)}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-medium uppercase ${
                          order.side === "buy"
                            ? "bg-[color:var(--series-3)]/15 text-[color:var(--series-3)]"
                            : "bg-[color:var(--series-2)]/15 text-[color:var(--series-2)]"
                        }`}
                      >
                        {order.side}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{order.quantity}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-[color:var(--text-secondary)]">
                      {lotSize}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatCurrency(fillPrice)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-[color:var(--text-secondary)]">
                      {formatCurrency(notional)}
                    </td>
                    <td
                      className={`px-3 py-2 text-right tabular-nums ${
                        pnl === null
                          ? "text-[color:var(--text-muted)]"
                          : pnl >= 0
                            ? "text-[color:var(--success-text)]"
                            : "text-[color:var(--critical-text)]"
                      }`}
                    >
                      {pnl === null ? "—" : formatCurrency(pnl, { signDisplay: true })}
                    </td>
                    <td className="px-3 py-2 text-[color:var(--text-secondary)]">
                      {formatCloseReason(order.side, order.close_reason)}
                    </td>
                    <td className="px-3 py-2 text-[color:var(--text-secondary)]">
                      {order.position_status}
                      {order.tradeType === "live" && order.order_status && (
                        <span className="ml-1 text-xs text-[color:var(--text-muted)]">
                          (broker: {order.order_status})
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
