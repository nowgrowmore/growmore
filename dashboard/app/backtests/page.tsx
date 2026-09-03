import { Fragment } from "react";
import Link from "next/link";
import { getBacktestRuns } from "@/lib/db";
import {
  filterBacktestRuns,
  formatDateRange,
  formatNumber,
  formatPercent,
  formatProfitFactor,
  formatStrategyParams,
  formatStrategyParamsTooltip,
  sortBacktestRuns,
  toNumber,
  type BacktestSortKey,
} from "@/lib/format";
import { STRATEGY_INFO } from "@/lib/strategy-info";

export const dynamic = "force-dynamic";

const SORT_COLUMNS: { key: BacktestSortKey; label: string; explain: string }[] = [
  { key: "started_at", label: "Started", explain: "When this backtest was run (not the price data's own dates -- see Timeframe)." },
  { key: "trade_count", label: "Trades", explain: "Closed trades in this run. Higher is more statistically trustworthy -- treat anything under ~15 as too thin to act on, regardless of how good the other numbers look." },
  { key: "sharpe_ratio", label: "Sharpe", explain: "Risk-adjusted return: reward per unit of volatility taken. Higher is better. Rough guide: <0.5 weak, 0.5-1 okay, >1 good, >2 excellent." },
  { key: "max_drawdown_pct", label: "Max drawdown", explain: "The worst peak-to-trough decline this run lived through. Lower is better -- it's literally how much pain before recovery." },
  { key: "win_rate_pct", label: "Win rate", explain: "% of closed trades that were profitable. Higher is generally better, but read it together with profit factor -- a high win rate with small wins and rare huge losses can still lose money overall." },
  { key: "profit_factor", label: "Profit factor", explain: "Gross profit / gross loss. Above 1 means net profitable over the sample; higher is better. \"inf\" means zero losing trades in the sample -- a sign the sample is very thin, not proof the strategy never loses." },
  { key: "cagr_pct", label: "CAGR", explain: "Annualized growth rate of this run's equity curve. Higher is better, but sensitive to the exact start/end dates of a short backtest -- weigh it less than Sharpe/profit factor for short runs." },
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
        live paper trading on the Strategies page. Column headers explain what each metric means
        and whether higher or lower is better -- also summarized in the legend below the table.
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
              <th className="px-3 py-2 font-medium">Params</th>
              <th className="px-3 py-2 font-medium">Instrument</th>
              <th className="px-3 py-2 font-medium" title="The price-data window this backtest was run over -- distinct from when it was actually run (see Started).">
                Timeframe
              </th>
              {SORT_COLUMNS.map((col) => (
                <th key={col.key} className="px-3 py-2 font-medium" title={col.explain}>
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
                <td className="px-3 py-2" title={run.strategy_name ? STRATEGY_INFO[run.strategy_name]?.summary : undefined}>
                  {run.strategy_name} <span className="text-[color:var(--text-muted)]">v{run.strategy_version}</span>
                </td>
                <td
                  className="px-3 py-2 text-[color:var(--text-secondary)]"
                  title={formatStrategyParamsTooltip(run.strategy_name ?? "", run.strategy_params) || undefined}
                >
                  {formatStrategyParams(run.strategy_params)}
                </td>
                <td className="px-3 py-2">{run.instrument_symbol}</td>
                <td className="px-3 py-2 whitespace-nowrap text-[color:var(--text-secondary)]">
                  {formatDateRange(run.period_start, run.period_end)}
                </td>
                <td className="px-3 py-2">{new Date(run.started_at).toLocaleDateString()}</td>
                <td className="px-3 py-2 tabular-nums">{toNumber(run.trade_count)}</td>
                <td className="px-3 py-2 tabular-nums">{formatNumber(run.sharpe_ratio ? Number(run.sharpe_ratio) : null)}</td>
                <td className="px-3 py-2 tabular-nums">
                  {formatPercent(run.max_drawdown_pct ? Number(run.max_drawdown_pct) : null)}
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {formatPercent(run.win_rate_pct ? Number(run.win_rate_pct) : null)}
                </td>
                <td className="px-3 py-2 tabular-nums">{formatProfitFactor(run.profit_factor)}</td>
                <td className="px-3 py-2 tabular-nums">
                  {formatPercent(run.cagr_pct ? Number(run.cagr_pct) : null)}
                </td>
              </tr>
            ))}
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={4 + SORT_COLUMNS.length} className="px-3 py-6 text-center text-[color:var(--text-muted)]">
                  No backtest runs match these filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4 text-sm sm:grid-cols-2">
        <dt className="font-medium">Timeframe</dt>
        <dd className="text-[color:var(--text-secondary)] sm:col-start-2">
          The price-data window backtested, not when the run was executed (see Started).
        </dd>
        {SORT_COLUMNS.filter((c) => c.key !== "started_at").map((col) => (
          <Fragment key={col.key}>
            <dt className="font-medium">{col.label}</dt>
            <dd className="text-[color:var(--text-secondary)] sm:col-start-2">{col.explain}</dd>
          </Fragment>
        ))}
      </dl>

      <div className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4">
        <h3 className="mb-3 text-sm font-semibold">Strategy reference</h3>
        <div className="flex flex-col gap-4">
          {Object.entries(STRATEGY_INFO).map(([name, info]) => (
            <div key={name}>
              <p className="text-sm font-medium">{info.label}</p>
              <p className="text-sm text-[color:var(--text-secondary)]">{info.summary}</p>
              <dl className="mt-1 grid grid-cols-1 gap-x-4 gap-y-1 pl-3 text-xs sm:grid-cols-[max-content_1fr]">
                {Object.entries(info.params).map(([key, param]) => (
                  <Fragment key={key}>
                    <dt className="text-[color:var(--text-muted)]">
                      {key} <span className="italic">({param.label})</span>
                    </dt>
                    <dd className="text-[color:var(--text-secondary)] sm:col-start-2">{param.explain}</dd>
                  </Fragment>
                ))}
              </dl>
            </div>
          ))}
        </div>
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
