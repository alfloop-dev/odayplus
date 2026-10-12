import { defineConfig } from "@playwright/test";
import base from "../../playwright.config";

// Supplemental geometry/axe checks stay outside the exact business-acceptance
// inventory in tests/e2e. Fixture geometry checks need only the web server.
export default defineConfig({
  ...base,
  testDir: "..",
  testMatch: /operator-(?:store-ops(?:-parity)?|growth-outcome-parity)\.spec\.ts/,
  workers: 1,
  retries: 0,
  webServer: Array.isArray(base.webServer) ? base.webServer.slice(1) : base.webServer,
});
