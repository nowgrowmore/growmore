"use client";

import { useState } from "react";
import Link from "next/link";
import {
  formatCurrency,
  formatPercent,
  formatStrategyParams,
  formatStrategyParamsTooltip,
  toNumber,
} from "@/lib/format";
import { explainSignal } from "@/lib/signal-explain";
import { buildGaugeConfig } from "@/lib/strategy-gauge";
import { findMatchingBacktestRun, findRankPositions } from "@/lib/backtest-rankings";
import { computeLiveStats, flagDrift } from "@/lib/live-stats";
import { LevelGauge } from "@/components/LevelGauge";
import type { BacktestRun, BotConfig, LiveOrder, PaperOrder } from "@/lib/types";

function configLabel(c: BotConfig): string {
  return `${c.strategy_name} · ${c.instrument_symbol} · ${c.mode} (${formatStrategyParams(c.strategy_params)})`;
}

function ConfigCard({
  config,
  backtestRuns,
  orders,
}: {
  config: BotConfig | undefined;
  backtestRuns: BacktestRun[];
  orders: (PaperOrder | LiveOrder)[];
}) {
  if (!config) {
    return (
      <div className="flex min-h-[200px] items-center justify-center rounded-lg border border-dashed border-[color:var(--border-hairline)] p-4 text-sm text-[color:var(--text-muted)]">
        Pick a config above
      </div>
    );
  }

  const matchedRun = findMatchingBacktestRun(config, backtestRuns);
  const positions = findRankPositions(matchedRun, backtestRuns);
  const configOrders = orders.filter(
    (o) => o.strategy_id === config.strategy_id && o.instrument_id === config.instrument_id
  );
  const liveStats = computeLiveStats(configOrders);
  const drift = flagDrift(liveStats, matchedRun);
  const gauge = buildGaugeConfig(config);
  const explanation = explainSignal(
    config.strategy_name ?? "",
    config.signal_indicators,
    config.strategy_params,
    config.signal_ltp
  );

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4">
      <div>
        <div className="flex items-center gap-2 font-medium">
          {config.strategy_name}
          <span
            className={`rounded px-1.5 py-0.5 text-xs font-medium uppercase ${
              config.mode === "live"
                ? "bg-[color:var(--critical-text)]/15 text-[color:var(--critical-text)]"
                : "bg-[color:var(--gridline)] text-[color:var(--text-secondary)]"
            }`}
          >
            {config.mode}
          </span>
          <span
            className={`rounded px-1.5 py-0.5 text-xs font-medium ${
              config.enabled
                ? "bg-[color:var(--success-text)]/15 text-[color:var(--success-text)]"
                : "bg-[color:var(--gridline)] text-[color:var(--text-muted)]"
            }`}
          >
            {config.enabled ? "enabled" : "disabled"}
          </span>
        </div>
        <div className="text-sm text-[color:var(--text-secondary)]">{config.instrument_symbol}</div>
        <div
          className="mt-1 font-mono text-xs text-[color:var(--text-secondary)]"
          title={formatStrategyParamsTooltip(config.strategy_name ?? "", config.strategy_params) || undefined}
        >
          {formatStrategyParams(config.strategy_params)}
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-[color:var(--text-muted)]">Max position size</dt>
        <dd className="tabular-nums">{toNumber(config.max_position_size)}</dd>
        <dt className="text-[color:var(--text-muted)]">Daily loss limit</dt>
        <dd className="tabular-nums">
          {config.daily_loss_limit_enabled ? formatCurrency(toNumber(config.daily_loss_limit)) : "off"}
        </dd>
              </dl>

      <div>
        <span className="rounded bg-[color:var(--gridline)] px-2 py-0.5 text-xs font-semibold uppercase text-[color:var(--text-secondary)]">
          {config.last_signal ?? "no data yet"}
        </span>
        <p className="mt-2 text-xs text-[color:var(--text-secondary)]">{explanation}</p>
        {gauge && (
          <div className="mt-2">
            <LevelGauge {...gauge} />
          </div>
        )}
      </div>

      {positions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {positions.map((pos) => (
            <Link
              key={pos.criterion}
              href={`/backtests?view=ranked&criterion=${pos.criterion}`}
              className="rounded bg-[color:var(--series-1)]/10 px-1.5 py-0.5 text-xs font-medium text-[color:var(--series-1)] hover:underline"
            >
              #{pos.rank} {pos.label}
            </Link>
          ))}
        </div>
      )}

      <div className="text-xs text-[color:var(--text-secondary)]">
        <span className="font-medium">Live/paper history:</span>{" "}
        {liveStats.tradeCount === 0
          ? "no closed trades yet"
          : `${liveStats.tradeCount} closed trades, ${formatPercent(liveStats.winRatePct)} win rate` +
            (matchedRun ? ` (backtest: ${formatPercent(toNumber(matchedRun.win_rate_pct))})` : "")}
        {drift && (
          <span className="ml-1 text-[color:var(--critical-text)]">— diverging from backtest</span>
        )}
      </div>
    </div>
  );
}

export function CompareClient({
  configs,
  backtestRuns,
  paperOrders,
  liveOrders,
}: {
  configs: BotConfig[];
  backtestRuns: BacktestRun[];
  paperOrders: PaperOrder[];
  liveOrders: LiveOrder[];
}) {
  const [aId, setAId] = useState<string>(configs[0]?.id ?? "");
  const [bId, setBId] = useState<string>(configs[1]?.id ?? "");
  const configA = configs.find((c) => c.id === aId);
  const configB = configs.find((c) => c.id === bId);

  function ordersFor(config: BotConfig | undefined) {
    return config?.mode === "live" ? liveOrders : paperOrders;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col text-xs text-[color:var(--text-secondary)]">
          Config A
          <select
            value={aId}
            onChange={(e) => setAId(e.target.value)}
            className="mt-1 rounded border border-[color:var(--border-hairline)] bg-transparent px-2 py-1.5 text-sm"
          >
            {configs.map((c) => (
              <option key={c.id} value={c.id}>
                {configLabel(c)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-[color:var(--text-secondary)]">
          Config B
          <select
            value={bId}
            onChange={(e) => setBId(e.target.value)}
            className="mt-1 rounded border border-[color:var(--border-hairline)] bg-transparent px-2 py-1.5 text-sm"
          >
            {configs.map((c) => (
              <option key={c.id} value={c.id}>
                {configLabel(c)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <ConfigCard config={configA} backtestRuns={backtestRuns} orders={ordersFor(configA)} />
        <ConfigCard config={configB} backtestRuns={backtestRuns} orders={ordersFor(configB)} />
      </div>
    </div>
  );
}
