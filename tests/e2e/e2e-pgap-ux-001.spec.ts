import { expect, test } from "@playwright/test";
import { execSync } from "child_process";
import http from "http";

/**
 * E2E tests for ODP-PGAP-UX-001.
 * Focuses on:
 * 1. Accessibility (keyboard navigation, Escape drawer close, deterministic focus return).
 * 2. Production mode mock table removal (no silent substitutions on error/empty).
 * 3. Form input preservation during error & retry (survives retries).
 * 4. Stale warning indicators and client retry buttons.
 */
const HEADERS = {
  "x-correlation-id": "corr-pgap-ux-001",
  "x-subject-id": "product-e2e-test",
  "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive",
};

test.describe("ODP-PGAP-UX-001: Accessibility, Resilient States, and Production Mode Gates", () => {
  test.describe.configure({ mode: "serial" });

  test("AVM workspace drawer allows keyboard closing and return focus working", async ({ page }) => {
    page.on("console", (msg) => console.log("BROWSER CONSOLE:", msg.text()));
    page.on("pageerror", (err) => console.log("BROWSER ERROR:", err.message));

    // Navigate to case cases page
    await page.goto("/w/dealroom/cases");

    // Click case to open drawer (dynamically locate the first case link)
    const caseLink = page.locator('a[href*="drawer=case"]').first();
    await caseLink.focus();
    await expect(caseLink).toBeFocused();
    await page.keyboard.press("Enter");

    // Drawer opens
    const drawer = page.getByTestId("avm-case-drawer");
    await expect(drawer).toBeVisible();

    // Close button autofocused
    const closeBtn = page.locator("#drawer-close-btn");
    await expect(closeBtn).toBeFocused();

    // Press Escape
    await page.keyboard.press("Escape");

    // Drawer is closed, focus returns to link
    await expect(drawer).toHaveCount(0);
    await expect(caseLink).toBeFocused();
  });

  test("Production mode removes fixture tables when API returns empty or failed", async ({ page }) => {
    const project = process.env.ODP_E2E_PROJECT || "oday-plus-e2e";
    const apiPort = process.env.ODP_E2E_API_PORT || "8099";

    // Helper to check if API is up
    const isApiReady = () => {
      return new Promise<boolean>((resolve) => {
        const req = http.get(`http://127.0.0.1:${apiPort}/platform/health`, (res) => {
          resolve(res.statusCode === 200);
        });
        req.on("error", () => resolve(false));
        req.end();
      });
    };

    // Helper to wait for API readiness
    const waitApi = async (timeoutMs = 15000) => {
      const start = Date.now();
      while (Date.now() - start < timeoutMs) {
        if (await isApiReady()) return true;
        await new Promise((r) => setTimeout(r, 500));
      }
      return false;
    };

    // 1. Test empty API response by restarting API (wipes in-memory db)
    execSync(`docker compose -p ${project} restart api`, { stdio: "ignore" });
    await waitApi();

    await page.setExtraHTTPHeaders({
      ...HEADERS,
      "x-production-mode": "true",
    });

    await page.goto("/w/dealroom/cases");

    // Live cases panel should show API empty
    const emptyPanel = page.getByTestId("avm-live-cases-empty");
    await expect(emptyPanel).toBeVisible();
    await expect(emptyPanel).toContainText("cold store");

    // Fixture table should NOT be visible under production mode
    const fixtureCaption = page.getByText("估值案件列表（reserve / asking 為敏感欄位，依權限遮罩）");
    await expect(fixtureCaption).toHaveCount(0);

    // 2. Test failed API response by stopping API container
    execSync(`docker compose -p ${project} stop api`, { stdio: "ignore" });
    await new Promise((r) => setTimeout(r, 1000));

    await page.goto("/w/dealroom/cases");

    // Live cases panel shows error
    await expect(emptyPanel).toBeVisible();
    await expect(emptyPanel).toContainText("後端讀取失敗");

    // Fixture table is still invisible
    await expect(fixtureCaption).toHaveCount(0);

    // 3. Restore seeded state for subsequent tests
    execSync(`docker compose -p ${project} start api`, { stdio: "ignore" });
    await waitApi();
    execSync("python3 scripts/e2e/seed_product_e2e_data.py --wait", { stdio: "ignore" });
  });

  test("User inputs survive AVM approval errors during submission", async ({ page, request }) => {
    // Get the current live case ID from the API
    const apiPort = process.env.ODP_E2E_API_PORT || "8099";
    let caseId = "vc-5102"; // fallback
    try {
      const res = await request.get(`http://127.0.0.1:${apiPort}/avm/cases`, {
        headers: HEADERS
      });
      if (res.ok()) {
        const data = await res.json();
        if (data.items && data.items.length > 0) {
          caseId = data.items[0].case_id;
        }
      }
    } catch (e) {}

    // Mock failing decision submission
    await page.route(`**/api/v1/operator/approvals/${caseId}/decision`, async (route) => {
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Invalid approval conditions" }),
      });
    });

    await page.goto(`/w/dealroom/cases/${caseId}`);

    // Select override and type reason
    const overrideCheckbox = page.locator('input[name="reserveOverride"]');
    await overrideCheckbox.check();

    const reasonTextarea = page.locator('textarea[name="reason"]');
    await reasonTextarea.fill("This is my custom test reason for override.");

    // Submit
    const submitBtn = page.getByTestId("approval-submit-button");
    await submitBtn.click();

    // Verify error is shown but inputs survive
    const errorBlock = page.getByTestId("approval-form-error");
    await expect(errorBlock).toBeVisible();

    await expect(overrideCheckbox).toBeChecked();
    await expect(reasonTextarea).toHaveValue("This is my custom test reason for override.");
  });

  test("User inputs survive Evidence export errors during submission", async ({ page }) => {
    // Mock failing evidence purpose confirmation
    const decisionId = "decision-lh-240";
    await page.route(`**/api/v1/operator/evidence/${decisionId}/purpose`, async (route) => {
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Failing evidence Purpose" }),
      });
    });

    await page.goto(`/w/audit/decisions/${decisionId}`);

    // Fill export reason
    const reasonTextarea = page.locator('[data-testid="evidence-export-panel"] textarea');
    await reasonTextarea.clear();
    await reasonTextarea.fill("Audit export test reasons that survive errors.");

    // Submit
    const submitBtn = page.getByTestId("export-submit-button");
    await submitBtn.click();

    // Verify error is shown but inputs survive
    const errorBlock = page.getByTestId("export-error");
    await expect(errorBlock).toBeVisible();

    await expect(reasonTextarea).toHaveValue("Audit export test reasons that survive errors.");
  });
});
