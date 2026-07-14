/**
 * E2E tests for the Growth workspace (ODP-OC-R4-004).
 *
 * Coverage:
 *   1. Growth workspace renders segments, recommendations, and actions
 *   2. Segment filter works (URL + table scoping)
 *   3. HARD_CONSTRAINT_FAILED recommendation blocks draft creation
 *   4. Ineffective action is blocked from direct closeout
 *   5. Effective action can be closed — audit log is emitted
 *   6. Five-step Draft Builder modal: fill fields → submit → console audit
 *   7. PriceOps "建立草稿" button opens the draft modal for the right recommendation
 *   8. Observing action shows PENDING outcome — close button disabled
 *
 * Assumes: Next.js dev server running at BASE_URL (default http://localhost:3000).
 */

import { expect, test } from "@playwright/test";

// ---------------------------------------------------------------------------
// 1. Workspace renders all sections
// ---------------------------------------------------------------------------

test("Growth workspace renders segments, recommendations, and actions from fixtures", async ({
  page,
}) => {
  await page.goto("/operator?ws=growth");

  // App shell and workspace root
  await expect(page.getByTestId("app-shell")).toBeVisible();
  await expect(page.getByTestId("growth-workspace")).toBeVisible();
  await expect(page.getByTestId("growth-data-status")).toContainText("FRESH");

  // Segment table
  const segmentTable = page.getByTestId("growth-segment-table");
  await expect(segmentTable).toContainText("都會晚餐高潛力組");
  await expect(segmentTable).toContainText("郊區午餐守成組");
  await expect(segmentTable).toContainText("宵夜外送流失組");

  // PriceOps recommendation table
  const recTable = page.getByTestId("growth-recommendation-table");
  await expect(recTable).toContainText("晚餐套餐 +3% 加權調價");
  await expect(recTable).toContainText("宵夜外送費 -2% 試點");

  // Growth Actions list
  const itemTable = page.getByTestId("growth-item-table");
  await expect(itemTable).toContainText("都會晚餐套餐調價活動");
  await expect(itemTable).toContainText("宵夜外送費試點活動");
});

// ---------------------------------------------------------------------------
// 2. Segment filter
// ---------------------------------------------------------------------------

test("Growth workspace segment filtering works", async ({ page }) => {
  await page.goto("/operator?ws=growth");

  // Click on a segment chip
  const filter = page.getByTestId("growth-segment-filter");
  await filter.getByText("都會晚餐高潛力組").click();

  // URL should carry the segment param
  await expect(page).toHaveURL(/segment=seg-metro-dinner/);

  // Recommendations scoped to the selected segment
  const recTable = page.getByTestId("growth-recommendation-table");
  await expect(recTable).toContainText("晚餐套餐 +3% 加權調價");
  await expect(recTable).not.toContainText("宵夜外送費 -2% 試點");

  // "全部分群" link resets filter
  await filter.getByText("全部分群").click();
  await expect(page).toHaveURL(/ws=growth/);
  await expect(page).not.toHaveURL(/segment=/);
});

// ---------------------------------------------------------------------------
// 3. HARD_CONSTRAINT_FAILED blocks draft creation
// ---------------------------------------------------------------------------

test("HARD_CONSTRAINT_FAILED recommendation has disabled draft button", async ({ page }) => {
  await page.goto("/operator?ws=growth");

  const recTable = page.getByTestId("growth-recommendation-table");

  // rec-9003 is HARD_CONSTRAINT_FAILED
  const hardConstraintRow = recTable.locator('tr:has-text("午餐主力商品 +9% 調價")');
  const blockedBtn = hardConstraintRow.getByText("建立草稿");
  await expect(blockedBtn).toHaveAttribute("aria-disabled", "true");

  // rec-9001 (PASS) should be enabled
  const passRow = recTable.locator('tr:has-text("晚餐套餐 +3% 加權調價")');
  const enabledBtn = passRow.getByTestId("growth-draft-rec-9001");
  await expect(enabledBtn).not.toHaveAttribute("aria-disabled", "true");
});

// ---------------------------------------------------------------------------
// 4. Ineffective action (growth-7002) — closeout gate blocks direct close
// ---------------------------------------------------------------------------

test("Ineffective Growth Action is blocked from direct closeout", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7002");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("宵夜外送費試點活動");
  await expect(detailPanel).toContainText("無效"); // INEFFECTIVE outcome label

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  const gate = closeoutPanel.getByTestId("growth-closeout-gate");
  await expect(gate).toHaveAttribute("data-can-close", "false");

  const closeButton = closeoutPanel.getByTestId("growth-close-button");
  await expect(closeButton).toBeDisabled();

  const requiredAction = closeoutPanel.getByTestId("growth-required-action");
  await expect(requiredAction).toContainText("需先：執行 Rollback");
});

