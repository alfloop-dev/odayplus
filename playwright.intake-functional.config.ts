import { defineConfig, devices } from "@playwright/test";

const WEB_PORT = Number(process.env.OPSBOARD_PORT ?? 13199);

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "operator-assisted-listing-intake-functional-closure.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: "list",
  timeout: 120_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.ODP_WEB_BASE_URL ?? `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
