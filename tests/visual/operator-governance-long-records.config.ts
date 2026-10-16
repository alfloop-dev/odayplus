import { defineConfig } from "@playwright/test";
import base from "../../playwright.config";

// Keep the two reviewed synthetic long-record cases outside the fixed 130-case
// product acceptance inventory. Existing API/web fixture servers and guards stay
// inherited; this explicit supplemental lane does not grant release authority.
export default defineConfig({
  ...base,
  testDir: ".",
  testMatch: "operator-governance-long-records.spec.ts",
  fullyParallel: false,
  use: { ...base.use, screenshot: "only-on-failure", trace: "retain-on-failure" },
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { ...base.expect, timeout: 5_000 },
});
