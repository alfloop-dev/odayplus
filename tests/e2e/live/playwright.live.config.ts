import { defineConfig } from "@playwright/test";

// Explicit live-only entry. No local webServer, seed, request interception,
// retries, tracing, screenshots or credential-bearing artifacts.
export default defineConfig({
  testDir: ".",
  testMatch: "business-ui.live.ts",
  workers: 1,
  retries: 0,
  timeout: 120_000,
  use: {
    browserName: "chromium",
    headless: true,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
