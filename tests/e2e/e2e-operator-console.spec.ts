import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const NETWORK_HEADERS = {
  "x-subject-id": "operator-expansion-manager",
  "x-roles": "expansion_user",
  "x-operator-role": "expansion-staff",
  "x-tenant-id": "tenant-a",
};

// No-op unless the running test actually took the operator backend lock, so it
// is safe for every test in this file and releases even on failure.
test.afterEach(() => {
  releaseOperatorBackendLock();
});

// These three cases explicitly exercise the anonymous design/legacy fixture
// path. Prove no authenticated account is present before replacing only its
// session-discovery premise; no business API payload or authority is supplied.
async function anonymousFixtureSession(page: import("@playwright/test").Page) {
  const session = await page.context().request.get("/auth/session");
  expect(session.status()).toBe(401);
  await page.route("**/auth/session", (route) => route.fulfill({
    status: 404, contentType: "application/json",
    body: JSON.stringify({ detail: "Anonymous design fixture; no authenticated session" }),
  }));
}

test("ODP-OC-PREVIEW-001 design-preview-only smoke mounts iframe prototype and Store Ops dialog", async ({
  page,
}) => {
  await anonymousFixtureSession(page);
  test.info().annotations.push({
    type: "scope",
    description:
      "design-preview-only; this is not API-backed Operator Console product proof",
  });

  const browserErrors: string[] = [];
  let resolveStoreOpsRequestStarted: (() => void) | undefined;
  const storeOpsRequestStarted = new Promise<void>((resolve) => {
    resolveStoreOpsRequestStarted = resolve;
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.route("**/api/v1/operator/store-ops/issues*", async (route) => {
    resolveStoreOpsRequestStarted?.();
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        stores: [],
        issues: [],
        evidence: [],
        auditEvents: [],
        fourLightSummary: [],
        count: 0,
      }),
    });
  });

  await page.addInitScript(() => {
    sessionStorage.removeItem("oday-plus-r3");
  });

  await page.goto("/operator");

  await expect(
    page.locator('[data-screen-label="Top Navigation"]'),
  ).toBeVisible();
  await expect(
    page.locator('[data-screen-label="Today 今日工作"]'),
  ).toBeVisible();
  await expect(page.getByText(/林承翰 — 營運主管/)).toBeVisible();
  await expect(page.getByText("今天最需要處理")).toBeVisible();

  await page.getByRole("button", { name: /門市營運/ }).click();
  await expect(
    page.locator('[data-screen-label="Store Ops 門市營運"]'),
  ).toBeVisible();
  await expect(
    page.locator('[data-screen-label="Store Ops 門市營運"]'),
  ).toContainText("ISS-1024");
  await storeOpsRequestStarted;
  await page
    .getByRole("button", { exact: true, name: "完成 Triage" })
    .last()
    .click();
  await expect(
    page.locator('[data-screen-label="Dialog Triage"]'),
  ).toBeVisible();
  await page.getByRole("button", { exact: true, name: "取消" }).click();

  await page.goto("/operator?ws=govern");
  await expect(page.getByTestId("governance-workspace")).toBeVisible();
  await expect(page.getByTestId("governance-workspace")).toContainText(
    "核准中心",
  );

  expect(
    browserErrors.filter((message) => !message.includes("404 (Not Found)")),
  ).toEqual([]);
});

