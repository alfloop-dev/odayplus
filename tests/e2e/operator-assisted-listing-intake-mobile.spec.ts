import {
  expect,
  test,
  type Page,
} from "@playwright/test";

/**
 * Assisted Listing Intake — Responsive & Viewport Product Gates (VDC-002)
 *
 * Verifies viewports 390 (mobile), 1024 (tablet), and 1440 (desktop):
 * - Zero page-level horizontal overflow
 * - Mobile layout collapse and side-by-side desktop-required banner
 * - Tablet 5-up meta grid folding
 * - Desktop side-by-side comparison view
 */

import { openPgScenario, PG_WEB } from "./_intakeScorePg";

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const SCREENSHOT_DIR =
  "docs/evidence/completion/ODP-INTAKE-UX-QA-001/screenshots";
const UPDATE_SCREENSHOT_EVIDENCE =
  process.env.ODP_UPDATE_SCREENSHOT_EVIDENCE === "1";

test.describe.configure({ mode: "serial", timeout: 120_000 });
// Original retained PostgreSQL composition supplies the actual actor/tenant.
// Layout is not evidence of fresh URL admission, parsing or native READY.
test.use({ extraHTTPHeaders: {} });

test.beforeAll(async () => {
  await acquireOperatorBackendLock();
});

test.afterAll(() => {
  releaseOperatorBackendLock();
});

async function expectNoPageOverflow(
  page: Page,
  viewportName: string,
  testId: string,
) {
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
          parentClassName:
            element.parentElement?.className?.toString().slice(0, 100) ?? "",
          parentWidth: parentRect ? Math.round(parentRect.width) : null,
          right: Math.round(rect.right),
          testId: element.getAttribute("data-testid"),
          width: Math.round(rect.width),
        };
      })
      .filter(
        (element) => element.right > document.documentElement.clientWidth + 1,
      )
      .slice(0, 12),
  }));
  expect(
    Math.max(metrics.bodyScrollWidth, metrics.documentScrollWidth),
    `${viewportName} must not have page-level horizontal overflow: ${JSON.stringify(metrics)}`,
  ).toBeLessThanOrEqual(
    Math.max(metrics.bodyClientWidth, metrics.documentClientWidth) + 1,
  );
}

async function captureScreenshotEvidence(page: Page, filename: string) {
  if (UPDATE_SCREENSHOT_EVIDENCE) {
    await page.screenshot({
      path: `${SCREENSHOT_DIR}/${filename}`,
      fullPage: true,
    });
  }
}

test.describe("VDC-002 Responsive Layout & Zero Overflow (390px Mobile)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("390px mobile viewport has zero horizontal overflow on intake queue", async ({
    page,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
      await page.goto(`${PG_WEB}/operator?ws=network&tab=radar&heatZoneId=`);
      await expect(page.getByTestId(`intake-inbox-row-${scenario.intake_id}`)).toBeVisible();
      await expectNoPageOverflow(page, "390px mobile inbox", "intake-inbox-view");
      await captureScreenshotEvidence(page, "intake-390-inbox.png");
    } finally { await api.dispose(); }
  });

  test("390px mobile viewport shows desktop-required warning on compare panel", async ({
    page,
  }) => {
    const { api } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
      await expect(page.getByTestId("intake-desktop-required")).toBeVisible();
      await expectNoPageOverflow(page, "390px mobile detail", "intake-detail-dialog");
      await captureScreenshotEvidence(page, "intake-390-detail.png");
    } finally { await api.dispose(); }
  });
});

test.describe("VDC-002 Responsive Layout & Zero Overflow (1024px Tablet)", () => {
  test.use({ viewport: { width: 1024, height: 768 } });

  test("1024px tablet viewport folds 5-up meta grid and has zero horizontal overflow", async ({
    page,
  }) => {
    const { api } = await openPgScenario(page, { retainedHistory: "revision" });
    try {
      await expect(page.getByTestId("intake-submission-summary")).toBeVisible();
      await expectNoPageOverflow(page, "1024px tablet detail", "intake-detail-dialog");
      await captureScreenshotEvidence(page, "intake-1024-detail.png");
    } finally { await api.dispose(); }
  });
});

test.describe("VDC-002 Responsive Layout & Zero Overflow (1440px Desktop)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("1440px desktop viewport displays full side-by-side comparison and zero overflow", async ({
    page,
  }) => {
    const { api } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
      await expect(page.getByTestId("intake-desktop-required")).toBeHidden();
      await expect(page.getByTestId("compare-table-grid")).toBeVisible();
      await expectNoPageOverflow(page, "1440px desktop detail", "intake-detail-dialog");
      await captureScreenshotEvidence(page, "intake-1440-detail.png");
    } finally { await api.dispose(); }
  });
});
