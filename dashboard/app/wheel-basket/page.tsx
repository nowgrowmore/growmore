import {
  getWheelBasketConfigs,
  getWheelBasketLegs,
  getWheelBasketPositions,
  getWheelBasketSelections,
} from "@/lib/db";
import { WheelBasketClient } from "@/components/WheelBasketClient";
import { toggleWheelBasketConfigEnabled } from "./actions";

export const dynamic = "force-dynamic";

export default async function WheelBasketPage() {
  const configs = await getWheelBasketConfigs();
  const [positions, legs, selections] = await Promise.all([
    Promise.all(configs.map((c) => getWheelBasketPositions(c.id))),
    Promise.all(configs.map((c) => getWheelBasketLegs(c.id))),
    Promise.all(configs.map((c) => getWheelBasketSelections(c.id))),
  ]);
  const positionsByConfigId = Object.fromEntries(configs.map((c, i) => [c.id, positions[i]]));
  const legsByConfigId = Object.fromEntries(configs.map((c, i) => [c.id, legs[i]]));
  const selectionsByConfigId = Object.fromEntries(configs.map((c, i) => [c.id, selections[i]]));

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Wheel Basket</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Paper-traded, dynamically-selected high-IV stock basket: an ATM put wheel with an
        RSI-scaled basis buffer on the covered call, restricted each cycle to a real per-stock IV
        ranking. See{" "}
        <code className="rounded bg-[color:var(--gridline)] px-1">
          docs/stock-options-results.md
        </code>{" "}
        for the backtested methodology this runs live. Paper only — no live order placement exists
        for this strategy.
      </p>
      {configs.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">
          No wheel-basket config yet — seed one directly in Postgres (the dashboard can only
          toggle <code className="rounded bg-[color:var(--gridline)] px-1">enabled</code> on an
          existing row, same as <code className="rounded bg-[color:var(--gridline)] px-1">bot_config</code>).
        </p>
      ) : (
        <WheelBasketClient
          configs={configs}
          positionsByConfigId={positionsByConfigId}
          legsByConfigId={legsByConfigId}
          selectionsByConfigId={selectionsByConfigId}
          onToggle={toggleWheelBasketConfigEnabled}
        />
      )}
    </div>
  );
}
