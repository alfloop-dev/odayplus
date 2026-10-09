import { defineConfig } from "@playwright/test";
import base from "../../playwright.config";

// Supplemental geometry/axe checks stay outside the exact business-acceptance
// inventory in tests/e2e. Both Store suites mock API calls; only web is needed.
export default defineConfig({
  ...base,
  testDir: "..",
  testMatch: /operator-store-ops(?:-parity)?\.spec\.ts/,
  workers: 1,
  retries: 0,
  webServer: Array.isArray(base.webServer) ? base.webServer.slice(1) : base.webServer,
});
