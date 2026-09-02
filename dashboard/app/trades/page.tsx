import { getTradeLog } from "@/lib/db";
import { formatCurrency, toNumber } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function TradesPage() {
  const orders = await getTradeLog(200);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Trade log</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Simulated fills from the paper trading engine (most recent first).
      </p>

      {orders.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">No trades recorded yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                <th className="px-3 py-2 font-medium">Filled at</th>
                <th className="px-3 py-2 font-medium">Strategy</th>
                <th className="px-3 py-2 font-medium">Instrument</th>
                <th className="px-3 py-2 font-medium">Side</th>
                <th className="px-3 py-2 font-medium text-right">Qty</th>
                <th className="px-3 py-2 font-medium text-right">Fill price</th>
                <th className="px-3 py-2 font-medium">Position</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                  <td className="px-3 py-2">{new Date(order.filled_at).toLocaleString()}</td>
                  <td className="px-3 py-2">{order.strategy_name}</td>
                  <td className="px-3 py-2">{order.instrument_symbol}</td>
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
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatCurrency(toNumber(order.simulated_fill_price))}
                  </td>
                  <td className="px-3 py-2 text-[color:var(--text-secondary)]">{order.position_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
