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
 * write calls fall back to an offline-labelled console audit. The server-side
 * conflict gate and approval-advances-state behaviour are proven by
 * tests/contract/test_operator_growth_api.py.
 */

import { expect, test } from "@playwright/test";

// ---------------------------------------------------------------------------
// 1. Workspace renders all sections
// ---------------------------------------------------------------------------

test("Growth workspace renders segments, recommendations, and actions from fixtures", async ({
  page,
}) => {
  await page.goto("/operator?ws=growth");

  await expect(page.getByTestId("app-shell")).toBeVisible();
  await expect(page.getByTestId("growth-workspace")).toBeVisible();
  await expect(page.getByTestId("growth-data-status")).toContainText("FRESH");

  const segmentTable = page.getByTestId("growth-segment-table");
  await expect(segmentTable).toContainText("都會晚餐高潛力組");
  await expect(segmentTable).toContainText("郊區午餐守成組");
  await expect(segmentTable).toContainText("宵夜外送流失組");

  const recTable = page.getByTestId("growth-recommendation-table");
  await expect(recTable).toContainText("晚餐套餐 +3% 加權調價");
  await expect(recTable).toContainText("宵夜外送費 -2% 試點");

  const itemTable = page.getByTestId("growth-item-table");
  await expect(itemTable).toContainText("都會晚餐套餐調價活動");
  await expect(itemTable).toContainText("宵夜外送費試點活動");
});

// ---------------------------------------------------------------------------
// 2. Segment filter
// ---------------------------------------------------------------------------

test("Growth workspace segment filtering works", async ({ page }) => {
  await page.goto("/operator?ws=growth");

  const filter = page.getByTestId("growth-segment-filter");
  await filter.getByText("都會晚餐高潛力組").click();
  await expect(page).toHaveURL(/segment=seg-metro-dinner/);

  const recTable = page.getByTestId("growth-recommendation-table");
  await expect(recTable).toContainText("晚餐套餐 +3% 加權調價");
  await expect(recTable).not.toContainText("宵夜外送費 -2% 試點");

  await filter.getByText("全部分群").click();
  await expect(page).toHaveURL(/ws=growth/);
  await expect(page).not.toHaveURL(/segment=/);
});

// ---------------------------------------------------------------------------
// 3. Three create-entry cards each open the builder prefilled for their kind
// ---------------------------------------------------------------------------

test("Three create-entry cards are present and open the five-step builder", async ({ page }) => {
  await page.goto("/operator?ws=growth");

  const cards = page.getByTestId("growth-entry-cards");
  await expect(cards.getByTestId("growth-entry-offpeak")).toBeVisible();
  await expect(cards.getByTestId("growth-entry-winback")).toBeVisible();
  await expect(cards.getByTestId("growth-entry-priceops")).toBeVisible();

  // Off-peak entry prefills the offpeak draft type in the builder.
  await cards.getByTestId("growth-entry-offpeak").click();
  const modal = page.getByTestId("growth-draft-modal");
  await expect(modal).toBeVisible();
  await expect(modal).toContainText("離峰促銷");
  await expect(page.getByTestId("growth-builder-step-1")).toBeVisible();
  await expect(page.getByTestId("growth-builder-steps")).toContainText("送核准");
});

// ---------------------------------------------------------------------------
// 4. Five-step builder: navigate all steps, reach conflict + review, submit
// ---------------------------------------------------------------------------

test("Five-step builder navigates all steps and emits a create audit on submit", async ({
  page,
}) => {
  await page.goto("/operator?ws=growth&builder=winback");

  const modal = page.getByTestId("growth-draft-modal");
  await expect(modal).toBeVisible();
  await expect(page.getByTestId("growth-builder-step-1")).toBeVisible();

  await modal.locator('input[name="name"]').fill("宵夜外送會員召回改進版");

  // step 1 -> 2 -> 3
  await modal.getByTestId("growth-builder-next").click();
  await expect(page.getByTestId("growth-builder-step-2")).toBeVisible();
  await modal.getByTestId("growth-builder-next").click();
  await expect(page.getByTestId("growth-builder-step-3")).toBeVisible();

  // step 3 -> 4 (risk/conflict step: server conflict gate panel is rendered)
  await modal.getByTestId("growth-builder-next").click();
  await expect(page.getByTestId("growth-builder-step-4")).toBeVisible();
  await expect(page.getByTestId("growth-conflict-panel")).toBeVisible();

  // step 4 -> 5 (review + submit)
  await modal.getByTestId("growth-builder-next").click();
  await expect(page.getByTestId("growth-builder-step-5")).toBeVisible();

  const consoleLogs: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().includes("[Console Audit]")) consoleLogs.push(msg.text());
  });

  await modal.getByTestId("growth-draft-submit").click();
  await expect(modal).not.toBeVisible();

  expect(consoleLogs.length).toBe(1);
  const parsed = JSON.parse(consoleLogs[0].replace("[Console Audit] ", ""));
  expect(parsed.action).toBe("CREATE_DRAFT");
  expect(parsed.kind).toBe("winback");
  expect(parsed.name).toBe("宵夜外送會員召回改進版");
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

