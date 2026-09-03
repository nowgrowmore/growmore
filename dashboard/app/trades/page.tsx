import { getTradeLog } from "@/lib/db";
import { formatCurrency, toNumber } from "@/lib/format";

export const dynamic = "force-dynamic";

function formatExpiry(expiry: string | null | undefined): string {
  if (!expiry) return "—";
  return new Date(expiry).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export default async function TradesPage() {
  const orders = await getTradeLog(200);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Trade log</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Simulated fills from the paper trading engine (most recent first). Contract details (lot
        size, expiry) are shown inline so there&rsquo;s no need to cross-check Dhan&rsquo;s own UI for them.
      </p>

      {orders.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">No trades recorded yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
          <table className="w-full min-w-[960px] text-sm">
            <thead>
              <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
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
              {orders.map((order) => {
                const lotSize = toNumber(order.instrument_lot_size) || 1;
                const fillPrice = toNumber(order.simulated_fill_price);
                const notional = fillPrice * toNumber(order.quantity) * lotSize;
                const pnl = order.pnl === null ? null : toNumber(order.pnl);
                return (
                  <tr key={order.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
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
                    <td className="px-3 py-2 text-[color:var(--text-secondary)]">{order.position_status}</td>
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
