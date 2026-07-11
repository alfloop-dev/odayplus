import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the OpsBoard shell and the API-bound UI suite.
 *
 * Two servers boot: the FastAPI backend (so e2e can prove a backend state
 * change reaches the UI — ODP-PV-010) and the web dev server, which is told
 * where the API lives via ODP_API_BASE_URL. The shell smoke tests still run
 * without auth (ODP-R0-004), and unbound routes keep rendering their fixture
 * fallback if the API is unreachable.
 */
const WEB_PORT = Number(process.env.OPSBOARD_PORT ?? 3100);
const API_PORT = Number(process.env.ODP_API_PORT ?? 8099);
const BASE_URL = `http://localhost:${WEB_PORT}`;
const API_BASE_URL = process.env.ODP_API_BASE_URL ?? `http://127.0.0.1:${API_PORT}`;
const REUSE_EXISTING_SERVER = process.env.ODP_PLAYWRIGHT_REUSE_EXISTING === "1" || !process.env.CI;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    extraHTTPHeaders: {
      "x-subject-id": "product-e2e-test",
      "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive",
    },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: [
    {
      command: `python3 -m uvicorn apps.api.oday_api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      url: `${API_BASE_URL}/platform/health`,
      timeout: 120_000,
      reuseExistingServer: REUSE_EXISTING_SERVER,
    },
    {
      command: `npm run dev --workspace=@oday-plus/web -- -p ${WEB_PORT}`,
      url: BASE_URL,
      timeout: 120_000,
      reuseExistingServer: REUSE_EXISTING_SERVER,
      env: { ODP_API_BASE_URL: API_BASE_URL },
    },
  ],
});
