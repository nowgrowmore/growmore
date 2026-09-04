import { getBotConfigs } from "@/lib/db";
import { StrategiesClient } from "@/components/StrategiesClient";
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
        is the single gate between &quot;backtested&quot; and &quot;trading live&quot; — review the
        run on the Backtests page before flipping this on. Each card below shows what the strategy is
        doing right now and why, straight from its most recent tick.
      </p>
      <StrategiesClient configs={configs} onToggle={toggleBotConfigEnabled} onSaveRiskParams={saveRiskParams} />
    </div>
  );
}
