import { expect, test, type Locator } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const NETWORK_HEADERS = {
  "x-subject-id": "operator-expansion-manager",
  "x-roles": "expansion_user",
  "x-operator-role": "expansion-manager",
  "x-tenant-id": "tenant-a",
};

test("ODP-OC-PREVIEW-001 design-preview-only smoke mounts iframe prototype and Store Ops dialog", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "scope",
    description: "design-preview-only; this is not API-backed Operator Console product proof",
  });

  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.addInitScript(() => {
    sessionStorage.removeItem("oday-plus-r3");
  });

  await page.goto("/operator");

  await expect(page.locator('[data-screen-label="Top Navigation"]')).toBeVisible();
  await expect(page.locator('[data-screen-label="Today 今日工作"]')).toBeVisible();
  await expect(page.getByText(/林承翰 — 營運主管/)).toBeVisible();
  await expect(page.getByText("今天最需要處理")).toBeVisible();

  await page.getByRole("button", { name: /門市營運/ }).click();
  await expect(page.locator('[data-screen-label="Store Ops 門市營運"]')).toBeVisible();
  await expect(page.locator('[data-screen-label="Store Ops 門市營運"]')).toContainText("ISS-1024");
  await page.getByRole("button", { exact: true, name: "完成 Triage" }).last().click();
  await expect(page.locator('[data-screen-label="Dialog Triage"]')).toBeVisible();
  await page.getByRole("button", { exact: true, name: "取消" }).click();

  await page.getByRole("button", { name: /治理稽核/ }).click();
  await expect(page.locator('[data-screen-label="Govern 治理稽核"]')).toBeVisible();
  await expect(page.locator('[data-screen-label="Govern 治理稽核"]')).toContainText("核准中心");

  expect(browserErrors).toEqual([]);
});

test("ODP-FIN-FE-003 command palette and task center are API-bound", async ({ page }) => {
  const taskRequests: string[] = [];
  await page.route("**/api/v1/tasks", async (route) => {
    taskRequests.push(route.request().url());
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: JSON.stringify({
        items: [
          {
            id: "API-902",
            title: "Live SiteScore review",
            summary: "Review CS-1002 before the expansion weekly gate.",
            owner: "展店",
            status: "review",
            priority: "P1",
            dueLabel: "42m",
            workspace: "govern",
          },
        ],
      }),
    });
  });

  await page.goto("/operator");

  await page.getByTestId("operator-task-center-button").click();
  const taskCenter = page.getByTestId("operator-task-center");
  await expect(taskCenter).toBeVisible();
  await expect(taskCenter).toContainText("API");
  await expect(taskCenter).toContainText("Live SiteScore review");
  expect(taskRequests.length).toBeGreaterThan(0);

  await page.keyboard.press("Control+K");
  const palette = page.getByTestId("operator-command-palette");
  await expect(palette).toBeVisible();
  await page.getByRole("combobox", { name: "Command palette search" }).fill("Live SiteScore");
  await expect(page.getByRole("option", { name: /Live SiteScore review/ })).toBeVisible();
  await page.keyboard.press("Enter");

  await expect(page.locator('[data-screen-label="Govern 治理稽核"]')).toBeVisible();
});

