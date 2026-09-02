import { getBotConfigs } from "@/lib/db";
import { toNumber } from "@/lib/format";
import { StrategyToggle } from "@/components/StrategyToggle";
import { RiskParamsForm } from "@/components/RiskParamsForm";
import { saveRiskParams, toggleBotConfigEnabled } from "./actions";

export const dynamic = "force-dynamic";

export default async function StrategiesPage() {
  const configs = await getBotConfigs();

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Strategy configuration</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Enable/disable a strategy for a given instrument and tune its risk guards.
        <code className="mx-1 rounded bg-[color:var(--gridline)] px-1">bot_config</code>
        is the single gate between &quot;backtested&quot; and &quot;paper trading live&quot; —
        review the run on the Backtests page before flipping this on.
      </p>

      {configs.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">No bot_config rows yet.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {configs.map((config) => (
            <div
              key={config.id}
              className="flex flex-col gap-3 rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-4 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex items-center gap-4">
                <StrategyToggle
                  configId={config.id}
                  initialEnabled={config.enabled}
                  onToggle={toggleBotConfigEnabled}
                />
                <div>
                  <div className="font-medium">{config.strategy_name}</div>
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
                action={saveRiskParams}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
