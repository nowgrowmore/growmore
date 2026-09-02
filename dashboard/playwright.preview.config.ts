import { defineConfig } from "@playwright/test";

// Preview-only smoke suite. This does NOT run in the fast local loop and is
// never started against a local `next dev` server — it exercises a real
// deployed Vercel Preview URL (and its real Neon preview branch) end to end.
//
// Usage:
//   PREVIEW_BASE_URL=https://growmore-dashboard-<hash>.vercel.app pnpm test:e2e:preview
//
// Preview deployments sit behind Vercel's SSO Deployment Protection, so
// requests need the "Protection Bypass for Automation" secret (Project
// Settings -> Deployment Protection). Set VERCEL_AUTOMATION_BYPASS_SECRET
// locally (see .env.local, gitignored) -- never commit the value.
//
// There is intentionally no `webServer` block here: we never boot a local
// server for this suite.
const baseURL = process.env.PREVIEW_BASE_URL;
const bypassSecret = process.env.VERCEL_AUTOMATION_BYPASS_SECRET;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 1,
  use: {
    baseURL,
    trace: "on-first-retry",
    extraHTTPHeaders: bypassSecret
      ? {
          "x-vercel-protection-bypass": bypassSecret,
          "x-vercel-set-bypass-cookie": "true",
        }
      : undefined,
  },
  reporter: [["list"]],
});
