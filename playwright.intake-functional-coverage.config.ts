import { defineConfig, devices } from "@playwright/test";

const WEB_PORT = Number(process.env.OPSBOARD_PORT ?? 13209);
const evidenceRoot =
  "docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/coverage";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "operator-assisted-listing-intake-functional-coverage.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  outputDir: `${evidenceRoot}/playwright-artifacts`,
  reporter: [
    ["list"],
    ["json", { outputFile: `${evidenceRoot}/playwright-results.json` }],
  ],
  timeout: 150_000,
  expect: {
    timeout: 20_000,
  },
  use: {
    ...devices["Desktop Chrome"],
    baseURL:
      process.env.ODP_WEB_BASE_URL ?? `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
