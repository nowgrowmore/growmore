"use client";

import { useState } from "react";
import Link from "next/link";
import {
  computeDailyLossProgress,
  computePctChangeToday,
  formatCurrency,
  formatPercent,
  formatStrategyParams,
  formatStrategyParamsTooltip,
  timeAgo,
  toNumber,
} from "@/lib/format";
import { explainSignal } from "@/lib/signal-explain";
import { buildGaugeConfig } from "@/lib/strategy-gauge";
import { STRATEGY_INFO } from "@/lib/strategy-info";
import { findMatchingBacktestRun, findRankPositions } from "@/lib/backtest-rankings";
import { computeLiveStats, flagDrift } from "@/lib/live-stats";
import { StrategyToggle } from "@/components/StrategyToggle";
import { RiskParamsForm } from "@/components/RiskParamsForm";
import { ModeFilter } from "@/components/ModeFilter";
import { StrategyNameFilter } from "@/components/StrategyNameFilter";
import { LevelGauge } from "@/components/LevelGauge";
import { SignalSparkline } from "@/components/SignalSparkline";
import type {
  AuditLogEntry,
  BacktestRun,
  BotConfig,
  LiveOrder,
  PaperOrder,
  SignalHistoryRow,
} from "@/lib/types";
import { Fragment } from "react";

type Mode = "all" | "paper" | "live";

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "all", label: "All" },
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
];

function signalBadgeClass(signal: string | null | undefined): string {
  switch (signal) {
    case "BUY":
      return "bg-[color:var(--success-text)]/15 text-[color:var(--success-text)]";
    case "SELL":
      return "bg-[color:var(--critical-text)]/15 text-[color:var(--critical-text)]";
    case "HOLD":
      return "bg-[color:var(--gridline)] text-[color:var(--text-secondary)]";
    default:
      return "bg-[color:var(--gridline)] text-[color:var(--text-muted)]";
  }
}

function formatIndicators(indicators: Record<string, number | string> | null | undefined): string {
  if (!indicators || Object.keys(indicators).length === 0) return "";
  return Object.entries(indicators)
    .map(([key, value]) => `${key}=${typeof value === "number" ? value.toFixed(2) : value}`)
    .join("  ·  ");
}

function lossBarColor(severity: "ok" | "warning" | "critical"): string {
  switch (severity) {
    case "critical":
      return "bg-[color:var(--critical-text)]";
    case "warning":
      return "bg-[color:var(--series-4)]";
    default:
      return "bg-[color:var(--success-text)]";
  }
}

interface StrategiesClientProps {
  configs: BotConfig[];
  backtestRuns: BacktestRun[];
  paperOrders: PaperOrder[];
  liveOrders: LiveOrder[];
  recentSignals: Record<string, SignalHistoryRow[]>;
  lastStateChanges: Record<string, AuditLogEntry>;
  onToggle: (id: string, enabled: boolean) => Promise<void>;
  onSaveRiskParams: (id: string, formData: FormData) => Promise<void>;
}

const STATE_CHANGE_LABELS: Record<string, string> = {
  strategy_enabled: "manual",
  strategy_disabled: "manual",
  risk_guard_daily_loss_limit_tripped: "daily loss limit tripped",
  live_risk_guard_daily_loss_limit_tripped: "daily loss limit tripped",
};

function formatRelativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const days = Math.floor(ms / (1000 * 60 * 60 * 24));
  if (days >= 1) return `${days}d ago`;
  const hours = Math.floor(ms / (1000 * 60 * 60));
  if (hours >= 1) return `${hours}h ago`;
  const minutes = Math.max(0, Math.floor(ms / (1000 * 60)));
  return `${minutes}m ago`;
}

