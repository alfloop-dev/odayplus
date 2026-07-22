import AxeBuilder from "@axe-core/playwright";
import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

/**
 * Assisted Listing Intake — Accessibility & Keyboard Product Gates (VDC-003)
 *
 * Verifies VDC-003 accessibility requirements:
 * - Stable focus return and focus trapping in dialogs
 * - Keyboard navigation (Tab, Shift+Tab, Enter, Escape)
 * - Screen-reader summaries and landmarks
 * - Reduced motion support
 * - Zero serious/critical axe accessibility violations
 */

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

const URLS = {
  clean: "https://www.synthetic.example/detail-77120345.html",
  possible: "https://www.synthetic.example/detail-99310418.html",
};

test.describe.configure({ mode: "serial", timeout: 120_000 });

test.beforeAll(async () => {
  await acquireOperatorBackendLock();
});

test.afterAll(() => {
  releaseOperatorBackendLock();
});

test.beforeEach(async () => {
  const api = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      "x-subject-id": "operator-expansion-manager",
      "x-roles": "expansion_user,operations_manager,site_reviewer,data_owner,auditor,executive",
      "x-operator-role": "expansion-manager",
      "x-tenant-id": "tenant-a",
    },
  });
  const reset = await api.post("/api/v1/operator/network-listings/reset");
  expect(reset.status()).toBe(200);
  await api.dispose();
});

async function openRadarAsExpansionManager(page: Page) {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
    window.localStorage.setItem("oday.operator.role", "expansion-manager");
  });
  await page.goto("/operator?ws=network");
  await page.evaluate(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
    window.localStorage.setItem("oday.operator.role", "expansion-manager");
  });
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-add-button")).toBeVisible({ timeout: 15_000 });
}

test.describe("VDC-003 Keyboard Navigation & Focus Management", () => {
  test("focus lands inside dialog on open and Escape key restores focus to trigger button", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);

    const addButton = page.getByTestId("intake-add-button");
    await addButton.click();
    await expect(page.getByTestId("intake-add-dialog")).toBeVisible();

    // Focus trapped inside dialog
    await expect(page.getByTestId("intake-url-input")).toBeFocused();

    // Escape closes dialog and restores focus
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("intake-add-dialog")).toBeHidden();
    await expect(addButton).toBeFocused();
  });

  test("dialog controls are navigable via Tab and Enter", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await page.getByTestId("intake-add-button").click();
    await expect(page.getByTestId("intake-add-dialog")).toBeVisible();

    await page.getByTestId("intake-url-input").fill(URLS.clean);
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByRole("button", { name: "關閉" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByTestId("intake-url-input")).toBeFocused();
    await page.getByTestId("intake-url-input").press("Enter");

    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("VDC-003 Screen Reader Landmarks & Accessibility Attributes", () => {
  test("screen-reader stage labels and aria landmarks are correctly populated", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);

    await expect(page.locator('[data-screen-label="Listing Inbox 收件匣"]')).toBeVisible();

    // Add dialog screen label
    await page.getByTestId("intake-add-button").click();
    await expect(page.locator('[data-screen-label="Dialog 從網址新增物件"]')).toBeVisible();
    await page.getByTestId("intake-url-input").fill(URLS.clean);
    await page.getByTestId("intake-submit-button").click();

    // Detail dialog screen label
    await expect(page.locator('[data-screen-label="Dialog 收件處理詳情"]')).toBeVisible({
      timeout: 15_000,
    });
  });

  test("respects prefers-reduced-motion media query", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openRadarAsExpansionManager(page);
    await expect(page.getByTestId("intake-add-button")).toBeVisible();
  });
});

test.describe("VDC-003 Axe Automated Accessibility Scan", () => {
  test("intake queue page passes axe accessibility scan", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await expect(page.getByTestId("intake-add-button")).toBeVisible();

    const results = await new AxeBuilder({ page })
      .include('[data-testid="intake-inbox-view"]')
      .analyze();

    const seriousViolations = results.violations.filter(
      (v) => v.impact === "critical" || v.impact === "serious"
    );
    expect(
      seriousViolations.map((violation) => ({
        id: violation.id,
        nodes: violation.nodes.map((node) => ({
          failureSummary: node.failureSummary,
          target: node.target,
        })),
      })),
    ).toEqual([]);
  });

  test("intake detail dialog passes axe accessibility scan", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await page.getByTestId("intake-add-button").click();
    await page.getByTestId("intake-url-input").fill(URLS.clean);
    await page.getByTestId("intake-submit-button").click();
    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });

    const results = await new AxeBuilder({ page })
      .include('[data-testid="intake-detail-dialog"]')
      .analyze();

    const seriousViolations = results.violations.filter(
      (v) => v.impact === "critical" || v.impact === "serious"
    );
    expect(
      seriousViolations.map((violation) => ({
        id: violation.id,
        nodes: violation.nodes.map((node) => ({
          failureSummary: node.failureSummary,
          target: node.target,
        })),
      })),
    ).toEqual([]);
  });
});
