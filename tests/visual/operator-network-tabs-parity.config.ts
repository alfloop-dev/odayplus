import { defineConfig } from "@playwright/test";
import base from "../../playwright.config";

// Supplemental geometry evidence must not change the exact business inventory.
export default defineConfig({
  ...base,
  testDir: ".",
  testMatch: /operator-(network-tabs|intake-dialogs|intake-detail|review-decision|review-surface|radar-surface|findareas-surface|rebalance-surface|promotion|sitescore-states)-parity\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  webServer: Array.isArray(base.webServer)
    ? base.webServer.map((server) => ({ ...server, cwd: process.cwd() }))
    : base.webServer,
});
