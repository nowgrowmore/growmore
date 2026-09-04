"use client";

import { useState } from "react";
import { formatCurrency, toNumber } from "@/lib/format";
import { ModeFilter } from "@/components/ModeFilter";

type Mode = "all" | "paper" | "live";

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "all", label: "All" },
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
];

export interface UnifiedTradeRow {
  id: string;
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
  position_status?: "open" | "closed";
  broker_order_id?: string;
  order_status?: string;
}

function formatExpiry(expiry: string | null | undefined): string {
  if (!expiry) return "—";
  return new Date(expiry).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function TradesClient({ rows }: { rows: UnifiedTradeRow[] }) {
  const [mode, setMode] = useState<Mode>("all");
  const filtered = mode === "all" ? rows : rows.filter((r) => r.tradeType === mode);

  return (
    <div className="flex flex-col gap-4">
      <ModeFilter value={mode} onChange={setMode} options={MODE_OPTIONS} />

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
