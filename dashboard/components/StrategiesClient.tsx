"use client";

import { useState } from "react";
import { computePctChangeToday, formatPercent, toNumber } from "@/lib/format";
import { explainSignal } from "@/lib/signal-explain";
import { StrategyToggle } from "@/components/StrategyToggle";
import { RiskParamsForm } from "@/components/RiskParamsForm";
import { ModeFilter } from "@/components/ModeFilter";
import type { BotConfig } from "@/lib/types";

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

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleString();
}

interface StrategiesClientProps {
  configs: BotConfig[];
  onToggle: (id: string, enabled: boolean) => Promise<void>;
  onSaveRiskParams: (id: string, formData: FormData) => Promise<void>;
}

export function StrategiesClient({ configs, onToggle, onSaveRiskParams }: StrategiesClientProps) {
  const [mode, setMode] = useState<Mode>("live");
  const filtered = mode === "all" ? configs : configs.filter((c) => c.mode === mode);

  return (
    <div className="flex flex-col gap-4">
      <ModeFilter value={mode} onChange={setMode} options={MODE_OPTIONS} />

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
                      </div>
                    </div>
                  </div>
                  <RiskParamsForm
                    configId={config.id}
                    maxPositionSize={toNumber(config.max_position_size)}
                    dailyLossLimit={toNumber(config.daily_loss_limit)}
                    virtualCapital={toNumber(config.virtual_capital)}
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
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
