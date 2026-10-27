/**
 * E2E tests for the Growth workspace (ODP-OC-R4-004).
 *
 * Coverage:
 *   1. Workspace renders segments, recommendations, and actions
 *   2. Segment filter works (URL + table scoping)
 *   3. Three create-entry cards (offpeak / winback / priceops) open the builder
 *   4. Five-step Draft Builder: step navigation + conflict step + submit audit
 *   5. HARD_CONSTRAINT_FAILED recommendation blocks its draft button
 *   6. Submit-for-approval flow on a DRAFT action (approval panel)
 *   7. Ineffective / inconclusive actions are blocked from direct closeout
 *   8. Effective action can be closed — audit log is emitted
 *   9. Observing action shows PENDING outcome — close button disabled
 *
 * Assumes: Next.js dev server running at BASE_URL (default http://localhost:3000).
 * The workspace renders from embedded fixtures when the API is unreachable, so
 * writes require an actual acknowledged API response. The create case reloads
 * the persisted action and its audit binding; server-side replay and gates are proven by
 * tests/contract/test_operator_growth_api.py.
 */

import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { primeOperatorPersona } from "./_operatorPersona";

test.beforeEach(async ({ page }) => {
  await primeOperatorPersona(page, "marketing-manager");
});

/** Resize the already-observed state; never replay a business write for a size.
 * Attachments travel through the original JSON reporter/recorder/collector.
 * API-backed POC rows are still controlled evidence, not authenticated SQL/UAT.
 */
async function renderGrowthState(
  page: Page,
  testInfo: TestInfo,
  state: string,
  label: "Growth 營收成長" | "Growth 會員分群" | "Growth PriceOps" | "Growth 建立入口"
    | "Dialog Growth Draft Builder" | "Dialog Growth Outcome",
  controls: Locator[],
) {
  const originalViewport = page.viewportSize();
  const surface = page.locator(`[data-screen-label="${label}"]`);
  const dialog = surface.getByRole("dialog");
  try {
    for (const width of [1440, 1024, 390]) {
      await test.step(`${label}: ${state} at ${width}`, async () => {
        await page.setViewportSize({ width, height: 900 });
        const observation: Record<string, unknown> = {
          designSource: "docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/Oday Plus Operator Console.dc.html",
          designSha256: "cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d",
          label, state, viewport: page.viewportSize(), url: page.url(),
          evidenceScope: "controlled product-e2e fixture composition; not authenticated SQL, production or human UAT",
          referenceDifferences: "Current API fields, fixture content and server gates retained; PriceOps uses an internal horizontal scroller; no reference pixels substituted.",
          controls: [],
        };
        try {
          await expect(surface).toBeVisible();
          const panel = label.startsWith("Dialog ") ? dialog : surface;
          const box = await panel.boundingBox();
          observation.surface = box;
          expect(box).not.toBeNull();
          expect(box!.x).toBeGreaterThanOrEqual(0);
          expect(box!.width).toBeGreaterThan(0);
          expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
          if (label.startsWith("Dialog ")) {
            expect(box!.y).toBeGreaterThanOrEqual(0);
            expect(box!.y + box!.height).toBeLessThanOrEqual(901);
            const geometry = await dialog.evaluate(node => ({ client: node.clientWidth, scroll: node.scrollWidth }));
            expect(geometry.scroll).toBeLessThanOrEqual(geometry.client + 1);
          }
          for (const control of controls) {
            await control.scrollIntoViewIfNeeded();
            await expect(control).toBeVisible();
            await expect(control).toBeInViewport();
            const bounds = await control.boundingBox();
            expect(bounds).not.toBeNull();
            expect(bounds!.x).toBeGreaterThanOrEqual(0);
            expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 1);
            expect(bounds!.y).toBeGreaterThanOrEqual(0);
            expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(901);
            expect(bounds!.height).toBeGreaterThanOrEqual(24);
            const disabled = await control.isDisabled() || await control.getAttribute("aria-disabled") === "true";
            (observation.controls as unknown[]).push({ bounds, disabled });
            // Trial only: hit testing and scrolling, without navigation or writes.
            if (!disabled) await control.click({ trial: true });
          }
        } finally {
          observation.rendererSource = await page.getByTestId("growth-workspace").getAttribute("data-source");
          const path = testInfo.outputPath(`r7-growth-${state}-${width}.png`);
          await page.screenshot({ path, fullPage: true });
          await testInfo.attach(`${label} / ${state} / ${width} / controlled`, { path, contentType: "image/png" });
          await testInfo.attach(`r7-growth-${state}-${width}-observation`, {
            body: JSON.stringify(observation, null, 2), contentType: "application/json",
          });
        }
      });
    }
  } finally {
    if (originalViewport) await page.setViewportSize(originalViewport);
  }
}

