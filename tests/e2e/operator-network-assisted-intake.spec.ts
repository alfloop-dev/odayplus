import { expect, test } from "@playwright/test";

test("ODP-OC-R5-002 Network assisted-listing intake and decision flow", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  // 1. Visit Operator Console
  await page.goto("/operator");
  await expect(page.locator('[data-screen-label="Top Navigation"]')).toBeVisible();

  // 2. Go to Network Workspace
  await page.getByRole("button", { name: /展店與店網/ }).click();
  await expect(page.locator('[data-screen-label="Network 展店與店網"]')).toBeVisible();

  // 3. Switch to Listing Radar (物件雷達) Tab
  await page.getByTestId("network-tab-1").click();
  await expect(page.locator('[data-screen-label="Network 物件雷達"]')).toBeVisible();
  await expect(page.locator('[data-screen-label="Network URL 收件佇列"]')).toBeVisible();

  // 4. Trigger "從網址新增物件" Dialog
  await page.getByRole("button", { name: "＋ 從網址新增物件" }).click();
  await expect(page.locator('[data-screen-label="Dialog 從網址新增物件"]')).toBeVisible();

  // 5. Test validation error with invalid URL
  await page.locator('input[placeholder*="591"]').fill("invalid-url");
  await page.getByRole("button", { name: "送出新增" }).click();
  await expect(page.getByText("請確認網址格式")).toBeVisible();

  // 6. Submit valid URL
  await page.locator('input[placeholder*="591"]').fill("https://www.591.com.tw/rent-detail-12345.html");
  await page.getByRole("button", { name: "送出新增" }).click();
  await expect(page.locator('[data-screen-label="Dialog 從網址新增物件"]')).not.toBeVisible();

  // 7. Click on the first item in the queue (INK-001) to open details
  await page.getByText("INK-001").first().click();
  await expect(page.locator('[data-screen-label="Dialog 收件處理詳情"]')).toBeVisible();

  // 8. Open Field Correction Dialog
  await page.getByRole("button", { name: "修正" }).click();
  await expect(page.locator('[data-screen-label="Dialog 欄位修正"]')).toBeVisible();

  // 9. Input correction and go back
  await page.locator('input[style*="width: 100%"]').first().fill("台北市信義區松仁路 26 號");
  await page.locator('textarea[placeholder*="確認門牌"]').fill("與房東電話確認門牌為 26 號");
  await page.getByRole("button", { name: "儲存修正" }).click();
  await expect(page.locator('[data-screen-label="Dialog 欄位修正"]')).not.toBeVisible();
  await expect(page.locator('[data-screen-label="Dialog 收件處理詳情"]')).toBeVisible();

  // 10. Open Decision confirmation Dialog
  await page.getByRole("button", { name: "人工比對與決策" }).click();
  await expect(page.locator('[data-screen-label="Dialog 收件決策確認"]')).toBeVisible();

  // 11. Test decision validation error (short reason)
  await page.locator('textarea[placeholder*="決策理由"]').fill("Short");
  await page.getByRole("button", { name: "確認決策" }).click();
  await expect(page.getByText("決策確認必須填寫理由，且最少 10 個字")).toBeVisible();

  // 12. Complete decision with valid reason and override checkbox
  await page.locator('textarea[placeholder*="決策理由"]').fill("確認為同一地址刊登之重複件，已進行併入");
  await page.getByRole("button", { name: "確認決策" }).click();
  // Should show override error if checkbox is not checked
  await expect(page.getByText("需勾選風險確認以進行決策")).toBeVisible();

  // Check the risk checkbox
  await page.getByText("我了解本決策為覆寫系統建議").click();
  await page.getByRole("button", { name: "確認決策" }).click();

  // Verify dialog is closed and status is updated
  await expect(page.locator('[data-screen-label="Dialog 收件決策確認"]')).not.toBeVisible();
  await expect(page.locator('[data-screen-label="Dialog 收件處理詳情"]')).not.toBeVisible();

  expect(browserErrors).toEqual([]);
});
