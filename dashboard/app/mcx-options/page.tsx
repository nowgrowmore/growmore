import {
  getMCXOptionsConfigs,
  getMCXOptionsLegsForConfigs,
  getMCXOptionsPositionsForConfigs,
  getMCXOptionsSelectionsForConfigs,
} from "@/lib/db";
import { MCXOptionsClient } from "@/components/MCXOptionsClient";
import { toggleMCXOptionsConfigEnabled } from "./actions";

export const dynamic = "force-dynamic";

export default async function MCXOptionsPage() {
  const configs = await getMCXOptionsConfigs();
  // Three batched queries regardless of how many configs exist -- these
  // return rows already keyed by config_id, so there is no index-zipping
  // step that could silently attribute GOLDM's positions to SILVERM.
  const configIds = configs.map((c) => c.id);
  const [positionsByConfigId, legsByConfigId, selectionsByConfigId] = await Promise.all([
    getMCXOptionsPositionsForConfigs(configIds),
    getMCXOptionsLegsForConfigs(configIds),
    getMCXOptionsSelectionsForConfigs(configIds),
  ]);

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
        Futures contract rollover is implemented: if an assigned long-futures
        position&apos;s contract month expires before its covered-call cycle resolves, the
        engine marks the old exposure, charges a round-trip roll cost, and rolls it to the
        new contract automatically (shown below as a &quot;roll&quot; leg). The flat
        per-lot roll-cost placeholder is still not a real sourced roll-spread figure — see{" "}
        <code className="rounded bg-[color:var(--gridline)] px-1">
          docs/technical-debt.md
        </code>.
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
