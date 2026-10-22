import { defineConfig } from "@playwright/test";
import path from "node:path";
import base from "../../playwright.config";

const apiPort = Number(process.env.ODP_API_PORT ?? 8099);
// This separate fixture cannot replace the canonical six-role business suite.
export default defineConfig({
  ...base, testDir: "..", testMatch: /operator-priceops-command-parity\.spec\.ts/,
  workers: 1, retries: 0,
  webServer: [
    {
      command: `.venv/bin/python -m uvicorn tests.visual.priceops_command_server:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: path.resolve(__dirname, "../.."),
      url: `http://127.0.0.1:${apiPort}/_synthetic/evidence`, timeout: 120_000,
      reuseExistingServer: false,
      env: { ODP_PRODUCT_MODE: "poc", ODP_DEPLOY_ENV: "e2e", ODP_E2E_MODE: "true" },
    },
    ...(Array.isArray(base.webServer) ? base.webServer.slice(1) : []),
  ],
});
