import { defineConfig } from "@playwright/test";
import path from "node:path";

// Dedicated synthetic PostgreSQL browser composition, outside the fixed
// 18-spec/130-case collector. Never reuse an unrelated local server.
const root = path.resolve(__dirname, "../..");
const database = process.env.ODP_SCORE_BROWSER_DATABASE_URL;
const control = process.env.ODP_SCORE_BROWSER_CONTROL_KEY;
if (process.env.ODP_SCORE_BROWSER_MODE !== "isolated-synthetic" || !database || !control || control.length < 32) {
  throw new Error("Explicit isolated personal-search browser configuration required");
}
const db = new URL(database);
if (!["postgres:", "postgresql:"].includes(db.protocol)
    || !["127.0.0.1", "localhost"].includes(db.hostname)
    || db.search || db.hash
    || !db.pathname.startsWith("/odp_score_browser_")) {
  throw new Error("Dedicated loopback synthetic PostgreSQL database required");
}
function localOrigin(value: string): URL {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port
      || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Dedicated loopback HTTP origin required");
  }
  return url;
}
const api = localOrigin(process.env.ODP_SCORE_BROWSER_API_URL ?? "http://127.0.0.1:8116");
const web = localOrigin(process.env.ODP_SCORE_BROWSER_WEB_URL ?? "http://127.0.0.1:3116");
if (api.port === web.port) throw new Error("API and web must use separate ports");
process.env.ODP_SCORE_BROWSER_API_URL = api.origin;
process.env.ODP_SCORE_BROWSER_WEB_URL = web.origin;

const fixture = {
  ODP_SCORE_BROWSER_MODE: "isolated-synthetic",
  ODP_SCORE_BROWSER_DATABASE_URL: database,
  ODP_SCORE_BROWSER_CONTROL_KEY: control,
  ODP_API_BASE_URL: api.origin,
  NEXT_PUBLIC_API_URL: api.origin,
  ODP_DATA_BINDING_MODE: "fixture",
  NEXT_PUBLIC_ODP_DATA_BINDING_MODE: "fixture",
  ODP_PRODUCT_MODE: "poc",
  NEXT_PUBLIC_ODP_PRODUCT_MODE: "poc",
  ODP_DEPLOY_ENV: "e2e",
  NEXT_PUBLIC_ODP_DEPLOY_ENV: "e2e",
  ODP_E2E_MODE: "true",
  NEXT_PUBLIC_ODP_E2E_MODE: "true",
  ODAY_ENV: "test",
  ODP_INTAKE_SCORE_ATTEMPT_SIGNING_KEY: "synthetic-browser-attempt-key-20261013-local-only",
  ODP_INTAKE_SCORE_RETRY_POLICY_JSON: JSON.stringify({ policy_id: "synthetic-local-score-retry-v1", version: 1,
    max_attempts: 5, backoff_seconds: [10, 30, 120, 600] }),
};

export default defineConfig({
  testDir: ".",
  testMatch: "operator-personal-listing-search.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  forbidOnly: true,
  reporter: "list",
  use: { headless: true, screenshot: "only-on-failure", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: [
    {
      command: `.venv/bin/python -B -m uvicorn tests.support.listing_search_browser_server:app --host 127.0.0.1 --port ${api.port}`,
      cwd: root,
      url: `${api.origin}/_synthetic/health`,
      env: { ...fixture, ODP_E2E_MODE: "false" },
      timeout: 120_000,
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGINT", timeout: 30_000 },
    },
    {
      // The workflow builds this fixture separately before starting Playwright.
      command: `npm run start --workspace=@oday-plus/web -- --hostname 127.0.0.1 --port ${web.port}`,
      cwd: root,
      url: web.origin,
      env: fixture,
      timeout: 120_000,
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGINT", timeout: 30_000 },
    },
  ],
});
