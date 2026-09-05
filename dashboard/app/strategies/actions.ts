"use server";

import { revalidatePath } from "next/cache";
import { setBotConfigEnabled, updateBotConfigRiskParams } from "@/lib/db";

// The dashboard's one write path: toggling a (strategy, instrument)
// bot_config row on/off, and editing its risk params. Both actions also
// append to audit_log (inside lib/db.ts), per docs/db-schema.md.

export async function toggleBotConfigEnabled(id: string, enabled: boolean): Promise<void> {
  await setBotConfigEnabled(id, enabled);
  revalidatePath("/strategies");
}

export async function saveRiskParams(id: string, formData: FormData): Promise<void> {
  const maxPositionSize = Number(formData.get("maxPositionSize"));
  const dailyLossLimit = Number(formData.get("dailyLossLimit"));
  const virtualCapital = Number(formData.get("virtualCapital"));
  // A checkbox is only present in FormData at all when checked.
  const dailyLossLimitEnabled = formData.get("dailyLossLimitEnabled") === "on";

  if (![maxPositionSize, dailyLossLimit, virtualCapital].every(Number.isFinite)) {
    throw new Error("Risk params must be numbers");
  }

  await updateBotConfigRiskParams(id, {
    maxPositionSize,
    dailyLossLimit,
    virtualCapital,
    dailyLossLimitEnabled,
  });
  revalidatePath("/strategies");
}