// ---------------------------------------------------------------------------
// 5. Effective action (growth-7001) can be closed — console audit emitted
// ---------------------------------------------------------------------------

test("Effective Growth Action can be closed and emits console audit log", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7001");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("都會晚餐套餐調價活動");
  await expect(detailPanel).toContainText("有效"); // EFFECTIVE outcome label

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  const gate = closeoutPanel.getByTestId("growth-closeout-gate");
  await expect(gate).toHaveAttribute("data-can-close", "true");

  const closeButton = closeoutPanel.getByTestId("growth-close-button");
  await expect(closeButton).toBeEnabled();

  // Capture console audit
  const consoleLogs: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().includes("[Console Audit]")) {
      consoleLogs.push(msg.text());
    }
  });

  await closeButton.click();

  // Success banner shown
  await expect(closeoutPanel.getByTestId("growth-closeout-success")).toBeVisible();

  // Console audit emitted once
  expect(consoleLogs.length).toBe(1);
  const parsed = JSON.parse(consoleLogs[0].replace("[Console Audit] ", ""));
  expect(parsed.action).toBe("APPROVE_CLOSEOUT");
  expect(parsed.itemId).toBe("growth-7001");
  expect(parsed.decisionId).toBe("dec-growth-7001");
});

// ---------------------------------------------------------------------------
// 6. Draft Builder modal: fill all five-step fields → submit → audit log
// ---------------------------------------------------------------------------

test("Growth draft modal renders, updates state, and outputs console audit log on submit", async ({
  page,
}) => {
  await page.goto("/operator?ws=growth");

  // Open draft modal for rec-9001 (PASS constraint)
  await page.getByTestId("growth-draft-rec-9001").click();

  const modal = page.getByTestId("growth-draft-modal");
  await expect(modal).toBeVisible();
  await expect(modal).toContainText("rec-9001");

  // Fill draft builder fields
  await modal.locator('input[name="name"]').fill("都會晚餐套餐調價活動改進版");
  await modal.locator('input[name="targetLift"]').fill("2.5");
  await modal.locator('select[name="observationWindow"]').selectOption("28");
  await modal
    .locator('textarea[name="rationale"]')
    .fill("以 PriceOps 建議為基礎，搭配晚餐時段滿額贈活動。");

  // Capture console audit
  const consoleLogs: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().includes("[Console Audit]")) {
      consoleLogs.push(msg.text());
    }
  });

  await modal.getByTestId("growth-draft-submit").click();

  // Modal should close after submit
  await expect(modal).not.toBeVisible();

  // Console audit emitted once
  expect(consoleLogs.length).toBe(1);
  const parsed = JSON.parse(consoleLogs[0].replace("[Console Audit] ", ""));
  expect(parsed.action).toBe("CREATE_DRAFT");
  expect(parsed.recommendationId).toBe("rec-9001");
  expect(parsed.name).toBe("都會晚餐套餐調價活動改進版");
  expect(parsed.targetLift).toBe(2.5);
  expect(parsed.observationWindow).toBe("28 天");
  expect(parsed.rationale).toBe("以 PriceOps 建議為基礎，搭配晚餐時段滿額贈活動。");
});

// ---------------------------------------------------------------------------
// 7. Draft modal close button works
// ---------------------------------------------------------------------------

test("Growth draft modal can be dismissed without submitting", async ({ page }) => {
  await page.goto("/operator?ws=growth");

  await page.getByTestId("growth-draft-rec-9001").click();
  const modal = page.getByTestId("growth-draft-modal");
  await expect(modal).toBeVisible();

  await modal.getByTestId("growth-draft-close").click();
  await expect(modal).not.toBeVisible();
});

// ---------------------------------------------------------------------------
// 8. Observing action shows PENDING — close button disabled
// ---------------------------------------------------------------------------

test("Observing Growth Action shows PENDING outcome and close is disabled", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7004");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("都會晚餐加點推薦活動");
  await expect(detailPanel).toContainText("觀察中"); // PENDING outcome label

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
});

// ---------------------------------------------------------------------------
// 9. Inconclusive action (growth-7003) — blocked, shows STRENGTHEN_EVIDENCE
// ---------------------------------------------------------------------------

test("Inconclusive Growth Action is blocked and shows required action", async ({ page }) => {
  await page.goto("/operator?ws=growth&item=growth-7003");

  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("郊區午餐加價包觀察活動");
  await expect(detailPanel).toContainText("待判定"); // INCONCLUSIVE label

  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(closeoutPanel.getByTestId("growth-closeout-gate")).toHaveAttribute(
    "data-can-close",
    "false",
  );
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
  await expect(closeoutPanel.getByTestId("growth-required-action")).toContainText(
    "需先：補強證據",
  );
});
