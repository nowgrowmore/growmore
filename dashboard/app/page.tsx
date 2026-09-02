import { getAllPaperPositions } from "@/lib/db";
import { formatCurrency, summarizePositions, toNumber } from "@/lib/format";
import { SummaryCards } from "@/components/SummaryCards";
import { EquityChart, type EquityChartPoint } from "@/components/EquityChart";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const positions = await getAllPaperPositions();
  const summary = summarizePositions(positions);
  const openPositions = positions.filter((p) => p.status === "open");

  // Cumulative realized P&L across closed positions, as a stand-in equity
  // curve for the overview (a true equity curve tied to virtual_capital
  // lives per-backtest-run on /backtests).
  const closedByTime = positions
    .filter((p) => p.status === "closed" && p.closed_at)
    .sort((a, b) => new Date(a.closed_at!).getTime() - new Date(b.closed_at!).getTime());
  let running = 0;
  const pnlTrend: EquityChartPoint[] = closedByTime.map((p) => {
    running += toNumber(p.realized_pnl);
    return { ts: p.closed_at as string, equity: running };
  });

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h2 className="mb-3 text-base font-semibold">Live paper P&amp;L</h2>
        <SummaryCards summary={summary} />
      </section>

      <section>
        <h2 className="mb-3 text-base font-semibold">Cumulative realized P&amp;L</h2>
        <div className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4">
          <EquityChart points={pnlTrend} />
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-base font-semibold">Open positions</h2>
        {openPositions.length === 0 ? (
          <p className="text-sm text-[color:var(--text-muted)]">No open positions.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                  <th className="px-3 py-2 font-medium">Strategy</th>
                  <th className="px-3 py-2 font-medium">Instrument</th>
                  <th className="px-3 py-2 font-medium text-right">Qty</th>
                  <th className="px-3 py-2 font-medium text-right">Avg entry</th>
                  <th className="px-3 py-2 font-medium text-right">Unrealized P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {openPositions.map((p) => {
                  const unrealized = toNumber(p.unrealized_pnl);
                  return (
                    <tr key={p.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                      <td className="px-3 py-2">{p.strategy_name}</td>
                      <td className="px-3 py-2">{p.instrument_symbol}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{p.quantity}</td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {formatCurrency(toNumber(p.avg_entry_price))}
                      </td>
                      <td
                        className={`px-3 py-2 text-right tabular-nums ${
                          unrealized >= 0
                            ? "text-[color:var(--success-text)]"
                            : "text-[color:var(--critical-text)]"
                        }`}
                      >
                        {formatCurrency(unrealized, { signDisplay: true })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