test("ODP-FIN-FE-003 command palette and task center are API-bound", async ({

  page,
}) => {
  const taskRequests: string[] = [];
  await page.route("**/api/v1/operator/shell/tasks", async (route) => {
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
  await page
    .getByRole("combobox", { name: "Command palette search" })
    .fill("Live SiteScore");
  await expect(
    page.getByRole("option", { name: /Live SiteScore review/ }),
  ).toBeVisible();
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/ws=govern/);
  await expect(page).toHaveURL(/entity=API-902/);
});

test("ODP-OC-FE-05 Governance Workspace details and evidence package export", async ({
  page,
}) => {
  await anonymousFixtureSession(page);
  test.setTimeout(90_000);
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const snapshotResponse = page.waitForResponse((response) =>
    response.url().endsWith("/operator/governance/snapshot") && response.request().method() === "GET",
  );
  await page.goto("/operator?ws=govern");
  const confirmedSnapshot = await snapshotResponse;
  expect(confirmedSnapshot.ok()).toBe(true);
  expect((await confirmedSnapshot.json()).actionAuthority.verified).toBe(true);
  await expect(page.getByTestId("governance-workspace")).toBeVisible();

  await expect(page.getByTestId("governance-decision-authority")).toHaveAttribute("data-authority", "granted");
  await expect(page.getByTestId("governance-decision-authority")).toHaveAttribute("title", /operations_manager/);

  // Test 3: 退回/駁回 reason-gate enforced
  // Click on a pending approval, e.g. "Close escalated service issue"
  await page
    .getByRole("button", { name: "Close escalated service issue" })
    .click();
  // Fill less than 10 characters
  await page.locator("#governance-reason").fill("Too short");
  // Click Return
  await page.getByRole("button", { name: "退回修改", exact: true }).click();
  // Should show error message
  await expect(page.getByText("退回或駁回理由需至少 10 個字")).toBeVisible();

  // Now fill at least 10 characters
  const decisionReason =
    "Rejecting because candidate is too risky due to high competitor density";
  await page.locator("#governance-reason").fill(decisionReason);
  const decisionResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/operator/governance/decisions"),
  );
  // Click Return
  await page.getByRole("button", { name: "退回修改", exact: true }).click();
  const confirmedDecision = await decisionResponse;
  expect(confirmedDecision.ok()).toBe(true);
  const decisionReceipt = await confirmedDecision.json();
  expect(decisionReceipt.decision.actor).toBe("product-e2e-test");
  // Check that toast or success shows completed decision notice
  await expect(page.getByText(/已退回決策/)).toBeVisible();
  await expect(page.getByText(`決策理由：${decisionReason}`)).toBeVisible();

  // Go to Decision Log tab
  await page.getByRole("button", { name: "Decision Log" }).click();
  const decisionLog = page.getByRole("region", { name: "Decision Log" });
  await expect(decisionLog).toContainText("Returned");
  await expect(decisionLog).toContainText(decisionReason);
  await expect(decisionLog).toContainText(decisionReceipt.decision.actor);

  // Go to Audit Trail tab
  await page.getByRole("button", { name: "Audit Trail" }).click();
  await expect(page.getByRole("region", { name: "Audit Trail" })).toContainText(
    "決策退回",
  );

  // Test 1: Evidence Package produces mock file entry + audit event
  // Go to Evidence Package 匯出 tab
  await page.getByTestId("governance-tab-evidencePackage").click();
  await expect(
    page.getByRole("button", { name: "產生 Evidence Package", exact: true }),
  ).toBeVisible();

  const exportResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/operator/governance/evidence-package"),
  );
  // Click generate button
  await page
    .getByRole("button", { name: "產生 Evidence Package", exact: true })
    .click();
  const confirmedExport = await exportResponse;
  expect(confirmedExport.ok()).toBe(true);
  const exportReceipt = await confirmedExport.json();
  expect(exportReceipt.package.actor).toBe("product-e2e-test");
  // Wait for result
  const resultPanel = page.locator('[data-testid="evidence-package-result"]');
  await expect(resultPanel).toBeVisible({ timeout: 5000 });
  const fileName = await resultPanel.locator("span").first().textContent();
  expect(fileName).toContain("EVD-2026-0705-");

  // Go to Audit Trail tab and verify audit event has been written.
  // This explicit-fixture export proves metadata and actor propagation only;
  // production artifacts are verified separately from this demo receipt.
  await page.getByTestId("governance-tab-audit").click();
  const auditTrail = page.getByRole("region", { name: "Audit Trail" });
  await expect(auditTrail).toContainText("Export Evidence Package");
  await expect(auditTrail).toContainText(exportReceipt.package.actor);

  // Test 2: Status board renders DQ/Model/Connector/Runbook from fixtures
  // Go to 系統狀態盤 tab
  await page.getByTestId("governance-tab-statusBoard").click();
  await expect(
    page.locator('[aria-label="System status board"]'),
  ).toBeVisible();

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

  expect(
    browserErrors.filter((message) => !message.includes("404 (Not Found)")),
  ).toEqual([]);
});

