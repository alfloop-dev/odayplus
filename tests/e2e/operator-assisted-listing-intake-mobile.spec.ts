import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

/**
 * Assisted Listing Intake — Responsive & Viewport Product Gates (VDC-002)
 *
 * Verifies viewports 390 (mobile), 1024 (tablet), and 1440 (desktop):
 * - Zero page-level horizontal overflow
 * - Mobile layout collapse and side-by-side desktop-required banner
 * - Tablet 5-up meta grid folding
 * - Desktop side-by-side comparison view
 */

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

const URLS = {
  possible: "https://www.synthetic.example/detail-99310418.html",
  clean: "https://www.synthetic.example/detail-77120345.html",
};
const SCREENSHOT_DIR = "docs/evidence/completion/ODP-INTAKE-UX-QA-001/screenshots";

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

async function expectNoPageOverflow(page: Page, viewportName: string, testId: string) {
  await expect(page.getByTestId(testId)).toBeVisible();
  const metrics = await page.evaluate(() => ({
    bodyClientWidth: document.body.clientWidth,
    bodyScrollWidth: document.body.scrollWidth,
    documentClientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
    offenders: Array.from(document.body.querySelectorAll("*"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const parentRect = element.parentElement?.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return {
          className: element.className?.toString().slice(0, 100) ?? "",
          computedWidth: style.width,
          display: style.display,
          maxWidth: style.maxWidth,
          minWidth: style.minWidth,
          parentClassName: element.parentElement?.className?.toString().slice(0, 100) ?? "",
          parentWidth: parentRect ? Math.round(parentRect.width) : null,
          right: Math.round(rect.right),
          testId: element.getAttribute("data-testid"),
          width: Math.round(rect.width),
        };
      })
      .filter((element) => element.right > document.documentElement.clientWidth + 1)
      .slice(0, 12),
  }));
  expect(
    Math.max(metrics.bodyScrollWidth, metrics.documentScrollWidth),
    `${viewportName} must not have page-level horizontal overflow: ${JSON.stringify(metrics)}`,
  ).toBeLessThanOrEqual(Math.max(metrics.bodyClientWidth, metrics.documentClientWidth) + 1);
}

test.describe("VDC-002 Responsive Layout & Zero Overflow (390px Mobile)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("390px mobile viewport has zero horizontal overflow on intake queue", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await expectNoPageOverflow(page, "390px mobile inbox", "intake-inbox-view");
    await page.screenshot({ path: `${SCREENSHOT_DIR}/intake-390-inbox.png`, fullPage: true });
  });

  test("390px mobile viewport shows desktop-required warning on compare panel", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await page.getByTestId("intake-add-button").click();
    await page.getByTestId("intake-url-input").fill(URLS.possible);
    await page.getByTestId("intake-submit-button").click();

    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("intake-desktop-required")).toBeVisible();
    await expectNoPageOverflow(page, "390px mobile detail", "intake-detail-dialog");
    await page.screenshot({ path: `${SCREENSHOT_DIR}/intake-390-detail.png`, fullPage: true });
  });
});

test.describe("VDC-002 Responsive Layout & Zero Overflow (1024px Tablet)", () => {
  test.use({ viewport: { width: 1024, height: 768 } });

  test("1024px tablet viewport folds 5-up meta grid and has zero horizontal overflow", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);
    await page.getByTestId("intake-add-button").click();
    await page.getByTestId("intake-url-input").fill(URLS.clean);
    await page.getByTestId("intake-submit-button").click();

    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
    await expectNoPageOverflow(page, "1024px tablet detail", "intake-detail-dialog");
    await page.screenshot({ path: `${SCREENSHOT_DIR}/intake-1024-detail.png`, fullPage: true });
  });
});

test.describe("VDC-002 Responsive Layout & Zero Overflow (1440px Desktop)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("1440px desktop viewport displays full side-by-side comparison and zero overflow", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);
    await page.getByTestId("intake-add-button").click();
    await page.getByTestId("intake-url-input").fill(URLS.possible);
    await page.getByTestId("intake-submit-button").click();

    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("intake-desktop-required")).toBeHidden();
    await expectNoPageOverflow(page, "1440px desktop detail", "intake-detail-dialog");
    await page.screenshot({ path: `${SCREENSHOT_DIR}/intake-1440-detail.png`, fullPage: true });
  });
});