// ---------------------------------------------------------------------------
// 1. Workspace renders all sections
// ---------------------------------------------------------------------------

test("Growth workspace renders segments, recommendations, and actions from fixtures", async ({
  page,
}, testInfo) => {
  await page.goto("/operator?ws=growth");

  // The Operator Console is full-bleed (package-6): no OpsBoard app-shell chrome.
  await expect(page.getByTestId("operator-console")).toBeVisible();
  await expect(page.getByTestId("app-shell")).toHaveCount(0);
  await expect(page.getByTestId("growth-workspace")).toBeVisible();
  await expect(page.getByTestId("growth-data-status")).toContainText("FRESH");

  // Default 活動 tab renders the Growth Action campaign cards.
  const itemTable = page.getByTestId("growth-item-table");
  await expect(itemTable).toContainText("都會晚餐套餐調價活動");
  await expect(itemTable).toContainText("宵夜外送費試點活動");
  await renderGrowthState(page, testInfo, "campaign", "Growth 營收成長", [
    page.getByTestId("growth-new-action"), page.getByTestId("growth-tab-segments"),
    page.getByTestId("growth-item-growth-7001"),
  ]);

  // 會員分群 tab renders the segment cards.
  await Promise.all([
    page.waitForURL(url => url.searchParams.get("gtab") === "segments"),
    page.getByTestId("growth-tab-segments").click(),
  ]);
  const segmentTable = page.getByTestId("growth-segment-table");
  await expect(segmentTable).toContainText("都會晚餐高潛力組");
  await expect(segmentTable).toContainText("郊區午餐守成組");
  await expect(segmentTable).toContainText("宵夜外送流失組");
  await expect(page.getByTestId("growth-tab-segments")).toHaveAttribute("aria-current", "page");
  await renderGrowthState(page, testInfo, "segments", "Growth 會員分群", [
    segmentTable.getByRole("link", { name: "建立活動草稿" }).first(),
    segmentTable.getByRole("link", { name: "建立活動草稿" }).last(),
  ]);

  // PriceOps tab renders the pricing recommendation table.
  await Promise.all([
    page.waitForURL(url => url.searchParams.get("gtab") === "priceops"),
    page.getByTestId("growth-tab-priceops").click(),
  ]);
  const recTable = page.getByTestId("growth-recommendation-table");
  await expect(recTable).toContainText("rec-9001");
  await expect(recTable).toContainText("rec-9002");
  await expect(page.getByTestId("growth-tab-priceops")).toHaveAttribute("aria-current", "page");
  await expect(recTable.getByRole("columnheader")).toHaveCount(9);
  await renderGrowthState(page, testInfo, "priceops", "Growth PriceOps", [
    page.getByTestId("growth-draft-rec-9001"),
    recTable.locator('tr:has-text("rec-9003")').getByText("建立定價草稿"),
  ]);
});

// ---------------------------------------------------------------------------
// 2. Segment card → Package 10 builder handoff
// ---------------------------------------------------------------------------

