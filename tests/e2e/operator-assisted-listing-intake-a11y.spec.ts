import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

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

import { loginNativeSession, nativeBrowserInput, openPgScenario, submitNativeURL } from "./_intakeScorePg";

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

test.describe.configure({ mode: "serial", timeout: 120_000 });
// Retained PG cases bind their actual fixture actor/tenant, not the global
// all-product identity. Native form consumers still require their own session.
test.use({ extraHTTPHeaders: {} });

test.beforeAll(async () => {
  await acquireOperatorBackendLock();
});

test.afterAll(() => {
  releaseOperatorBackendLock();
});

test.describe("VDC-003 Keyboard Navigation & Focus Management", () => {
  test("canonical native: focus lands inside dialog on open and Escape key restores focus to trigger button", async ({
    page,
  }) => {
    const input = nativeBrowserInput();
    await loginNativeSession(page, input);

    const addButton = page.getByTestId("intake-add-button");
    await addButton.click();
    await expect(page.getByTestId("intake-add-dialog")).toBeVisible();

    // Focus trapped inside dialog
    await expect(page.getByTestId("intake-url-input")).toBeFocused();

    // Escape closes dialog and restores focus
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("intake-add-dialog")).toBeHidden();
    await expect(addButton).toBeFocused();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(new URL(page.url()).searchParams.get("dialog")).toBeNull();
  });

  test("canonical native: dialog controls are navigable via Tab and Enter", async ({ page }) => {
    const input = nativeBrowserInput();
    await loginNativeSession(page, input);
    await page.getByTestId("intake-add-button").click();
    await expect(page.getByTestId("intake-add-dialog")).toBeVisible();

    await expect(page.getByTestId("intake-url-input")).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByTestId("intake-add-dialog").getByRole("button", { name: "關閉", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByTestId("intake-url-input")).toBeFocused();
    // URL alone is not a valid command. Tab through the actual explicit scope
    // and reason fields, then activate the same submit owner via Enter.
    await page.keyboard.press("Tab");
    await expect(page.getByRole("combobox", { name: /^正式收件範圍/ })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("送件原因", { exact: true })).toBeFocused();
    const { receipt } = await submitNativeURL(page, input, input.fresh_urls.keyboard,
      "鍵盤操作正式收件表單，明確選取已讀範圍並驗證已送出回執。", "enter");
    await expect(page.getByTestId("intake-detail-id")).toHaveText(receipt.intake_id);
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已送出");
  });
});

test.describe("VDC-003 Screen Reader Landmarks & Accessibility Attributes", () => {
  test("canonical native: screen-reader stage labels and aria landmarks are correctly populated", async ({
    page,
  }) => {
    const input = nativeBrowserInput();
    await loginNativeSession(page, input);

    await expect(page.locator('[data-screen-label="Listing Inbox 收件匣"]')).toBeVisible();

    // Add dialog screen label
    await page.getByTestId("intake-add-button").click();
    await expect(page.locator('[data-screen-label="Dialog 從網址新增物件"]')).toBeVisible();
    await expect(page.getByRole("dialog", { name: "從網址新增物件", exact: true })).toHaveAttribute("aria-modal", "true");
    const { receipt } = await submitNativeURL(page, input, input.fresh_urls.landmarks,
      "以正式收件實際送件回執核對讀屏標籤與收件處理詳情地標。");

    // Detail dialog screen label
    await expect(page.locator('[data-screen-label="Intake 收件處理詳情頁"]')).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("region", { name: "收件處理詳情", exact: true })).toBeVisible();
    await expect(page.getByTestId("intake-detail-id")).toHaveText(receipt.intake_id);
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已送出");
    await expect(page.getByTestId("native-intake-stage-timeline")).toContainText(receipt.transition_id);
  });

  test("retained queue respects prefers-reduced-motion media query", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const { api } = await openPgScenario(page, { entry: "inbox", retainedHistory: "possible-match" });
    try {
      await expect(page.getByTestId("intake-add-button")).toBeVisible();
      expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    } finally { await api.dispose(); }
  });
});

test.describe("VDC-003 Axe Automated Accessibility Scan", () => {
  test("retained populated intake queue passes axe accessibility scan", async ({ page }) => {
    const { api } = await openPgScenario(page, { entry: "inbox", retainedHistory: "possible-match" });
    try {
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
    } finally { await api.dispose(); }
  });

  test("retained intake detail passes axe accessibility scan", async ({ page }) => {
    // Existing comparison history, not a new URL, fetched page or native READY.
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
    await expect(page.getByTestId("intake-detail-id")).toHaveText(scenario.intake_id);
    await expect(page.getByTestId("compare-table-grid")).toBeVisible();

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
    } finally { await api.dispose(); }
  });
});
