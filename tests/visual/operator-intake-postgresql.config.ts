import { defineConfig } from "@playwright/test";
import path from "node:path";
import base from "../../playwright.config";

const api = process.env.ODP_SCORE_BROWSER_API_URL ?? "http://127.0.0.1:38299";
const web = process.env.ODP_SCORE_BROWSER_WEB_URL ?? "http://127.0.0.1:38300";
const reuse = process.env.ODP_SCORE_BROWSER_REUSE === "1";
const cwd = path.resolve(__dirname, "../..");

// A targeted invocation of the ORIGINAL canonical cases, not another copy or
// a replacement inventory. Requires an explicitly provisioned scratch PG DB.
export default defineConfig({
  ...base,
  testDir: "../e2e",
  testMatch: /operator-assisted-listing-intake\.spec\.ts/,
  grep: /canonical (4:|5 and 6:)/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: { ...base.use, baseURL: web, extraHTTPHeaders: {}, trace: "on" },
  webServer: [
    {
      command: `.venv/bin/python -m uvicorn tests.support.intake_score_browser_server:app --host 127.0.0.1 --port ${new URL(api).port}`,
      url: `${api}/_synthetic/health`, cwd, reuseExistingServer: reuse, timeout: 120_000,
      env: {
        ODP_SCORE_BROWSER_MODE: "isolated-synthetic",
        ODP_SCORE_BROWSER_DATABASE_URL_FILE: process.env.ODP_SCORE_BROWSER_DATABASE_URL_FILE ?? "/tmp/odayplus-score-browser-local-dsn",
        ODP_SCORE_BROWSER_CONTROL_KEY: process.env.ODP_SCORE_BROWSER_CONTROL_KEY ?? "synthetic-browser-control-20261013-local-only",
        ODP_INTAKE_SCORE_ATTEMPT_SIGNING_KEY: "synthetic-browser-attempt-key-20261013-local-only",
        ODP_INTAKE_SCORE_RETRY_POLICY_JSON: JSON.stringify({ policy_id: "synthetic-local-score-retry-v1", version: 1, max_attempts: 5, backoff_seconds: [10, 30, 120, 600] }),
        ODP_PRODUCT_MODE: "poc", ODP_DEPLOY_ENV: "e2e",
        ODP_AUDIT_WORM_SINK_URI: "file:///tmp/odayplus-score-browser-audit",
      },
    },
    {
      command: `npm run start --workspace=@oday-plus/web -- -p ${new URL(web).port}`,
      url: web, cwd, reuseExistingServer: reuse, timeout: 120_000,
      env: { ODP_API_BASE_URL: api, ODP_PRODUCT_MODE: "poc", ODP_DEPLOY_ENV: "e2e", ODP_E2E_MODE: "true", ODP_DATA_BINDING_MODE: "fixture" },
    },
  ],
});