test("ODP-OC-FE-05 Governance Workspace details and evidence package export", async ({
  page,
}) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/operator");

  // Go to Govern workspace
  await page.getByRole("button", { name: /治理稽核/ }).click();
  await expect(page.locator('[data-screen-label="Govern 治理稽核"]')).toBeVisible();

  // Test 3: 退回/駁回 reason-gate enforced
  // Click on a pending approval, e.g. "Close escalated service issue"
  await page.getByRole("button", { name: "Close escalated service issue" }).click();
  // Fill less than 10 characters
  await page.locator("#governance-reason").fill("Too short");
  // Click Return
  await page.getByRole("button", { name: "Return", exact: true }).click();
  // Should show error message
  await expect(page.getByText("退回或駁回理由需至少 10 個字")).toBeVisible();

  // Now fill at least 10 characters
  const decisionReason = "Rejecting because candidate is too risky due to high competitor density";
  await page.locator("#governance-reason").fill(decisionReason);
  // Click Return
  await page.getByRole("button", { name: "Return", exact: true }).click();
  // Check that toast or success shows completed decision notice
  await expect(page.getByText("已完成決策 (returned)")).toBeVisible();
  await expect(page.getByText(`決策理由：${decisionReason}`)).toBeVisible();

  // Go to Decision Log tab
  await page.getByRole("button", { name: "Decision Log" }).click();
  await expect(page.locator("table")).toContainText("Returned");
  await expect(page.locator("table")).toContainText(decisionReason);

  // Go to Audit Trail tab
  await page.getByRole("button", { name: "Audit Trail" }).click();
  await expect(page.locator("table")).toContainText("決策退回");

  // Test 1: Evidence Package produces mock file entry + audit event
  // Go to Evidence Package 匯出 tab
  await page.getByRole("button", { name: "Evidence Package 匯出" }).click();
  await expect(page.getByRole("button", { name: "產生 Evidence Package", exact: true })).toBeVisible();
  
  // Click generate button
  await page.getByRole("button", { name: "產生 Evidence Package", exact: true }).click();
  // Wait for result
  const resultPanel = page.locator('[data-testid="evidence-package-result"]');
  await expect(resultPanel).toBeVisible({ timeout: 5000 });
  const fileName = await resultPanel.locator("span").first().textContent();
  expect(fileName).toContain("EVD-2026-0705-");

  // Go to Audit Trail tab and verify audit event has been written.
  // The Govern workspace is now API-bound (ODP-OC-R4-009): the export audit is
  // attributed to the acting role rather than the former hardcoded mock actor.
  await page.getByRole("button", { name: "Audit Trail" }).click();
  await expect(page.locator("table")).toContainText("Export Evidence Package");
  await expect(page.locator("table")).toContainText("營運主管");

  // Test 2: Status board renders DQ/Model/Connector/Runbook from fixtures
  // Go to 系統狀態盤 tab
  await page.getByRole("button", { name: "系統狀態盤" }).click();
  await expect(page.locator('[aria-label="System status board"]')).toBeVisible();
  
  // Verify Data Quality monitor
  await expect(page.getByText("Google Reviews Connector")).toBeVisible();
  await expect(page.getByText("Camera Events")).toBeVisible();
  // Verify Model Registry
  await expect(page.getByText("CS Intent")).toBeVisible();
  await expect(page.getByText("PriceOps")).toBeVisible();
  // Verify Connector/API
  await expect(page.getByText("Google Business Profile")).toBeVisible();
  await expect(page.getByText("LINE 官方帳號")).toBeVisible();
  // Verify Runbook status
  await expect(page.getByText("災備演練 (Disaster Recovery)")).toBeVisible();
  await expect(page.getByText("系統觀測性 (Observability)")).toBeVisible();

  expect(browserErrors).toEqual([]);
});


test("ODP-OC-FE-04 Network workspace exposes all six remaining tabs", async ({ page }) => {
  const resetRebalance = await page.request.post(`${API_BASE_URL}/api/v1/operator/network-rebalance/reset`, {
    headers: NETWORK_HEADERS,
  });
  expect(resetRebalance.status()).toBe(200);
  const resetListings = await page.request.post(`${API_BASE_URL}/api/v1/operator/network-listings/reset`, {
    headers: NETWORK_HEADERS,
  });
  expect(resetListings.status()).toBe(200);

  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/operator");

  // Enter the Network (展店與店網) workspace.
  await page.getByRole("button", { name: /展店與店網/ }).click();
  const workspace = page.getByTestId("network-find-areas-workspace");
  await expect(workspace).toBeVisible();

  // Default tab is Find Areas.
  await expect(page.getByTestId("network-panel-find-areas")).toBeVisible();

  // 物件雷達 / Listing Radar
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("network-panel-listings")).toBeVisible();
  await expect(page.getByTestId("network-listing-table")).toContainText("L-2024");
  await expect(page.getByTestId("network-listing-table")).toContainText("L-2030");
  await page.getByTestId("convert-L-2024").click();

  // 候選點 / Candidates
  await expect(page.getByTestId("network-panel-candidates")).toBeVisible();
  await expect(page.getByTestId("network-candidate-table")).toContainText("CS-1001");

  // SiteScore / Score Lab
  await page.getByTestId("network-tab-3").click();
  await expect(page.getByTestId("network-panel-sitescore")).toBeVisible();
  await expect(page.getByTestId("sitescore-card-CS-1001")).toContainText("SiteScore v2.3");

  // 比較 / Compare — ODP-OC-R4-006 productized the compare table to the
  // score-sorted SiteScore comparison with a primary/alternate/avoid rec.
  await page.getByTestId("network-tab-4").click();
  await expect(page.getByTestId("network-panel-compare")).toBeVisible();
  await expect(page.getByTestId("network-compare-table")).toContainText("SiteScore");

  // 審核 / Review
  await page.getByTestId("network-tab-5").click();
  await expect(page.getByTestId("network-panel-review")).toBeVisible();
  await expect(page.getByTestId("review-card-RV-1001")).toBeVisible();

  // Test reason gate validation error
  await page.getByTestId("review-reason-input-RV-1001").fill("Short");
  await page.getByTestId("review-btn-approve-RV-1001").click();
  await expect(page.getByTestId("review-error-RV-1001")).toContainText("決策理由需至少 10 個字");

  // Perform a valid decision
  const reviewReason = "Review approved based on excellent SiteScore and fit metrics.";
  await page.getByTestId("review-reason-input-RV-1001").fill(reviewReason);
  await page.getByTestId("review-btn-return-RV-1001").click(); // Return decision status

  // Verify UI changes on card
  await expect(page.getByTestId("review-card-RV-1001")).toContainText("退回");
  await expect(page.getByTestId("review-reason-RV-1001")).toContainText(reviewReason);
  await expect(page.getByTestId("review-card-RV-1001")).toContainText("Decided");

  // Candidates tab now surfaces the ODP-OC-R4-006 data-completeness Gate:
  // CS-1003 is gate-blocked ("缺資料 — 無法評分") so scoring is locked.
  await page.getByTestId("network-tab-2").click();
  await expect(page.getByTestId("network-panel-candidates")).toBeVisible();
  await expect(page.getByTestId("network-candidate-table")).toContainText("CS-1001");
  await expect(page.getByTestId("candidate-gate-block-CS-1003")).toContainText("缺資料 — 無法評分");

  // 低效重配 / Rebalance
  await page.getByTestId("network-tab-6").click();
  await expect(page.getByTestId("network-panel-rebalance")).toBeVisible();
  await expect(page.getByTestId("rebalance-card-RB-801")).toContainText("新北板橋文化");
  await expect(page.getByTestId("rebalance-primary-action")).toContainText("建立 AVM 估值請求");
  await expect(page.getByTestId("rebalance-boundary-RB-801")).toContainText("relocationExecuted=false");

  // Back to Find Areas remains functional.
  await page.getByTestId("network-tab-0").click();
  await expect(page.getByTestId("network-panel-find-areas")).toBeVisible();

  expect(browserErrors).toEqual([]);
});


