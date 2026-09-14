import {
  getMCXOptionsConfigs,
  getMCXOptionsLegs,
  getMCXOptionsPositions,
  getMCXOptionsSelections,
} from "@/lib/db";
import { MCXOptionsClient } from "@/components/MCXOptionsClient";
import { toggleMCXOptionsConfigEnabled } from "./actions";

export const dynamic = "force-dynamic";

export default async function MCXOptionsPage() {
  const configs = await getMCXOptionsConfigs();
  const [positions, legs, selections] = await Promise.all([
    Promise.all(configs.map((c) => getMCXOptionsPositions(c.id))),
    Promise.all(configs.map((c) => getMCXOptionsLegs(c.id))),
    Promise.all(configs.map((c) => getMCXOptionsSelections(c.id))),
  ]);
  const positionsByConfigId = Object.fromEntries(configs.map((c, i) => [c.id, positions[i]]));
  const legsByConfigId = Object.fromEntries(configs.map((c, i) => [c.id, legs[i]]));
  const selectionsByConfigId = Object.fromEntries(configs.map((c, i) => [c.id, selections[i]]));

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">MCX Options</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        Paper-traded MCX Goldmini/Silvermini options-selling strategy: sell an OTM put at a
        regime-dependent delta, hold to expiry or assignment (never closed early), and roll an
        assigned futures position into a covered call. See{" "}
        <code className="rounded bg-[color:var(--gridline)] px-1">
          bot/research/mcx_options/engine.py
        </code>{" "}
        for the backtested methodology this runs live. Paper only — no live order placement exists
        for this strategy.
      </p>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        <strong className="text-[color:var(--critical-text)]">Futures contract rollover is
        not implemented.</strong> If an assigned long-futures position&apos;s contract month
        expires before its covered-call cycle resolves, the engine does nothing today — do not
        enable this strategy live across a contract-month boundary until that is built and
        tested (see <code className="rounded bg-[color:var(--gridline)] px-1">
          docs/technical-debt.md
        </code>).
      </p>
      {configs.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)]">
          No mcx-options config yet — seed one directly in Postgres (the dashboard can only
          toggle <code className="rounded bg-[color:var(--gridline)] px-1">enabled</code> on an
          existing row, same as <code className="rounded bg-[color:var(--gridline)] px-1">bot_config</code>).
        </p>
      ) : (
        <MCXOptionsClient
          configs={configs}
          positionsByConfigId={positionsByConfigId}
          legsByConfigId={legsByConfigId}
          selectionsByConfigId={selectionsByConfigId}
          onToggle={toggleMCXOptionsConfigEnabled}
        />
      )}
    </div>
  );
}
