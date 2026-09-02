import { expect, test } from "@playwright/test";

// PREVIEW-ONLY SUITE. Run with:
//   PREVIEW_BASE_URL=https://<your-preview>.vercel.app pnpm test:e2e:preview
//
// This does NOT run against a local `next dev` server and is not part of
// the fast local loop (`pnpm test`). It exercises a real deployed Vercel
// Preview and its real Neon preview branch, per docs/architecture.md's
// deployment model. It is skipped entirely if PREVIEW_BASE_URL is unset so
// it never accidentally runs (or hangs) in CI/local runs that don't have a
// live preview to point at.

test.skip(!process.env.PREVIEW_BASE_URL, "PREVIEW_BASE_URL is not set — skipping preview smoke suite");

test.describe("preview smoke", () => {
  test("Overview page loads and renders its heading", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: /live paper p&l/i })).toBeVisible();
  });

  test("Backtests page loads and renders its heading", async ({ page }) => {
    const response = await page.goto("/backtests");
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: /backtest runs/i })).toBeVisible();
  });

  test("Trade Log page loads and renders its heading", async ({ page }) => {
    const response = await page.goto("/trades");
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: /trade log/i })).toBeVisible();
  });

  test("Strategies page loads and renders its heading", async ({ page }) => {
    const response = await page.goto("/strategies");
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: /strategy configuration/i })).toBeVisible();
  });
});
