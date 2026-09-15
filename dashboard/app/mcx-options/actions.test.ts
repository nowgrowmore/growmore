import { beforeEach, describe, expect, it, vi } from "vitest";

// Both sibling routes (app/wheel-basket, app/strategies) have had this test
// since they were written; /mcx-options did not, so a typo in its
// revalidatePath argument would have failed silently -- the toggle would
// appear to work (the server action response re-renders the tree anyway) while
// the named path was never actually revalidated. Found by independent code
// review, 2026-09-15.

vi.mock("@/lib/db", () => ({
  setMCXOptionsConfigEnabled: vi.fn(),
}));
vi.mock("next/cache", () => ({
  revalidatePath: vi.fn(),
}));

import { setMCXOptionsConfigEnabled } from "@/lib/db";
import { revalidatePath } from "next/cache";
import { toggleMCXOptionsConfigEnabled } from "./actions";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("toggleMCXOptionsConfigEnabled", () => {
  it("delegates to setMCXOptionsConfigEnabled and revalidates the page", async () => {
    await toggleMCXOptionsConfigEnabled("config-1", true);

    expect(setMCXOptionsConfigEnabled).toHaveBeenCalledWith("config-1", true);
    expect(revalidatePath).toHaveBeenCalledWith("/mcx-options");
  });

  it("passes false through unchanged", async () => {
    await toggleMCXOptionsConfigEnabled("config-2", false);
    expect(setMCXOptionsConfigEnabled).toHaveBeenCalledWith("config-2", false);
  });
});
