import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the OpsBoard shell smoke test.
 * The shell renders with no auth backend, so the web dev server is the only
 * dependency (ODP-R0-004 acceptance: "smoke can open shell without auth backend").
 */
const PORT = Number(process.env.OPSBOARD_PORT ?? 3100);
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: `npm run dev --workspace=@oday-plus/web -- -p ${PORT}`,
    url: BASE_URL,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
  },
});
