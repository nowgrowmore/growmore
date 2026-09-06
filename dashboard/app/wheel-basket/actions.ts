"use server";

import { revalidatePath } from "next/cache";
import { setWheelBasketConfigEnabled } from "@/lib/db";

// The wheel-basket page's one write path: toggling the config on/off. Same
// convention as app/strategies/actions.ts -- no UI to arm live mode here
// either (see docs/architecture.md).

export async function toggleWheelBasketConfigEnabled(id: string, enabled: boolean): Promise<void> {
  await setWheelBasketConfigEnabled(id, enabled);
  revalidatePath("/wheel-basket");
}
