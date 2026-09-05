import { getBacktestRuns, getBotConfigs, getLiveTradeLog, getTradeLog } from "@/lib/db";
import { CompareClient } from "@/components/CompareClient";

export const dynamic = "force-dynamic";

export default async function ComparePage() {
  const [configs, backtestRuns, paperOrders, liveOrders] = await Promise.all([
    getBotConfigs(),
    getBacktestRuns(),
    getTradeLog(500),
    getLiveTradeLog(500),
  ]);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Compare configs</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Pick any two strategy configs to see their params, current signal, backtest standing, and
        real trading history side by side.
      </p>
      <CompareClient configs={configs} backtestRuns={backtestRuns} paperOrders={paperOrders} liveOrders={liveOrders} />
    </div>
  );
}
