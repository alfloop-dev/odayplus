import { expect, test } from "@playwright/test";

test("Growth workspace renders segments, recommendations, and actions from fixtures", async ({ page }) => {
  await page.goto("/operator?ws=growth");
  
  // 1. App shell and page headers are visible
  await expect(page.getByTestId("app-shell")).toBeVisible();
  await expect(page.getByTestId("growth-workspace")).toBeVisible();
  await expect(page.getByTestId("growth-data-status")).toContainText("FRESH");

  // 2. Segments render correctly
  const segmentTable = page.getByTestId("growth-segment-table");
  await expect(segmentTable).toContainText("都會晚餐高潛力組");
  await expect(segmentTable).toContainText("郊區午餐守成組");

  // 3. PriceOps recommendations render correctly
  const recTable = page.getByTestId("growth-recommendation-table");
  await expect(recTable).toContainText("晚餐套餐 +3% 加權調價");
  
  // HARD_CONSTRAINT_FAILED should be blocked
  const blockedBtn = recTable.locator('tr:has-text("午餐主力商品 +9% 調價")').getByText("建立草稿");
  await expect(blockedBtn).toHaveAttribute("aria-disabled", "true");

  // 4. Growth Actions list renders correctly
  const itemTable = page.getByTestId("growth-item-table");
  await expect(itemTable).toContainText("都會晚餐套餐調價活動");
});

test("Growth workspace segment filtering works", async ({ page }) => {
  await page.goto("/operator?ws=growth");
  
  // Click on segment chip
  const filter = page.getByTestId("growth-segment-filter");
  await filter.getByText("都會晚餐高潛力組").click();

  // URL should update
  await expect(page).toHaveURL(/segment=seg-metro-dinner/);

  // Recommendations and Actions should be filtered
  const recTable = page.getByTestId("growth-recommendation-table");
  await expect(recTable).toContainText("晚餐套餐 +3% 加權調價");
  await expect(recTable).not.toContainText("宵夜外送費試點");
});

test("Growth Action details separate effectiveness and enforce closeout gate", async ({ page }) => {
  // 1. Ineffective campaign (growth-7002) is blocked from closeout
  await page.goto("/operator?ws=growth&item=growth-7002");
  
  const detailPanel = page.getByTestId("growth-item-detail");
  await expect(detailPanel).toContainText("宵夜外送費試點活動");
  await expect(detailPanel).toContainText("無效"); // Outcome label
  
  const closeoutPanel = page.getByTestId("growth-closeout-panel");
  await expect(closeoutPanel.getByTestId("growth-closeout-gate")).toHaveAttribute("data-can-close", "false");
  await expect(closeoutPanel.getByTestId("growth-close-button")).toBeDisabled();
  await expect(closeoutPanel.getByTestId("growth-required-action")).toContainText("需先：執行 Rollback");

  // 2. Effective campaign (growth-7001) can be closed
  await page.goto("/operator?ws=growth&item=growth-7001");
  await expect(detailPanel).toContainText("都會晚餐套餐調價活動");
  await expect(detailPanel).toContainText("有效");
  
  const closeButton = closeoutPanel.getByTestId("growth-close-button");
  await expect(closeButton).toBeEnabled();

  // Listen to console log for audit trail
  const consoleLogs: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().includes("[Console Audit]")) {
      consoleLogs.push(msg.text());
    }
  });

  await closeButton.click();
  await expect(closeoutPanel.getByTestId("growth-closeout-success")).toBeVisible();
  expect(consoleLogs.length).toBe(1);
  const parsedAudit = JSON.parse(consoleLogs[0].replace("[Console Audit] ", ""));
  expect(parsedAudit.action).toBe("APPROVE_CLOSEOUT");
  expect(parsedAudit.itemId).toBe("growth-7001");
  expect(parsedAudit.decisionId).toBe("dec-growth-7001");
});

test("Growth draft modal renders, updates state, and outputs console audit log on submit", async ({ page }) => {
  await page.goto("/operator?ws=growth");
  
  // Click draft button for metro dinner recommendation
  await page.getByTestId("growth-draft-rec-9001").click();

  const modal = page.getByTestId("growth-draft-modal");
  await expect(modal).toBeVisible();

  // Fill form fields
  await modal.locator('input[name="name"]').fill("都會晚餐套餐調價活動改進版");
  await modal.locator('input[name="targetLift"]').fill("2.5");
  await modal.locator('select[name="observationWindow"]').selectOption("28");
  await modal.locator('textarea[name="rationale"]').fill("以 PriceOps 建議為基礎，搭配晚餐時段滿額贈活動。");

  // Listen to console log for audit trail
  const consoleLogs: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().includes("[Console Audit]")) {
      consoleLogs.push(msg.text());
    }
  });

  await modal.getByTestId("growth-draft-submit").click();

  // Modal should close (which updates URL and removes draft id)
  await expect(modal).not.toBeVisible();
  
  // Verify audit log output
  expect(consoleLogs.length).toBe(1);
  const parsedAudit = JSON.parse(consoleLogs[0].replace("[Console Audit] ", ""));
  expect(parsedAudit.action).toBe("CREATE_DRAFT");
  expect(parsedAudit.recommendationId).toBe("rec-9001");
  expect(parsedAudit.name).toBe("都會晚餐套餐調價活動改進版");
  expect(parsedAudit.targetLift).toBe(2.5);
  expect(parsedAudit.observationWindow).toBe("28 天");
  expect(parsedAudit.rationale).toBe("以 PriceOps 建議為基礎，搭配晚餐時段滿額贈活動。");
});
