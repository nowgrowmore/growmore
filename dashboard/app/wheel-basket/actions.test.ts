import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/db", () => ({
  setWheelBasketConfigEnabled: vi.fn(),
}));
vi.mock("next/cache", () => ({
  revalidatePath: vi.fn(),
}));

import { setWheelBasketConfigEnabled } from "@/lib/db";
import { revalidatePath } from "next/cache";
import { toggleWheelBasketConfigEnabled } from "./actions";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("toggleWheelBasketConfigEnabled", () => {
  it("delegates to setWheelBasketConfigEnabled and revalidates the page", async () => {
    await toggleWheelBasketConfigEnabled("config-1", true);

    expect(setWheelBasketConfigEnabled).toHaveBeenCalledWith("config-1", true);
    expect(revalidatePath).toHaveBeenCalledWith("/wheel-basket");
  });

  it("passes false through unchanged", async () => {
    await toggleWheelBasketConfigEnabled("config-2", false);
    expect(setWheelBasketConfigEnabled).toHaveBeenCalledWith("config-2", false);
  });
});
