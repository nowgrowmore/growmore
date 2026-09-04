import { getPortfolioBacktestRuns, getPortfolioEquityCurve, getPortfolioHoldings } from "@/lib/db";
import { SmallcapClient } from "@/components/SmallcapClient";

export const dynamic = "force-dynamic";

export default async function SmallcapPage() {
  const runs = await getPortfolioBacktestRuns();
  const [equityCurves, holdings] = await Promise.all([
    Promise.all(runs.map((r) => getPortfolioEquityCurve(r.id))),
    Promise.all(runs.map((r) => getPortfolioHoldings(r.id))),
  ]);
  const equityCurveByRunId = Object.fromEntries(runs.map((r, i) => [r.id, equityCurves[i]]));
  const holdingsByRunId = Object.fromEntries(runs.map((r, i) => [r.id, holdings[i]]));

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Small-cap momentum research</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Real-data backtests of a cross-sectional momentum(+quality) strategy on Nifty Smallcap 250
        and Nifty Midcap 150 — research only, not a strategy this bot trades. See{" "}
        <code className="rounded bg-[color:var(--gridline)] px-1">
          docs/smallcap-momentum-backtest-results.md
        </code>{" "}
        for the full methodology and caveats before drawing conclusions from any single number here.
      </p>
      {runs.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">
          No backtest runs yet — run{" "}
          <code className="rounded bg-[color:var(--gridline)] px-1">
            python -m research.smallcap_momentum.run_backtest --persist
          </code>{" "}
          from <code className="rounded bg-[color:var(--gridline)] px-1">bot/</code>.
        </p>
      ) : (
        <SmallcapClient
          runs={runs}
          equityCurveByRunId={equityCurveByRunId}
          holdingsByRunId={holdingsByRunId}
        />
      )}
    </div>
  );
}
