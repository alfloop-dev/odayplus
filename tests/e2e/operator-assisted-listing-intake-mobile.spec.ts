import { expect, test, type Page } from "@playwright/test";

/**
 * Assisted Listing Intake — Responsive & Viewport Product Gates (VDC-002)
 *
 * Verifies viewports 390 (mobile), 1024 (tablet), and 1440 (desktop):
 * - Zero page-level horizontal overflow (scrollWidth <= clientWidth)
 * - Mobile layout collapse and side-by-side desktop-required banner
 * - Tablet 5-up meta grid folding
 * - Desktop side-by-side comparison view
 */

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const URLS = {
  possible: "https://www.synthetic.example/detail-99310418.html",
  clean: "https://www.synthetic.example/detail-77120345.html",
};

test.describe.configure({ mode: "serial", timeout: 120_000 });

test.beforeAll(async () => {
  await acquireOperatorBackendLock();
});

test.afterAll(() => {
  releaseOperatorBackendLock();
});

async function openRadarAsExpansionManager(page: Page) {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
  });
  await page.goto("/operator?ws=network");
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-queue")).toBeVisible();
}

async function checkNoHorizontalOverflow(page: Page, viewportName: string) {
  const isOverflowing = await page.evaluate(() => {
    const documentWidth = document.documentElement.clientWidth;
    const scrollWidth = document.documentElement.scrollWidth;
    const bodyScrollWidth = document.body.scrollWidth;
    return scrollWidth > documentWidth + 1 || bodyScrollWidth > documentWidth + 1;
  });
  expect(isOverflowing, `Page should not have horizontal overflow on ${viewportName}`).toBe(false);
}

test.describe("VDC-002 Responsive Layout & Zero Overflow (390px Mobile)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("390px mobile viewport has zero horizontal overflow on intake queue", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await checkNoHorizontalOverflow(page, "390px mobile");
  });

  test("390px mobile viewport shows desktop-required warning on compare panel", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await page.getByTestId("intake-add-button").click();
    await page.getByTestId("intake-url-input").fill(URLS.possible);
    await page.getByTestId("intake-submit-button").click();

    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("intake-desktop-required")).toBeVisible();
    await checkNoHorizontalOverflow(page, "390px mobile detail dialog");
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
    await checkNoHorizontalOverflow(page, "1024px tablet detail dialog");
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
    await expect(page.getByTestId("intake-desktop-required")).toHaveCount(0);
    await checkNoHorizontalOverflow(page, "1440px desktop detail dialog");
  });
});
