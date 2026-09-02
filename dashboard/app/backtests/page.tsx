import Link from "next/link";
import { getBacktestRuns } from "@/lib/db";
import {
  filterBacktestRuns,
  formatNumber,
  formatPercent,
  sortBacktestRuns,
  type BacktestSortKey,
} from "@/lib/format";

export const dynamic = "force-dynamic";

const SORT_COLUMNS: { key: BacktestSortKey; label: string }[] = [
  { key: "started_at", label: "Started" },
  { key: "sharpe_ratio", label: "Sharpe" },
  { key: "max_drawdown_pct", label: "Max drawdown" },
  { key: "win_rate_pct", label: "Win rate" },
  { key: "profit_factor", label: "Profit factor" },
  { key: "cagr_pct", label: "CAGR" },
];

interface BacktestsPageProps {
  searchParams: Promise<{
    sort?: string;
    dir?: string;
    instrument?: string;
    strategy?: string;
  }>;
}

function isSortKey(value: string | undefined): value is BacktestSortKey {
  return !!value && SORT_COLUMNS.some((c) => c.key === value);
}

export default async function BacktestsPage({ searchParams }: BacktestsPageProps) {
  const params = await searchParams;
  const sortKey: BacktestSortKey = isSortKey(params.sort) ? params.sort : "started_at";
  const direction = params.dir === "asc" ? "asc" : "desc";

  const runs = await getBacktestRuns();
  const filtered = filterBacktestRuns(runs, {
    instrumentId: params.instrument,
    strategyId: params.strategy,
  });
  const sorted = sortBacktestRuns(filtered, sortKey, direction);

  const instruments = Array.from(
    new Map(runs.map((r) => [r.instrument_id, r.instrument_symbol ?? r.instrument_id])).entries()
  );
  const strategies = Array.from(
    new Map(runs.map((r) => [r.strategy_id, r.strategy_name ?? r.strategy_id])).entries()
  );

  function sortHref(key: BacktestSortKey) {
    const nextDir = sortKey === key && direction === "desc" ? "asc" : "desc";
    const qp = new URLSearchParams();
    qp.set("sort", key);
    qp.set("dir", nextDir);
    if (params.instrument) qp.set("instrument", params.instrument);
    if (params.strategy) qp.set("strategy", params.strategy);
    return `/backtests?${qp.toString()}`;
  }

  function filterHref(next: { instrument?: string; strategy?: string }) {
    const qp = new URLSearchParams();
    qp.set("sort", sortKey);
    qp.set("dir", direction);
    const instrument = "instrument" in next ? next.instrument : params.instrument;
    const strategy = "strategy" in next ? next.strategy : params.strategy;
    if (instrument) qp.set("instrument", instrument);
    if (strategy) qp.set("strategy", strategy);
    return `/backtests?${qp.toString()}`;
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Backtest runs</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Review Sharpe, drawdown, win rate, and profit factor here before enabling a strategy for
        live paper trading on the Strategies page.
      </p>

      <div className="flex flex-wrap gap-4 text-sm">
        <FilterSelect
          label="Instrument"
          value={params.instrument ?? ""}
          options={instruments}
          hrefFor={(v) => filterHref({ instrument: v || undefined })}
        />
        <FilterSelect
          label="Strategy"
          value={params.strategy ?? ""}
          options={strategies}
          hrefFor={(v) => filterHref({ strategy: v || undefined })}
        />
      </div>

      <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
        <table className="w-full min-w-[800px] text-sm">
          <thead>
            <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
              <th className="px-3 py-2 font-medium">Strategy</th>
              <th className="px-3 py-2 font-medium">Instrument</th>
              {SORT_COLUMNS.map((col) => (
                <th key={col.key} className="px-3 py-2 font-medium">
                  <Link href={sortHref(col.key)} className="hover:underline">
                    {col.label}
                    {sortKey === col.key ? (direction === "asc" ? " ↑" : " ↓") : ""}
                  </Link>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((run) => (
              <tr key={run.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                <td className="px-3 py-2">
                  {run.strategy_name} <span className="text-[color:var(--text-muted)]">v{run.strategy_version}</span>
                </td>
                <td className="px-3 py-2">{run.instrument_symbol}</td>
                <td className="px-3 py-2">{new Date(run.started_at).toLocaleDateString()}</td>
                <td className="px-3 py-2 tabular-nums">{formatNumber(run.sharpe_ratio ? Number(run.sharpe_ratio) : null)}</td>
                <td className="px-3 py-2 tabular-nums">
                  {formatPercent(run.max_drawdown_pct ? Number(run.max_drawdown_pct) : null)}
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {formatPercent(run.win_rate_pct ? Number(run.win_rate_pct) : null)}
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {formatNumber(run.profit_factor ? Number(run.profit_factor) : null)}
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {formatPercent(run.cagr_pct ? Number(run.cagr_pct) : null)}
                </td>
              </tr>
            ))}
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={2 + SORT_COLUMNS.length} className="px-3 py-6 text-center text-[color:var(--text-muted)]">
                  No backtest runs match these filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  hrefFor,
}: {
  label: string;
  value: string;
  options: [string, string][];
  hrefFor: (value: string) => string;
}) {
  // Rendered as a row of links rather than a native <select> so filtering
  // works without client JS (server component, App Router friendly).
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[color:var(--text-secondary)]">{label}</span>
      <div className="flex flex-wrap gap-1">
        <Link
          href={hrefFor("")}
          className={`rounded px-2 py-1 ${
            value === "" ? "bg-[color:var(--series-1)] text-white" : "bg-[color:var(--surface-1)] border border-[color:var(--border-hairline)]"
          }`}
        >
          All
        </Link>
        {options.map(([id, name]) => (
          <Link
            key={id}
            href={hrefFor(id)}
            className={`rounded px-2 py-1 ${
              value === id ? "bg-[color:var(--series-1)] text-white" : "bg-[color:var(--surface-1)] border border-[color:var(--border-hairline)]"
            }`}
          >
            {name}
          </Link>
        ))}
      </div>
    </div>
  );
}