test("Growth segment card opens a scoped activity draft", async ({ page }, testInfo) => {
  await page.goto("/operator?ws=growth&gtab=segments");

  const segmentTable = page.getByTestId("growth-segment-table");
  const segmentCard = segmentTable
    .locator("article")
    .filter({ hasText: "都會晚餐高潛力組" });
  await segmentCard.getByRole("link", { name: "建立活動草稿" }).click();
  await expect(page).toHaveURL(/segment=seg-metro-dinner/);
  await expect(page).toHaveURL(/builder=offpeak/);
  await expect(page.getByTestId("growth-draft-modal")).toBeVisible();
  await renderGrowthState(page, testInfo, "segment-scoped-builder", "Dialog Growth Draft Builder", [
    page.getByTestId("growth-draft-modal").locator('input[name="name"]'),
    page.getByTestId("growth-builder-next"), page.getByTestId("growth-draft-close"),
  ]);
  await expect(page).toHaveURL(/segment=seg-metro-dinner/);
});

// ---------------------------------------------------------------------------
// 3. Three create-entry cards each open the builder prefilled for their kind
// ---------------------------------------------------------------------------

test("Three create-entry cards are present and open the five-step builder", async ({
  page,
}, testInfo) => {
  await page.goto("/operator?ws=growth");

  const cards = page.getByTestId("growth-entry-cards");
  await expect(cards.getByTestId("growth-entry-offpeak")).toBeVisible();
  await expect(cards.getByTestId("growth-entry-winback")).toBeVisible();
  await expect(cards.getByTestId("growth-entry-priceops")).toBeVisible();
  await renderGrowthState(page, testInfo, "create-entries", "Growth 建立入口", [
    cards.getByTestId("growth-entry-offpeak"), cards.getByTestId("growth-entry-winback"),
    cards.getByTestId("growth-entry-priceops"),
  ]);

  // Off-peak entry prefills the offpeak draft type in the builder.
  await cards.getByTestId("growth-entry-offpeak").click();
  const modal = page.getByTestId("growth-draft-modal");
  await expect(modal).toBeVisible();
  await expect(modal).toContainText("離峰促銷");
  await expect(page.getByTestId("growth-builder-step-1")).toBeVisible();
  await expect(page.getByTestId("growth-builder-steps")).toContainText(
    "送核准",
  );
  await renderGrowthState(page, testInfo, "entry-offpeak", "Dialog Growth Draft Builder", [
    modal.locator('input[name="name"]'), modal.getByTestId("growth-builder-next"),
    modal.getByTestId("growth-draft-close"),
  ]);
  // Exercise the other existing entries without creating or approving anything.
  for (const [kind, title] of [["winback", "會員召回"], ["priceops", "PriceOps 測試"]]) {
    await modal.getByTestId("growth-draft-close").click();
    await expect(modal).not.toBeVisible();
    await cards.getByTestId(`growth-entry-${kind}`).click();
    await expect(page).toHaveURL(new RegExp(`builder=${kind}`));
    await expect(modal).toContainText(title);
    await expect(page.getByTestId("growth-builder-step-1")).toBeVisible();
    await renderGrowthState(page, testInfo, `entry-${kind}`, "Dialog Growth Draft Builder", [
      modal.locator('input[name="name"]'), modal.getByTestId("growth-builder-next"),
      modal.getByTestId("growth-draft-close"),
    ]);
  }
});

// ---------------------------------------------------------------------------
// 4. Five-step builder: navigate all steps, reach conflict + review, submit
// ---------------------------------------------------------------------------

