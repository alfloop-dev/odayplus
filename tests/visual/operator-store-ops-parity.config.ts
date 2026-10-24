import { defineConfig } from "@playwright/test";
import base from "../../playwright.config";

// Supplemental geometry/axe checks stay outside the exact business-acceptance
// inventory in tests/e2e. Fixture geometry checks need only the web server.
export default defineConfig({
  ...base,
  testDir: "..",
  testMatch: /operator-(?:store-ops(?:-parity)?|growth-(?:outcome|builder|lifecycle|selection)-parity|network-review-parity|governance-(?:navigation|command-parity))\.spec\.ts/,
  use: { ...base.use, screenshot: "only-on-failure", trace: "retain-on-failure" },
  workers: 1,
  retries: 0,
  webServer: Array.isArray(base.webServer) ? base.webServer.slice(1) : base.webServer,
});
