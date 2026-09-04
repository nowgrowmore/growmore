import { getBacktestRuns } from "@/lib/db";
import { RankingsClient } from "@/components/RankingsClient";

export const dynamic = "force-dynamic";

export default async function RankingsPage() {
  const runs = await getBacktestRuns();

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Backtest rankings</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        The same backtest runs as the Backtests page, pre-ranked by each metric that matters —
        overall pick, pure growth, risk-adjusted quality, and so on — so you don&rsquo;t have to
        re-sort the full table to see who wins on which criterion.
      </p>
      <RankingsClient runs={runs} />
    </div>
  );
}
