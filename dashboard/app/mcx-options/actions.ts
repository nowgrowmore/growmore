"use server";

import { revalidatePath } from "next/cache";
import { setMCXOptionsConfigEnabled } from "@/lib/db";

// The mcx-options page's one write path: toggling the config on/off. Same
// convention as app/wheel-basket/actions.ts -- no UI to arm live mode here
// either (see docs/architecture.md).

export async function toggleMCXOptionsConfigEnabled(id: string, enabled: boolean): Promise<void> {
  await setMCXOptionsConfigEnabled(id, enabled);
  revalidatePath("/mcx-options");
}