test("Five-step builder navigates all steps and emits a create audit on submit", async ({
  page,
}, testInfo) => {
  // Use the API's current segment and existing winback scope, not UI demo defaults.
  const [segmentsResponse, actionsResponse] = await Promise.all([
    page.request.get("/api/v1/operator/growth/segments"),
    page.request.get("/api/v1/operator/growth/actions"),
  ]);
  expect(segmentsResponse.ok()).toBe(true);
  expect(actionsResponse.ok()).toBe(true);
  const segments: { id: string; name: string; storeId?: string }[] = (await segmentsResponse.json()).items;
  const actions: { kind: string; segmentId: string; store: string }[] = (await actionsResponse.json()).items;
  const scope = actions.find(action => action.kind === "winback"
    && typeof action.store === "string" && action.store.trim().length > 0
    && segments.some(segment => segment.id === action.segmentId));
  expect(scope).toBeDefined();
  const segment = segments.find(segment => segment.id === scope!.segmentId)!;
  // POC multi-store segments have no storeId; retain the API's explicit scope label.
  const store = segment.storeId || scope!.store;
  await page.goto(`/operator?ws=growth&builder=winback&segment=${encodeURIComponent(segment.id)}`);
  // API arrival changes the builder context; wait for its actual remount before editing.
  await expect(page.getByTestId("growth-workspace")).toHaveAttribute("data-source", "api");

  const modal = page.getByTestId("growth-draft-modal");
  await expect(modal).toBeVisible();
  await expect(page.getByTestId("growth-builder-step-1")).toBeVisible();

  await modal.locator('input[name="name"]').fill("宵夜外送會員召回改進版");
  await modal.locator('input[name="store"]').fill(store);
  await renderGrowthState(page, testInfo, "builder-step-1", "Dialog Growth Draft Builder", [
    modal.locator('input[name="name"]'), modal.locator('input[name="store"]'),
    modal.getByTestId("growth-builder-next"),
  ]);
  await expect(modal.locator('input[name="store"]')).toHaveValue(store);

  // step 1 -> 2 -> 3
  await modal.getByTestId("growth-builder-next").click();
  await expect(page.getByTestId("growth-builder-step-2")).toBeVisible();
  await modal.locator('input[name="segmentId"]').fill(segment.id);
  await renderGrowthState(page, testInfo, "builder-step-2", "Dialog Growth Draft Builder", [
    modal.locator('input[name="segmentId"]'), modal.locator('select[name="channel"]'),
    modal.getByTestId("growth-builder-prev"), modal.getByTestId("growth-builder-next"),
  ]);
  await expect(modal.locator('input[name="segmentId"]')).toHaveValue(segment.id);
  await modal.getByTestId("growth-builder-next").click();
  await expect(page.getByTestId("growth-builder-step-3")).toBeVisible();
  // Observe existing inputs only; do not invent financial/ROI values for layout.
  await renderGrowthState(page, testInfo, "builder-step-3", "Dialog Growth Draft Builder", [
    modal.locator('input[name="targetLift"]'), modal.locator('input[name="budget"]'),
    modal.locator('textarea[name="rollbackPlan"]'), modal.getByTestId("growth-builder-next"),
  ]);

  // step 3 -> 4 (risk/conflict step: server conflict gate panel is rendered)
  await modal.getByTestId("growth-builder-next").click();
  await expect(page.getByTestId("growth-builder-step-4")).toBeVisible();
  await expect(page.getByTestId("growth-conflict-panel")).toBeVisible();
  await renderGrowthState(page, testInfo, "builder-step-4-conflicts", "Dialog Growth Draft Builder", [
    modal.getByTestId("growth-builder-prev"), modal.getByTestId("growth-builder-next"),
  ]);

  // step 4 -> 5 (review + submit)
  await modal.getByTestId("growth-builder-next").click();
  await expect(page.getByTestId("growth-builder-step-5")).toBeVisible();
  await renderGrowthState(page, testInfo, "builder-step-5-review", "Dialog Growth Draft Builder", [
    modal.getByTestId("growth-builder-prev"), modal.getByTestId("growth-draft-submit"),
    modal.getByTestId("growth-draft-submit-approval"),
  ]);

  const responsePromise = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/operator/growth/actions");
  await modal.getByTestId("growth-draft-submit").click();
  const response = await responsePromise;
  expect(response.ok()).toBe(true);
  const created = await response.json();
  expect(created.status).toBe("DRAFT");
  expect(created.kind).toBe("winback");
  expect(created.name).toBe("宵夜外送會員召回改進版");
  expect(created.audit.correlationId).toBeTruthy();
  await expect(modal).not.toBeVisible();
  const stored = await page.request.get(`/api/v1/operator/growth/actions/${created.id}`);
  expect(stored.ok()).toBe(true);
  const persisted = await stored.json();
  expect(persisted.id).toBe(created.id);
  expect(persisted.name).toBe(created.name);
  expect(persisted.segmentId).toBe(segment.id);
  expect(persisted.store).toBe(store);
  expect(persisted.status).toBe("DRAFT");
  expect(persisted.audit).toEqual(created.audit);

});

