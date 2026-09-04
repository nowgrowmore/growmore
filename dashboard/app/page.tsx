import { getAllLivePositions, getAllPaperPositions } from "@/lib/db";
import { formatCurrency, summarizePositions, toNumber } from "@/lib/format";
import { SummaryCards } from "@/components/SummaryCards";
import { EquityChart, type EquityChartPoint } from "@/components/EquityChart";
import type { LivePosition, PaperPosition } from "@/lib/types";

export const dynamic = "force-dynamic";

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
            <th className="px-3 py-2 font-medium text-right">Notional exposure</th>
            <th className="px-3 py-2 font-medium text-right">Unrealized P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const unrealized = toNumber(p.unrealized_pnl);
            const lotSize = toNumber(p.instrument_lot_size) || 1;
            const notional = toNumber(p.avg_entry_price) * toNumber(p.quantity) * lotSize;
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
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default async function OverviewPage() {
  const [paperPositions, livePositions] = await Promise.all([
    getAllPaperPositions(),
    getAllLivePositions(),
  ]);
  const paperSummary = summarizePositions(paperPositions);
  const liveSummary = summarizePositions(livePositions);
  const openPaperPositions = paperPositions.filter((p) => p.status === "open");
  const openLivePositions = livePositions.filter((p) => p.status === "open");

  // Cumulative realized P&L across closed positions, as a stand-in equity
  // curve for the overview (a true equity curve tied to virtual_capital
  // lives per-backtest-run on /backtests).
  const closedByTime = paperPositions
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
        <SummaryCards summary={paperSummary} />
      </section>

      <section>
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-base font-semibold">Real (live) P&amp;L</h2>
          <span className="rounded bg-[color:var(--critical-text)]/15 px-1.5 py-0.5 text-xs font-medium uppercase text-[color:var(--critical-text)]">
            real money
          </span>
        </div>
        {livePositions.length === 0 ? (
          <p className="text-sm text-[color:var(--text-muted)]">
            No real orders have ever been placed — live trading is currently disabled
            (<code className="rounded bg-[color:var(--gridline)] px-1">LIVE_TRADING_ENABLED=false</code>).
            This section will populate automatically the moment that changes.
          </p>
        ) : (
          <SummaryCards summary={liveSummary} />
        )}
      </section>

      <section>
        <h2 className="mb-3 text-base font-semibold">Cumulative realized P&amp;L (paper)</h2>
        <div className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4">
          <EquityChart points={pnlTrend} />
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-base font-semibold">Open paper positions</h2>
        <OpenPositionsTable positions={openPaperPositions} />
      </section>

      {openLivePositions.length > 0 && (
        <section>
          <h2 className="mb-3 text-base font-semibold">Open real positions</h2>
          <OpenPositionsTable positions={openLivePositions} />
        </section>
      )}
    </div>
  );
}
