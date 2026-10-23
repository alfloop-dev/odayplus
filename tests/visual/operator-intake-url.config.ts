import { defineConfig } from "@playwright/test";
import path from "node:path";
import base from "../../playwright.config";

// Supplemental navigation proof on loopback synthetic data; does not replace
// the fixed acceptance inventory or authorize production persona journeys.
export default defineConfig({
  ...base,
  testDir: "..",
  testMatch: /operator-intake-url-parity\.spec\.ts/,
  workers: 1,
  retries: 0,
  fullyParallel: false,
  webServer: (Array.isArray(base.webServer) ? base.webServer : []).map((server) => ({
    ...server,
    command: process.env.ODP_INTAKE_URL_COMPILED === "1" && server.command.startsWith("npm run dev")
      ? `npm run start --workspace=@oday-plus/web -- -p ${Number(process.env.OPSBOARD_PORT ?? 3100)}` : server.command,
    cwd: path.resolve(__dirname, "../.."),
    reuseExistingServer: false,
    env: { ...server.env, ODP_PRODUCT_MODE: "poc", ODP_DEPLOY_ENV: "e2e", ODP_E2E_MODE: "true" },
  })),
});