test("Growth builder can be dismissed without submitting", async ({ page }) => {
  await page.goto("/operator?ws=growth&builder=offpeak");
  const modal = page.getByTestId("growth-draft-modal");
  await expect(modal).toBeVisible();
  await modal.getByTestId("growth-draft-close").click();
  await expect(modal).not.toBeVisible();
});

// ---------------------------------------------------------------------------
// 5. HARD_CONSTRAINT_FAILED blocks the recommendation draft button
// ---------------------------------------------------------------------------

test("HARD_CONSTRAINT_FAILED recommendation has disabled draft button", async ({
  page,
}) => {
  await page.goto("/operator?ws=growth&gtab=priceops");

  const recTable = page.getByTestId("growth-recommendation-table");
  const hardConstraintRow = recTable.locator('tr:has-text("rec-9003")');
  await expect(hardConstraintRow.getByText("建立定價草稿")).toHaveAttribute(
    "aria-disabled",
    "true",
  );

  const passRow = recTable.locator('tr:has-text("rec-9001")');
  await expect(
    passRow.getByTestId("growth-draft-rec-9001"),
  ).not.toHaveAttribute("aria-disabled", "true");
});

// ---------------------------------------------------------------------------
// 6. Submit-for-approval flow on the DRAFT seed action (growth-7005)
// ---------------------------------------------------------------------------

test("Draft action exposes a submit-for-approval affordance", async ({
  page,
}, testInfo) => {
  await page.goto("/operator?ws=growth&item=growth-7005");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("宵夜外送廣告增量草稿");

  const approvalPanel = page.getByTestId("growth-approval-panel");
  await expect(approvalPanel).toBeVisible();
  await expect(
    approvalPanel.getByTestId("growth-submit-approval"),
  ).toBeVisible();
  await renderGrowthState(page, testInfo, "draft-approval-affordance", "Growth 營收成長", [
    approvalPanel.getByTestId("growth-submit-approval"),
  ]);
});

// ---------------------------------------------------------------------------
// 7. Ineffective / inconclusive actions blocked from direct closeout
// ---------------------------------------------------------------------------

test("Ineffective Growth Action is blocked from direct closeout", async ({
  page,
}, testInfo) => {
  await page.goto("/operator?ws=growth&item=growth-7002");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("宵夜外送費試點活動");
  await expect(detailPanel).toContainText("無效");

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(
    closeoutPanel.getByTestId("growth-closeout-gate"),
  ).toHaveAttribute("data-can-close", "false");
  await closeoutPanel.getByTestId("growth-outcome-open").click();
  await page.getByLabel("判斷理由（寫入 Decision Log）").fill("合成覆核：目前仍需執行 Rollback，不得直接結案。");
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
  await expect(
    closeoutPanel.getByTestId("growth-required-action"),
  ).toContainText("需先：執行 Rollback");
  await renderGrowthState(page, testInfo, "outcome-ineffective-blocked", "Dialog Growth Outcome", [
    page.getByLabel("判斷理由（寫入 Decision Log）"), closeoutPanel.getByTestId("growth-close-button"),
    page.getByRole("button", { name: "關閉活動成效判斷" }),
  ]);
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
});

test("Inconclusive Growth Action is blocked and shows required action", async ({
  page,
}, testInfo) => {
  await page.goto("/operator?ws=growth&item=growth-7003");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("郊區午餐加價包觀察活動");
  await expect(detailPanel).toContainText("待判定");

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(
    closeoutPanel.getByTestId("growth-closeout-gate"),
  ).toHaveAttribute("data-can-close", "false");
  await closeoutPanel.getByTestId("growth-outcome-open").click();
  await page.getByLabel("判斷理由（寫入 Decision Log）").fill("合成覆核：目前證據不足，仍不得直接結案。");
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
  await expect(
    closeoutPanel.getByTestId("growth-required-action"),
  ).toContainText("需先：補強證據");
  await renderGrowthState(page, testInfo, "outcome-inconclusive-blocked", "Dialog Growth Outcome", [
    page.getByLabel("判斷理由（寫入 Decision Log）"), closeoutPanel.getByTestId("growth-close-button"),
    page.getByRole("button", { name: "關閉活動成效判斷" }),
  ]);
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
});