export function StrategiesClient({
  configs,
  backtestRuns,
  paperOrders,
  liveOrders,
  recentSignals,
  lastStateChanges,
  onToggle,
  onSaveRiskParams,
}: StrategiesClientProps) {
  const [mode, setMode] = useState<Mode>("live");

  const strategyNameOptions = Array.from(new Set(configs.map((c) => c.strategy_name).filter(Boolean)))
    .filter((name): name is string => name !== undefined)
    .sort()
    .map((name) => ({ value: name, label: STRATEGY_INFO[name]?.label ?? name }));
  const [selectedStrategyNames, setSelectedStrategyNames] = useState<Set<string>>(
    () => new Set(strategyNameOptions.map((o) => o.value))
  );
  const toggleStrategyName = (name: string) => {
    setSelectedStrategyNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const filtered = (mode === "all" ? configs : configs.filter((c) => c.mode === mode)).filter(
    (c) => !c.strategy_name || selectedStrategyNames.has(c.strategy_name)
  );

  const strategiesPresent = Array.from(new Set(configs.map((c) => c.strategy_name).filter(Boolean)))
    .filter((name): name is string => name !== undefined && name in STRATEGY_INFO);

  return (
    <div className="flex flex-col gap-4">
      <ModeFilter value={mode} onChange={setMode} options={MODE_OPTIONS} />
      <StrategyNameFilter
        options={strategyNameOptions}
        selected={selectedStrategyNames}
        onToggle={toggleStrategyName}
        onSelectAll={() => setSelectedStrategyNames(new Set(strategyNameOptions.map((o) => o.value)))}
        onSelectNone={() => setSelectedStrategyNames(new Set())}
      />

      {filtered.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">
          {configs.length === 0 ? "No bot_config rows yet." : `No ${mode} configs.`}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {filtered.map((config) => {
            const explanation = explainSignal(
              config.strategy_name ?? "",
              config.signal_indicators,
              config.strategy_params,
              config.signal_ltp
            );
            const matchedRun = findMatchingBacktestRun(config, backtestRuns);
            const configOrders =
              config.mode === "paper"
                ? paperOrders.filter(
                    (o) => o.strategy_id === config.strategy_id && o.instrument_id === config.instrument_id
                  )
                : liveOrders.filter(
                    (o) => o.strategy_id === config.strategy_id && o.instrument_id === config.instrument_id
                  );
            const liveStats = computeLiveStats(configOrders);
            const drift = flagDrift(liveStats, matchedRun);
            const stateChange = !config.enabled ? lastStateChanges[config.id] : undefined;
            const history = recentSignals[config.id] ?? [];
            return (
              <div
                key={config.id}
                className="flex flex-col gap-4 rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex items-center gap-4">
                    <StrategyToggle configId={config.id} initialEnabled={config.enabled} onToggle={onToggle} />
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
                      </div>
                      <div className="text-sm text-[color:var(--text-secondary)]">
                        {config.instrument_symbol}
                        {" · "}
                        <span
                          className="font-mono text-xs"
                          title={formatStrategyParamsTooltip(config.strategy_name ?? "", config.strategy_params) || undefined}
                        >
                          {formatStrategyParams(config.strategy_params)}
                        </span>
                        {" · "}
                        <Link
                          href={`/backtests?strategy=${config.strategy_id}&instrument=${config.instrument_id}`}
                          className="text-[color:var(--series-1)] hover:underline"
                        >
                          View backtest
                        </Link>
                      </div>
                      {(() => {
                        const positions = findRankPositions(matchedRun, backtestRuns);
                        if (positions.length === 0 && !drift) return null;
                        return (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {positions.map((pos) => (
                              <Link
                                key={pos.criterion}
                                href={`/rankings`}
                                className="rounded bg-[color:var(--series-1)]/10 px-1.5 py-0.5 text-xs font-medium text-[color:var(--series-1)] hover:underline"
                                title={`#${pos.rank} in the top-10 ${pos.label} backtest ranking`}
                              >
                                #{pos.rank} {pos.label}
                              </Link>
                            ))}
                            {drift && (
                              <span
                                className="rounded bg-[color:var(--critical-text)]/10 px-1.5 py-0.5 text-xs font-medium text-[color:var(--critical-text)]"
                                title={`Backtest win rate ${formatPercent(drift.backtestWinRatePct)}, live win rate ${formatPercent(drift.liveWinRatePct)} over ${liveStats.tradeCount} closed trades`}
                              >
                                Live win rate {formatPercent(drift.liveWinRatePct)} vs backtest{" "}
                                {formatPercent(drift.backtestWinRatePct)} — worth a look
                              </span>
                            )}
                          </div>
                        );
                      })()}
                      {stateChange && (
                        <p className="mt-1 text-xs text-[color:var(--text-muted)]">
                          Disabled {formatRelativeTime(stateChange.ts)} —{" "}
                          {STATE_CHANGE_LABELS[stateChange.event_type] ?? stateChange.event_type}
                        </p>
                      )}
                      {history.length > 0 && (
                        <div className="mt-1.5">
                          <SignalSparkline history={history} />
                        </div>
                      )}
                    </div>
                  </div>
                  <RiskParamsForm
                    configId={config.id}
                    maxPositionSize={toNumber(config.max_position_size)}
                    dailyLossLimit={toNumber(config.daily_loss_limit)}
                    virtualCapital={toNumber(config.virtual_capital)}
                    dailyLossLimitEnabled={config.daily_loss_limit_enabled}
                    action={onSaveRiskParams}
                  />
                </div>

                <div className="rounded-md bg-[color:var(--gridline)]/40 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${signalBadgeClass(
                        config.last_signal
                      )}`}
                    >
                      {config.last_signal ?? "no data yet"}
                    </span>
                    <span className="text-xs text-[color:var(--text-muted)]">
                      checked {timeAgo(config.signal_checked_at)}
                      {config.signal_ltp ? ` · LTP ${toNumber(config.signal_ltp).toLocaleString("en-IN")}` : ""}
                    </span>
                    {(() => {
                      const pctChange = computePctChangeToday(config.signal_ltp, config.signal_prev_close);
                      if (pctChange === null) return null;
                      return (
                        <span
                          className={`text-xs font-medium tabular-nums ${
                            pctChange >= 0
                              ? "text-[color:var(--success-text)]"
                              : "text-[color:var(--critical-text)]"
                          }`}
                        >
                          {pctChange >= 0 ? "▲" : "▼"} {formatPercent(Math.abs(pctChange))} today
                        </span>
                      );
                    })()}
                  </div>
                  <p className="mt-2 text-sm text-[color:var(--text-secondary)]">
                    {config.last_signal
                      ? explanation
                      : "The bot hasn't ticked this config yet — it only evaluates during MCX market hours (9:00 AM–11:30/11:55 PM IST, weekdays)."}
                  </p>
                  {config.signal_indicators && Object.keys(config.signal_indicators).length > 0 && (
                    <p className="mt-1 font-mono text-xs text-[color:var(--text-muted)]">
                      {formatIndicators(config.signal_indicators)}
                    </p>
                  )}
                  {(() => {
                    const gauge = buildGaugeConfig(config);
                    if (!gauge) return null;
                    return (
                      <div className="mt-2">
                        <LevelGauge {...gauge} />
                      </div>
                    );
                  })()}
                </div>

                {!config.daily_loss_limit_enabled ? (
                  <p className="text-xs text-[color:var(--text-muted)]">
                    Daily loss limit guard is off — relying on the strategy&apos;s own signals only.
                  </p>
                ) : (
                  (() => {
                  const progress = computeDailyLossProgress(
                    config.signal_daily_pnl,
                    config.daily_loss_limit
                  );
                  if (!progress) return null;
                  return (
                    <div>
                      <div className="mb-1 flex items-center justify-between text-xs text-[color:var(--text-secondary)]">
                        <span>Daily loss limit</span>
                        <span className="tabular-nums">
                          {formatCurrency(Math.max(0, -progress.dailyPnl))} / {formatCurrency(progress.limit)}
                          {" · "}
                          {progress.usedPct.toFixed(0)}%
                        </span>
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--gridline)]">
                        <div
                          className={`h-full rounded-full ${lossBarColor(progress.severity)}`}
                          style={{ width: `${Math.min(100, progress.usedPct)}%` }}
                        />
                      </div>
                    </div>
                  );
                  })()
                )}
              </div>
            );
          })}
        </div>
      )}

      {strategiesPresent.length > 0 && (
        <div className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4">
          <h3 className="mb-3 text-sm font-semibold">Strategy reference</h3>
          <p className="mb-3 text-xs text-[color:var(--text-secondary)]">
            What each strategy above actually does -- see the{" "}
            <Link href="/backtests" className="text-[color:var(--series-1)] hover:underline">
              Backtests page
            </Link>{" "}
            for historical performance where one exists (some strategies, like the live VWAP+CPR
            one, are intentionally never backtested -- see their summary below for why).
          </p>
          <div className="flex flex-col gap-4">
            {strategiesPresent.map((name) => {
              const info = STRATEGY_INFO[name];
              return (
                <div key={name}>
                  <p className="text-sm font-medium">{info.label}</p>
                  <p className="text-sm text-[color:var(--text-secondary)]">{info.summary}</p>
                  {Object.keys(info.params).length > 0 && (
                    <dl className="mt-1 grid grid-cols-1 gap-x-4 gap-y-1 pl-3 text-xs sm:grid-cols-[max-content_1fr]">
                      {Object.entries(info.params).map(([key, param]) => (
                        <Fragment key={key}>
                          <dt className="text-[color:var(--text-muted)]">
                            {key} <span className="italic">({param.label})</span>
                          </dt>
                          <dd className="text-[color:var(--text-secondary)] sm:col-start-2">
                            {param.explain}
                          </dd>
                        </Fragment>
                      ))}
                    </dl>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
