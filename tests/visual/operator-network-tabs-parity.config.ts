import { defineConfig } from "@playwright/test";
import base from "../../playwright.config";

// Supplemental geometry evidence must not change the exact business inventory.
export default defineConfig({
  ...base,
  testDir: ".",
  testMatch: /operator-network-tabs-parity\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
});
