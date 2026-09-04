"use client";

import { useState } from "react";
import {
  DEFAULT_MAX_DRAWDOWN_PCT,
  DEFAULT_MIN_TRADE_COUNT,
  rankByComposite,
  rankByCriterion,
  type RankableKey,
} from "@/lib/backtest-rankings";
import {
  formatDateRange,
  formatNumber,
  formatPercent,
  formatProfitFactor,
  formatStrategyParams,
  formatStrategyParamsTooltip,
  toNumber,
} from "@/lib/format";
import { ModeFilter } from "@/components/ModeFilter";
import type { BacktestRun } from "@/lib/types";

type Criterion = "composite" | RankableKey;

const CRITERIA: { value: Criterion; label: string }[] = [
  { value: "composite", label: "Overall pick" },
  { value: "cagr_pct", label: "CAGR" },
  { value: "sharpe_ratio", label: "Sharpe" },
  { value: "profit_factor", label: "Profit factor" },
  { value: "win_rate_pct", label: "Win rate" },
  { value: "max_drawdown_pct", label: "Max drawdown" },
];

function metricValue(run: BacktestRun, criterion: Criterion): string {
  switch (criterion) {
    case "composite":
      return `${formatNumber(toNumber(run.cagr_pct))}% CAGR / ${formatNumber(toNumber(run.sharpe_ratio))} Sharpe`;
    case "cagr_pct":
      return formatPercent(toNumber(run.cagr_pct));
    case "sharpe_ratio":
      return formatNumber(toNumber(run.sharpe_ratio));
    case "profit_factor":
      return formatProfitFactor(run.profit_factor);
    case "win_rate_pct":
      return formatPercent(toNumber(run.win_rate_pct));
    case "max_drawdown_pct":
      return formatPercent(toNumber(run.max_drawdown_pct));
    default:
      return "—";
  }
}

export function RankingsClient({ runs }: { runs: BacktestRun[] }) {
  const [criterion, setCriterion] = useState<Criterion>("composite");
  const ranked =
    criterion === "composite" ? rankByComposite(runs, 10) : rankByCriterion(runs, criterion, 10);

  return (
    <div className="flex flex-col gap-4">
      <ModeFilter value={criterion} onChange={setCriterion} options={CRITERIA} />

      <p className="text-xs text-[color:var(--text-muted)]">
        Only runs with at least {DEFAULT_MIN_TRADE_COUNT} closed trades and a max drawdown at or
        under {DEFAULT_MAX_DRAWDOWN_PCT}% are ranked — the rest are too thin or too risky to read as
        a real result, same guardrails as{" "}
        <code className="rounded bg-[color:var(--gridline)] px-1">docs/backtest-results.md</code>.
        {criterion === "composite" &&
          " \"Overall pick\" ranks by average percentile across CAGR and Sharpe together, so a run has to be genuinely good on both growth and risk-adjusted quality, not just spike one metric."}
      </p>

      {ranked.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">
          No backtest runs pass the guardrails yet.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[color:var(--border-hairline)]">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-[color:var(--border-hairline)] text-left text-[color:var(--text-secondary)]">
                <th className="px-3 py-2 font-medium">#</th>
                <th className="px-3 py-2 font-medium">Strategy</th>
                <th className="px-3 py-2 font-medium">Params</th>
                <th className="px-3 py-2 font-medium">Instrument</th>
                <th className="px-3 py-2 font-medium">Timeframe</th>
                <th className="px-3 py-2 font-medium text-right">Trades</th>
                <th className="px-3 py-2 font-medium text-right">
                  {CRITERIA.find((c) => c.value === criterion)?.label}
                </th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((run, i) => (
                <tr key={run.id} className="border-b border-[color:var(--border-hairline)] last:border-0">
                  <td className="px-3 py-2 tabular-nums text-[color:var(--text-muted)]">{i + 1}</td>
                  <td className="px-3 py-2">
                    {run.strategy_name}{" "}
                    <span className="text-[color:var(--text-muted)]">v{run.strategy_version}</span>
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
                  <td className="px-3 py-2 text-right tabular-nums">{toNumber(run.trade_count)}</td>
                  <td className="px-3 py-2 text-right font-medium tabular-nums">
                    {metricValue(run, criterion)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
