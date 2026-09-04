import { getLiveTradeLog, getTradeLog } from "@/lib/db";
import { TradesClient, type UnifiedTradeRow } from "@/components/TradesClient";
import type { LiveOrder, PaperOrder } from "@/lib/types";

export const dynamic = "force-dynamic";

function toUnifiedRow(order: PaperOrder | LiveOrder, tradeType: "paper" | "live"): UnifiedTradeRow {
  const isLive = tradeType === "live";
  return {
    id: order.id,
    tradeType,
    filled_at: order.filled_at,
    strategy_name: order.strategy_name,
    instrument_symbol: order.instrument_symbol,
    exchange_segment: order.exchange_segment,
    contract_expiry: order.contract_expiry,
    side: order.side,
    quantity: order.quantity,
    instrument_lot_size: order.instrument_lot_size,
    fill_price: isLive ? (order as LiveOrder).fill_price ?? "0" : (order as PaperOrder).simulated_fill_price,
    pnl: order.pnl,
    position_status: order.position_status,
    broker_order_id: isLive ? (order as LiveOrder).broker_order_id : undefined,
    order_status: isLive ? (order as LiveOrder).order_status : undefined,
  };
}

export default async function TradesPage() {
  const [paperOrders, liveOrders] = await Promise.all([getTradeLog(200), getLiveTradeLog(200)]);
  const rows = [
    ...paperOrders.map((o) => toUnifiedRow(o, "paper")),
    ...liveOrders.map((o) => toUnifiedRow(o, "live")),
  ].sort((a, b) => new Date(b.filled_at).getTime() - new Date(a.filled_at).getTime());

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Trade log</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Fills from both the paper trading engine and real orders (once any exist), most recent first.
        Contract details (lot size, expiry) are shown inline so there&rsquo;s no need to cross-check
        Dhan&rsquo;s own UI for them.
      </p>
      <TradesClient rows={rows} />
    </div>
  );
}