test("ODP-OC-PROD-014 productization gate rejects iframe-only or non-API-backed /operator", async ({
  page,
}) => {
  test.skip(
    process.env.ODP_OPERATOR_PRODUCT_GATE !== "1",
    "Set ODP_OPERATOR_PRODUCT_GATE=1 to run the go/no-go Operator Console productization gate.",
  );

  const operatorApiRequests: OperatorApiRequest[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/v1/operator/")) return;

    operatorApiRequests.push({
      headers: request.headers(),
      method: request.method(),
      pathname: url.pathname,
    });
  });

  await page.goto("/operator");
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1_000);

  await tryClick(page.getByRole("button", { name: /Store Ops|門市營運/ }));
  await tryClick(page.getByRole("button", { name: /完成 Triage|Triage|triage/i }));
  await page.waitForTimeout(500);
  await tryClick(page.getByRole("button", { name: /Submit Triage/i }));
  await page.waitForTimeout(500);

  const failures: string[] = [];
  const designFrameCount = await page.getByTestId("operator-design-frame").count();
  const designArchiveFrameCount = await page.locator('iframe[src*="/operator-design/"]').count();

  if (designFrameCount > 0 || designArchiveFrameCount > 0) {
    failures.push(
      "/operator still renders the design iframe (operator-design-frame or /operator-design/), so it is preview-only.",
    );
  }

  const hasReadProof = operatorApiRequests.some(
    ({ method, pathname }) => method === "GET" && requiredOperatorReadPaths.has(pathname),
  );
  if (!hasReadProof) {
    failures.push(
      "No Operator Console read API proof observed; expected a GET to /api/v1/operator/bootstrap, /today, /issues, or /approvals.",
    );
  }

  const workflowWrites = operatorApiRequests.filter(
    ({ method, pathname }) => method === "POST" && operatorWorkflowWritePathPattern.test(pathname),
  );
  if (workflowWrites.length === 0) {
    failures.push(
      "No API-backed workflow proof observed; expected a POST to an Operator Console workflow endpoint during the gate.",
    );
  }

  const workflowWriteMissingRequiredHeaders = workflowWrites.some(
    ({ headers }) => !headers["idempotency-key"] || !headers["x-correlation-id"],
  );
  if (workflowWriteMissingRequiredHeaders) {
    failures.push("Operator Console workflow writes must include Idempotency-Key and X-Correlation-Id headers.");
  }

  expect(failures).toEqual([]);
});

type OperatorApiRequest = {
  headers: Record<string, string>;
  method: string;
  pathname: string;
};

const requiredOperatorReadPaths = new Set([
  "/api/v1/operator/bootstrap",
  "/api/v1/operator/today",
  "/api/v1/operator/issues",
  "/api/v1/operator/approvals",
]);

const operatorWorkflowWritePathPattern =
  /^\/api\/v1\/operator\/(?:issues\/[^/]+\/(?:triage|assign|actions|field-report|outcome|escalate)|approvals\/[^/]+\/decision|evidence\/[^/]+\/purpose)$/;

async function tryClick(locator: Locator) {
  if ((await locator.count()) === 0) return;
  await locator.first().click();
}