test("ODP-OC-FE-04 Network tabs preserve fixture Candidate navigation and review", async ({
  page,
  request,
}) => {
  await anonymousFixtureSession(page);
  test.info().annotations.push({
    type: "scope",
    description:
      "Local fixture compatibility, existing-record navigation and remaining Network tabs; canonical PostgreSQL promotion approval is covered by the assisted-intake journey.",
  });
  // Only this test resets the shared operator listing backend, so the lock is
  // taken here rather than for the whole file. Released by the afterEach below,
  // which is a no-op for the tests that never took it.
  await acquireOperatorBackendLock();

  const resetRebalance = await page.request.post(
    `${API_BASE_URL}/api/v1/operator/network-rebalance/reset`,
    {
      headers: NETWORK_HEADERS,
    },
  );
  expect(resetRebalance.status()).toBe(200);
  const resetListings = await page.request.post(
    `${API_BASE_URL}/api/v1/operator/network-listings/reset`,
    {
      headers: NETWORK_HEADERS,
    },
  );
  expect(resetListings.status()).toBe(200);

  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  // ODP-OC-R4-007: reset the review service before the panel first fetches so
  // the golden GO review (RV-702) is pending and decidable in this smoke.
  const resetReviews = await request.post(`${API_BASE_URL}/api/v1/operator/network-reviews/reset`);
  expect(resetReviews.status()).toBe(200);

  // Use the canonical reviewer persona for this approving flow. Ops-lead
  // must not be silently promoted to a reviewer by the Network workspace.
  await page.addInitScript(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
  });
  await page.goto("/operator");
  // This fixture API journey starts after its shell envelope is applied.
  await expect(page.getByTestId("operator-envelope-source")).toHaveText(
    "operator-shell-api-envelope", { timeout: 15_000 },
  );
  // Arm the response observer immediately before the single Network click.
  const listingSnapshotResponse = page.waitForResponse(
    (response) => response.url().includes("/api/v1/operator/network-listings?") &&
      response.request().method() === "GET" &&
      response.request().headers()["x-operator-role"] === "expansion-manager",
    { timeout: 15_000 },
  );
  // Enter the Network (展店與店網) workspace.
  await page.getByRole("button", { name: /展店與店網/ }).click();
  const workspace = page.getByTestId("network-find-areas-workspace");
  await expect(workspace).toBeVisible({ timeout: 15_000 });

  // Default tab is Find Areas.
  await expect(page.getByTestId("network-panel-find-areas")).toBeVisible();

  // Require the API binding rather than racing the local fixture fallback.
  const listingSnapshot = await listingSnapshotResponse;
  expect(listingSnapshot.status(), await listingSnapshot.text()).toBe(200);

  // 物件雷達 / Listing Radar
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("network-panel-listings")).toBeVisible();
  await expect(page.getByTestId("network-listing-table")).toContainText(
    "L-2024",
  );
  await expect(page.getByTestId("network-listing-table")).toContainText(
    "L-2030",
  );
  // A reload must follow the committed tab URL, not its optimistic panel.
  await expect(page).toHaveURL(
    (url) => url.searchParams.get("ws") === "network" && url.searchParams.get("tab") === "radar",
    { timeout: 15_000 },
  );
  // Explicit fixture-only setup retains the legacy API compatibility check.
  // The following Radar click must only navigate to this existing candidate.
  const converted = await request.post(
    `${API_BASE_URL}/api/v1/operator/network-listings/listings/L-2024/convert`,
    {
      headers: {
        "x-subject-id": "operator-expansion-manager",
        "x-roles": "expansion_user,site_reviewer",
        "x-operator-role": "expansion-manager",
        "x-tenant-id": "tenant-a",
        "X-Correlation-Id": randomUUID(),
        "Idempotency-Key": `fixture-navigation-${randomUUID()}`,
      },
      data: {
        actorRoleId: "expansion-manager",
        reason: "Prepare the local synthetic Candidate navigation fixture.",
        riskAcknowledged: true,
        riskSummary: "Explicit local fixture setup for existing-record navigation.",
      },
    },
  );
  expect(converted.status(), await converted.text()).toBe(200);
  expect(await converted.json()).toMatchObject({
    listing: { id: "L-2024", status: "candidate", candidateId: "CS-1001" },
    candidate: { id: "CS-1001", listingId: "L-2024" },
    candidateCount: 1,
  });
  await page.reload();
  await expect(page.getByTestId("network-panel-listings")).toBeVisible();

  const navigationConversionPosts: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /\/network-listings\/listings\/[^/]+\/convert$/.test(new URL(request.url()).pathname)
    ) {
      navigationConversionPosts.push(request.url());
    }
  });
  await page.getByTestId("listing-navigate-L-2024").click();
  await expect(page).toHaveURL((url) => url.searchParams.get("candidate") === "CS-1001");

  // 候選點 / Candidates
  await expect(page.getByTestId("network-panel-candidates")).toBeVisible();
  await expect(page.getByTestId("network-candidate-table")).toContainText(
    "CS-1001",
  );
  await expect(page.getByTestId("candidate-row-CS-1001")).toHaveAttribute("data-active", "true");
  await page.reload();
  await expect(page).toHaveURL((url) => url.searchParams.get("candidate") === "CS-1001");
  await expect(page.getByTestId("candidate-row-CS-1001")).toHaveAttribute("data-active", "true");
  expect(navigationConversionPosts).toEqual([]);

  // SiteScore / Score Lab
  await page.getByTestId("network-tab-3").click();
  await expect(page.getByTestId("network-panel-sitescore")).toBeVisible();
  await expect(page.getByTestId("sitescore-card-CS-1001")).toContainText(
    "SiteScore v2.3",
  );

  // 比較 / Compare — ODP-OC-R4-006 productized the compare table to the
  // score-sorted SiteScore comparison with a primary/alternate/avoid rec.
  await page.getByTestId("network-tab-4").click();
  await expect(page.getByTestId("network-panel-compare")).toBeVisible();
  await expect(page.getByTestId("network-compare-table")).toContainText(
    "SiteScore",
  );

  // 選址審核 / Review — ODP-OC-R4-007 productized the review to the API-backed
  // decision dialog (Candidate/Review/Approval/Decision/Audit atomic sync).
  await page.getByTestId("network-tab-5").click();
  await expect(page.getByTestId("network-panel-review")).toBeVisible();
  await expect(page.getByTestId("review-card-RV-702")).toBeVisible({
    timeout: 15_000,
  });

  // Reason gate: submitting without a substantive reason is blocked.
  await page.getByTestId("review-card-RV-702").click();
  await page.getByTestId("review-btn-go-RV-702").click();
  await expect(page.getByTestId("review-decision-dialog")).toBeVisible();
  await page.getByTestId("review-decision-reason").fill("Short");
  await page.getByTestId("review-decision-submit").click();
  await expect(page.getByTestId("review-decision-error")).toContainText(
    "決策原因",
  );

  // A complete GO decision maps to Approved and closes the dialog.
  await page
    .getByTestId("review-decision-reason")
    .fill("Review approved based on excellent SiteScore and fit metrics.");
  const decisionResponse = page.waitForResponse(
    (response) => response.request().method() === "POST" &&
      new URL(response.url()).pathname.endsWith("/operator/network-reviews/RV-702/decide"),
    { timeout: 15_000 },
  );
  await page.getByTestId("review-decision-submit").click();
  const decision = await decisionResponse;
  expect(decision.status()).toBe(200);
  await expect(page.getByTestId("review-decision-dialog")).toHaveCount(0);
  await expect(page.getByTestId("review-decided-RV-702")).toContainText(
    "Approved",
  );

  // Candidates tab now surfaces the ODP-OC-R4-006 data-completeness Gate:
  // CS-1003 is gate-blocked ("缺資料 — 無法評分") so scoring is locked.
  await page.getByTestId("network-tab-2").click();
  await expect(page.getByTestId("network-panel-candidates")).toBeVisible();
  await expect(page.getByTestId("network-candidate-table")).toContainText(
    "CS-1001",
  );
  await expect(page.getByTestId("candidate-gate-block-CS-1003")).toContainText(
    "缺資料 — 無法評分",
  );

  // 低效重配 / Rebalance
  await page.getByTestId("network-tab-6").click();
  await expect(page.getByTestId("network-panel-rebalance")).toBeVisible();
  await expect(page.getByTestId("rebalance-card-RB-801")).toContainText(
    "新北板橋文化",
  );
  await expect(page.getByTestId("rebalance-primary-action")).toContainText(
    "建立 AVM 估值請求",
  );
  await expect(page.getByTestId("rebalance-boundary-RB-801")).toHaveAttribute(
    "data-relocation-executed",
    "false",
  );
  await expect(page.getByTestId("rebalance-boundary-RB-801")).toContainText("尚未執行搬遷");

  // Back to Find Areas remains functional.
  await page.getByTestId("network-tab-0").click();
  await expect(page.getByTestId("network-panel-find-areas")).toBeVisible();

  expect(
    browserErrors.filter((message) => !message.includes("404 (Not Found)")),
  ).toEqual([]);
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

  await page.getByRole("button", { name: /Store Ops|門市營運/ }).click();
  await expect(
    page.locator('[data-screen-label="Store Ops 門市營運"]'),
  ).toBeVisible();
  await page
    .getByRole("button", { exact: true, name: "完成 Triage" })
    .last()
    .click();
  const triageDialog = page.locator('[data-screen-label="Dialog Triage"]');
  await expect(triageDialog).toBeVisible();
  // Package 10 uses the same Chinese CTA in the rail and submit action;
  // scope to the dialog so this exercises the API-backed form, not its opener.
  await triageDialog
    .getByRole("button", { exact: true, name: "完成 Triage" })
    .click();
  await expect
    .poll(() =>
      operatorApiRequests.some(
        ({ method, pathname }) =>
          method === "POST" && operatorWorkflowWritePathPattern.test(pathname),
      ),
    )
    .toBe(true);

  const failures: string[] = [];
  const designFrameCount = await page
    .getByTestId("operator-design-frame")
    .count();
  const designArchiveFrameCount = await page
    .locator('iframe[src*="/operator-design/"]')
    .count();

  if (designFrameCount > 0 || designArchiveFrameCount > 0) {
    failures.push(
      "/operator still renders the design iframe (operator-design-frame or /operator-design/), so it is preview-only.",
    );
  }

  const hasReadProof = operatorApiRequests.some(
    ({ method, pathname }) =>
      method === "GET" && requiredOperatorReadPaths.has(pathname),
  );
  if (!hasReadProof) {
    failures.push(
      "No Operator Console read API proof observed; expected a GET to /api/v1/operator/bootstrap, /today, /issues, or /approvals.",
    );
  }

  const workflowWrites = operatorApiRequests.filter(
    ({ method, pathname }) =>
      method === "POST" && operatorWorkflowWritePathPattern.test(pathname),
  );
  if (workflowWrites.length === 0) {
    failures.push(
      "No API-backed workflow proof observed; expected a POST to an Operator Console workflow endpoint during the gate.",
    );
  }

  const workflowWriteMissingRequiredHeaders = workflowWrites.some(
    ({ headers }) =>
      !headers["idempotency-key"] || !headers["x-correlation-id"],
  );
  if (workflowWriteMissingRequiredHeaders) {
    failures.push(
      "Operator Console workflow writes must include Idempotency-Key and X-Correlation-Id headers.",
    );
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
  /^\/api\/v1\/operator\/(?:store-ops\/issues\/[^/]+\/(?:triage|assign|actions|field-report|outcome|escalate|camera-purpose)|approvals\/[^/]+\/decision|evidence\/[^/]+\/purpose)$/;