// ---------------------------------------------------------------------------
// 8. Effective action can be closed — console audit emitted
// ---------------------------------------------------------------------------

test("Effective Growth Action can be closed and emits console audit log", async ({
  page,
}, testInfo) => {
  await page.goto("/operator?ws=growth&item=growth-7001");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("都會晚餐套餐調價活動");
  await expect(detailPanel).toContainText("達標且證據充足");

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(
    closeoutPanel.getByTestId("growth-closeout-gate"),
  ).toHaveAttribute("data-can-close", "true");
  let writes = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/growth/actions/growth-7001/outcome")) writes += 1;
  });
  const trigger = closeoutPanel.getByTestId("growth-outcome-open");
  await trigger.focus();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "活動成效判斷" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("判斷理由（寫入 Decision Log）")).toBeFocused();
  const closeButton = closeoutPanel.getByTestId("growth-close-button");
  await expect(closeButton).toBeDisabled();
  expect(writes).toBe(0);
  await renderGrowthState(page, testInfo, "outcome-reason-required", "Dialog Growth Outcome", [
    dialog.getByLabel("判斷理由（寫入 Decision Log）"), closeButton,
    dialog.getByRole("button", { name: "關閉活動成效判斷" }),
  ]);
  await expect(closeButton).toBeDisabled();
  await dialog.getByLabel("判斷理由（寫入 Decision Log）").fill("已核對對照組、觀測增量與 L3 證據，確認活動成效。");
  await expect(closeButton).toBeEnabled();
  await renderGrowthState(page, testInfo, "outcome-ready", "Dialog Growth Outcome", [
    dialog.getByLabel("判斷理由（寫入 Decision Log）"), closeButton,
    dialog.getByRole("button", { name: "關閉活動成效判斷" }),
  ]);
  expect(writes).toBe(0);

  const consoleLogs: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().includes("[Console Audit]")) consoleLogs.push(msg.text());
  });

  await closeButton.click();
  await expect(
    closeoutPanel.getByTestId("growth-closeout-success"),
  ).toBeVisible();

  expect(consoleLogs.length).toBe(1);
  const parsed = JSON.parse(consoleLogs[0].replace("[Console Audit] ", ""));
  expect(parsed.action).toBe("APPROVE_CLOSEOUT");
  expect(parsed.itemId).toBe("growth-7001");
  expect(parsed.decisionId).toBe("dec-growth-7001");
  expect(writes).toBe(1);
  await renderGrowthState(page, testInfo, "outcome-acknowledged", "Dialog Growth Outcome", [
    closeButton, dialog.getByRole("button", { name: "關閉活動成效判斷" }),
  ]);
  await expect(dialog.getByLabel("判斷理由（寫入 Decision Log）")).toHaveAttribute("readonly", "");
  expect(writes).toBe(1);
  await dialog.getByRole("button", { name: "關閉活動成效判斷" }).click();
  await expect(trigger).toBeFocused();
});

// ---------------------------------------------------------------------------
// 9. Observing action stays in the lifecycle panel until its window matures
// ---------------------------------------------------------------------------

test("Observing Growth Action cannot enter closeout before its window matures", async ({
  page,
}, testInfo) => {
  await page.goto("/operator?ws=growth&item=growth-7004");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("都會晚餐加點推薦活動");
  await expect(detailPanel).toContainText("觀察中");

  const transitionPanel = page.getByTestId("growth-transition-panel");
  await expect(
    transitionPanel.getByTestId("growth-transition-action"),
  ).toBeDisabled();
  await expect(page.getByTestId("growth-closeout-panel")).toHaveCount(0);
  await renderGrowthState(page, testInfo, "observation-not-mature", "Growth 營收成長", [
    transitionPanel.getByTestId("growth-transition-action"),
  ]);
  await expect(page.getByTestId("growth-closeout-panel")).toHaveCount(0);
});
