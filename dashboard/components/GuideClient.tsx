"use client";

import { LevelGauge } from "@/components/LevelGauge";
import { explainSignal } from "@/lib/signal-explain";
import { buildGaugeConfig } from "@/lib/strategy-gauge";
import { buildExampleGaugeConfig } from "@/lib/strategy-gauge-examples";
import { STRATEGY_INFO } from "@/lib/strategy-info";
import { timeAgo, toNumber } from "@/lib/format";
import type { BotConfig } from "@/lib/types";
import { Fragment } from "react";

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

export function GuideClient({ configs }: { configs: BotConfig[] }) {
  const configsByStrategy = new Map<string, BotConfig[]>();
  for (const config of configs) {
    if (!config.strategy_name) continue;
    const list = configsByStrategy.get(config.strategy_name) ?? [];
    list.push(config);
    configsByStrategy.set(config.strategy_name, list);
  }

  return (
    <div className="flex flex-col gap-6">
      {Object.entries(STRATEGY_INFO).map(([name, info]) => {
        const liveConfigs = configsByStrategy.get(name) ?? [];
        const exampleGauge = buildExampleGaugeConfig(name);

        return (
          <section
            key={name}
            className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4"
          >
            <h3 className="text-base font-semibold">{info.label}</h3>
            <p className="mt-1 max-w-2xl text-sm text-[color:var(--text-secondary)]">{info.summary}</p>

            {Object.keys(info.params).length > 0 && (
              <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
                {Object.entries(info.params).map(([key, param]) => (
                  <Fragment key={key}>
                    <dt className="text-[color:var(--text-muted)]">
                      {key} <span className="italic">({param.label})</span>
                    </dt>
                    <dd className="text-[color:var(--text-secondary)] sm:col-start-2">{param.explain}</dd>
                  </Fragment>
                ))}
              </dl>
            )}

            {exampleGauge && (
              <div className="mt-4 rounded-md bg-[color:var(--gridline)]/30 p-3">
                <p className="mb-1 text-xs font-medium uppercase text-[color:var(--text-muted)]">
                  Illustrative example (made-up numbers)
                </p>
                <LevelGauge {...exampleGauge} />
              </div>
            )}

            {liveConfigs.length > 0 && (
              <div className="mt-4 flex flex-col gap-3">
                <p className="text-xs font-medium uppercase text-[color:var(--text-muted)]">
                  Live right now
                </p>
                {liveConfigs.map((config) => {
                  const gauge = buildGaugeConfig(config);
                  const explanation = explainSignal(
                    config.strategy_name ?? "",
                    config.signal_indicators,
                    config.strategy_params,
                    config.signal_ltp
                  );
                  return (
                    <div
                      key={config.id}
                      className="rounded-md border border-[color:var(--border-hairline)] p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">{config.instrument_symbol}</span>
                        <span className="rounded bg-[color:var(--gridline)] px-1.5 py-0.5 text-xs uppercase text-[color:var(--text-secondary)]">
                          {config.mode}
                        </span>
                        <span
                          className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${signalBadgeClass(
                            config.last_signal
                          )}`}
                        >
                          {config.last_signal ?? "no data yet"}
                        </span>
                        <span className="text-xs text-[color:var(--text-muted)]">
                          checked {timeAgo(config.signal_checked_at)}
                          {config.signal_ltp
                            ? ` · LTP ${toNumber(config.signal_ltp).toLocaleString("en-IN")}`
                            : ""}
                        </span>
                      </div>
                      {config.last_signal && (
                        <p className="mt-2 text-sm text-[color:var(--text-secondary)]">{explanation}</p>
                      )}
                      {gauge && (
                        <div className="mt-2">
                          <LevelGauge {...gauge} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