test("HARD_CONSTRAINT_FAILED recommendation has disabled draft button", async ({ page }) => {
  await page.goto("/operator?ws=growth");

  const recTable = page.getByTestId("growth-recommendation-table");
  const hardConstraintRow = recTable.locator('tr:has-text("午餐主力商品 +9% 調價")');
  await expect(hardConstraintRow.getByText("建立草稿")).toHaveAttribute("aria-disabled", "true");

  const passRow = recTable.locator('tr:has-text("晚餐套餐 +3% 加權調價")');
  await expect(passRow.getByTestId("growth-draft-rec-9001")).not.toHaveAttribute(
    "aria-disabled",
    "true",
  );
});

// ---------------------------------------------------------------------------
// 6. Submit-for-approval flow on the DRAFT seed action (growth-7005)
// ---------------------------------------------------------------------------

test("Draft action exposes a submit-for-approval affordance", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7005");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("宵夜外送廣告增量草稿");

  const approvalPanel = page.getByTestId("growth-approval-panel");
  await expect(approvalPanel).toBeVisible();
  await expect(approvalPanel.getByTestId("growth-submit-approval")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 7. Ineffective / inconclusive actions blocked from direct closeout
// ---------------------------------------------------------------------------

test("Ineffective Growth Action is blocked from direct closeout", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7002");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("宵夜外送費試點活動");
  await expect(detailPanel).toContainText("無效");

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(closeoutPanel.getByTestId("growth-closeout-gate")).toHaveAttribute(
    "data-can-close",
    "false",
  );
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
  await expect(closeoutPanel.getByTestId("growth-required-action")).toContainText(
    "需先：執行 Rollback",
  );
});

test("Inconclusive Growth Action is blocked and shows required action", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7003");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("郊區午餐加價包觀察活動");
  await expect(detailPanel).toContainText("待判定");

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(closeoutPanel.getByTestId("growth-closeout-gate")).toHaveAttribute(
    "data-can-close",
    "false",
  );
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
  await expect(closeoutPanel.getByTestId("growth-required-action")).toContainText("需先：補強證據");
});

// ---------------------------------------------------------------------------
// 8. Effective action can be closed — console audit emitted
// ---------------------------------------------------------------------------

test("Effective Growth Action can be closed and emits console audit log", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7001");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("都會晚餐套餐調價活動");
  await expect(detailPanel).toContainText("有效");

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(closeoutPanel.getByTestId("growth-closeout-gate")).toHaveAttribute(
    "data-can-close",
    "true",
  );
  const closeButton = closeoutPanel.getByTestId("growth-close-button");
  await expect(closeButton).toBeEnabled();

  const consoleLogs: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().includes("[Console Audit]")) consoleLogs.push(msg.text());
  });

  await closeButton.click();
  await expect(closeoutPanel.getByTestId("growth-closeout-success")).toBeVisible();

  expect(consoleLogs.length).toBe(1);
  const parsed = JSON.parse(consoleLogs[0].replace("[Console Audit] ", ""));
  expect(parsed.action).toBe("APPROVE_CLOSEOUT");
  expect(parsed.itemId).toBe("growth-7001");
  expect(parsed.decisionId).toBe("dec-growth-7001");
});

// ---------------------------------------------------------------------------
// 9. Observing action shows PENDING — close button disabled
// ---------------------------------------------------------------------------

test("Observing Growth Action shows PENDING outcome and close is disabled", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7004");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("都會晚餐加點推薦活動");
  await expect(detailPanel).toContainText("觀察中");

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
});
