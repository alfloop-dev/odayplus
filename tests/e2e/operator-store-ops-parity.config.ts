import { defineConfig } from "@playwright/test";
import base from "../../playwright.config";

// Both Store Ops suites intercept API calls; no backend process is required.
export default defineConfig({
  ...base,
  testDir: ".",
  testMatch: /operator-store-ops(?:-parity)?\.spec\.ts/,
  workers: 1,
  retries: 0,
  webServer: Array.isArray(base.webServer) ? base.webServer.slice(1) : base.webServer,
});
