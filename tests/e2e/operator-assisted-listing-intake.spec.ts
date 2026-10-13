import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from "@playwright/test";

import { createPgNavigationControl, openPgRadar, openPgScenario, type PgScenario } from "./_intakeScorePg";

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

/**
 * Assisted Listing Intake product gates (ODP-INTAKE-UX-QA-001).
 *
 * Every canonical flow uses the mounted Operator Console and the real FastAPI
 * service. Canonical promotion cases use isolated PostgreSQL, explicit
 * synthetic admission/model inputs, and the actual worker. Source revocation
 * exercises the real admission failure gate. No Playwright route is stubbed.
 */

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const PG_CASE = /^canonical (4:|5 and 6:)/;
// Explicit isolated fixture input; never a production default or catalog entry.
const SYNTHETIC_TARGET_FORMAT = "synthetic-manual-format:Browser/v1";

const URLS = {
  clean: "https://www.synthetic.example/detail-77120345.html",
  possible: "https://www.synthetic.example/detail-99310418.html",
  timeout: "https://www.synthetic.example/detail-50000001.html",
  assistedOnly: "https://www.591.com.tw/rent-detail-16244102.html",
  blocked: "https://listing-aggregator.example/item/7731",
  unknown: "https://unknown-house.example.tw/item/7731",
};

const MANAGER_HEADERS = {
  "x-roles": "expansion_user,site_reviewer",
  "x-operator-role": "expansion-manager",
  "x-tenant-id": "tenant-a",
};

test.describe.configure({ mode: "serial", timeout: 120_000 });
// The repository-wide Playwright context carries a broad all-product role set.
// Clear it here so these security scenarios exercise the application's active
// Operator role headers instead of accidentally inheriting administrator-like
// access from the test runner.
test.use({ extraHTTPHeaders: {} });

test.beforeAll(async () => {
  await acquireOperatorBackendLock();
});

test.afterAll(() => {
  releaseOperatorBackendLock();
});

test.beforeEach(async ({}, info) => {
  // These positive cases have a separate physical PostgreSQL composition.
  // A missing fixture is a failure, never a skip or expected 503 substitute.
  if (PG_CASE.test(info.title)) return;
  const api = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      ...MANAGER_HEADERS,
      "x-subject-id": "qa-reset-manager",
    },
  });
  const reset = await api.post("/api/v1/operator/network-listings/reset");
  expect(reset.status()).toBe(200);
  await api.dispose();
});

async function setOperatorSession(
  page: Page,
  role = "expansion-manager",
  subject = "qa-expansion-manager",
  tenant = "tenant-a",
) {
  await page.evaluate(
    ({ roleId, subjectId, tenantId }) => {
      window.sessionStorage.setItem("oday.operator.role", roleId);
      window.localStorage.setItem("oday.operator.role", roleId);
      window.sessionStorage.setItem("oday.operator.subject", subjectId);
      window.sessionStorage.setItem("oday.operator.tenant", tenantId);
    },
    { roleId: role, subjectId: subject, tenantId: tenant },
  );
}

async function openRadar(
  page: Page,
  role = "expansion-manager",
  subject = "qa-expansion-manager",
) {
  await page.goto("/operator?ws=network");
  await setOperatorSession(page, role, subject);
  await page.reload();
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible({
    timeout: 15_000,
  });
}

async function submitUrl(page: Page, url: string) {
  await page.getByTestId("intake-add-button").click();
  await expect(page.getByTestId("intake-add-dialog")).toBeVisible();
  await page.getByTestId("intake-url-input").fill(url);
  await page.getByTestId("intake-submit-button").click();
  await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({
    timeout: 15_000,
  });
  return (
    (await page.getByTestId("intake-detail-id").textContent())?.trim() ?? ""
  );
}

async function requestPromotion(page: Page) {
  await expect(page.getByTestId("promotion-request-form")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("promotion-target-format")).toHaveValue("");
  await page.getByTestId("promotion-target-format").fill(SYNTHETIC_TARGET_FORMAT);
  await page
    .getByTestId("promotion-request-reason")
    .fill("商圈缺口、租金與坪效門檻已核對，提出 Candidate Site 晉升申請。");
  await page.getByTestId("promotion-request-ack").check();
  const [request, response] = await Promise.all([
    page.waitForRequest((candidate) =>
      candidate.url().includes("/promotion-requests"),
    ),
    page.waitForResponse((candidate) =>
      candidate.url().includes("/promotion-requests"),
    ),
    page.getByTestId("promotion-request-submit").click(),
  ]);
  const headers = await request.allHeaders();
  expect(request.postDataJSON().target_format_code).toBe(SYNTHETIC_TARGET_FORMAT);
  expect(headers["x-operator-role"]).toBe("expansion-manager");
  expect(headers["x-roles"]?.split(",").sort()).toEqual(
    ["expansion_user", "site_reviewer"].sort(),
  );
  expect(
    response.status(),
    `promotion request failed: ${await response.text()}`,
  ).toBe(202);
  expect((await response.json()).target_format_code).toBe(SYNTHETIC_TARGET_FORMAT);
  await expect(page.getByTestId("promotion-receipt")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("promotion-receipt-status")).toContainText(
    "PENDING_REVIEW",
  );
  await expect(page.getByTestId("promotion-receipt")).toContainText(SYNTHETIC_TARGET_FORMAT);
  return {
    decisionId:
      (await page.getByTestId("promotion-decision-id").textContent())?.trim() ??
      "",
    etag:
      (await page.getByTestId("promotion-version").textContent())?.trim() ?? "",
  };
}

async function reopenIntakeAs(
  page: Page,
  intakeId: string,
  subject: string,
  role = "expansion-manager",
  tenant = "tenant-a",
) {
  if (tenant !== "tenant-a") await page.setExtraHTTPHeaders({ ...MANAGER_HEADERS, "x-tenant-id": tenant, "x-subject-id": subject });
  await setOperatorSession(page, role, subject, tenant);
  await page.reload();
  // Package 10: while the durable URL context (selected + dialog) is active,
  // the intake detail owns the whole workspace surface and no Network tab
  // strip precedes it. Only when the URL carries no durable detail context may
  // the reviewer navigate through the Listing Radar tab and the inbox row.
  const detailDialog = page.getByTestId("intake-detail-dialog");
  // Mirror of isNetworkIntakeDetailRoute (NetworkFindAreasWorkspace).
  const url = new URL(page.url());
  const detailOwnsSurface =
    /^\/intake\/[^/]+$/.test(url.pathname) ||
    (Boolean(url.searchParams.get("selected")) &&
      ["detail", "fix", "decide", "assignmentSla"].includes(
        url.searchParams.get("dialog") ?? "",
      ));
  if (!detailOwnsSurface) {
    await page.getByTestId("network-tab-1").click();
    await expect(page.getByTestId("intake-inbox-view")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId(`intake-inbox-row-${intakeId}`).click();
  }
  await expect(detailDialog).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("intake-detail-id")).toHaveText(intakeId);
}

test("canonical 1: exact URL duplicate is intercepted before retrieval", async ({
  page,
}) => {
  await openRadar(page);
  const firstId = await submitUrl(page, URLS.clean);
  await page.getByTestId("intake-return-button").click();

  const secondId = await submitUrl(page, URLS.clean);
  expect(secondId).toBe(firstId);
  await page.getByTestId("intake-return-button").click();
  await expect(page.getByTestId(`intake-inbox-row-${firstId}`)).toHaveCount(1);
});

test("canonical 2: assisted-entry-only keeps URL and validates durable manual input", async ({
  page,
}) => {
  await openRadar(page);
  await submitUrl(page, URLS.assistedOnly);

  await expect(page.getByTestId("intake-detail-stage")).toHaveText(
    "待人工補錄",
  );
  await expect(page.getByTestId("intake-detail-dialog")).toContainText(
    "僅人工補錄",
  );
  await page.getByTestId("assisted-save").click();
  await expect(page.getByTestId("intake-assisted-error")).toContainText(
    "地址、租金、坪數",
  );

  await page
    .getByTestId("assisted-address")
    .fill("新北市板橋區府中路 26 號 1F");
  await page.getByTestId("assisted-rent").fill("54000");
  await page.getByTestId("assisted-area").fill("22");
  await page.getByTestId("assisted-save").click();
  await expect(page.getByTestId("intake-assisted-error")).toContainText(
    "了解人工補錄的風險",
  );

  await page.getByTestId("assisted-risk-ack").check();
  await page.getByTestId("assisted-save").click();
  await expect(page.getByTestId("intake-parsed-lineage")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("intake-timeline-audit-section")).toContainText(
    "人工補錄",
  );
});

test("canonical 3: possible match requires explicit reason and risk acknowledgement", async ({
  page,
}) => {
  await openRadar(page);
  await submitUrl(page, URLS.possible);

  await expect(page.getByTestId("intake-detail-stage")).toHaveText(
    "待人工覆核",
  );
  await expect(page.getByTestId("intake-detail-match")).toHaveText("疑似重複");
  await expect(page.getByTestId("no-auto-merge-warning")).toContainText(
    "絕不自動合併",
  );

  await page.getByTestId("decide-action-create").click();
  await page.getByTestId("intake-decide-submit").click();
  await expect(page.getByTestId("intake-decide-error")).toContainText(
    "必須填寫原因",
  );
  await page
    .getByTestId("intake-decide-reason")
    .fill("現場確認樓層與來源物件 ID 均不同，判定為獨立物件。");
  await page.getByTestId("intake-decide-submit").click();
  await expect(page.getByTestId("intake-decide-error")).toContainText(
    "了解此決策的影響",
  );
  await page.getByTestId("intake-decide-risk-ack").check();
  await page.getByTestId("intake-decide-submit").click();

  await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
  await expect(page.getByTestId("intake-timeline-audit-section")).toContainText(
    "現場確認樓層",
  );
});


type PgEvidence = PgScenario & {
  scope: string;
  read_model_runtime: { scope: string; infer_calls: number };
  promotion: { status: string; candidate_site_id: string; site_score_job_id: string } | null;
  job: { job_id: string; status: string; attempt: number; generation: number;
    result: { report_id: string; prediction_run_id: string; sitescore_run_id: string } | null } | null;
  delivery: { job_id: string; status: string } | null;
  candidates: { id: string; score: number | null; model_version: string | null }[];
  reports: { report_id: string; candidate_site_id: string; model_version: string; sitescore_run_id: string }[];
  inferences: { job_status: string; promotion_status: string; report_count_before_inference: number }[];
  event_types: string[];
  lineage: { prediction_run_exists: boolean; sitescore_run_exists: boolean; prediction_count: number };
};

async function pgEvidence(api: APIRequestContext, scenario: PgScenario, info: TestInfo, label: string) {
  const response = await api.get(`/_synthetic/evidence/${scenario.scenario_id}`);
  expect(response.ok(), await response.text()).toBe(true);
  const value = await response.json() as PgEvidence;
  expect(value.scope).toBe("ISOLATED_SYNTHETIC_PG_BROWSER_NOT_PRODUCTION_ADMISSION");
  expect(value.read_model_runtime).toEqual({ scope: "SYNTHETIC_READ_ONLY_NO_PRODUCTION_MODEL", infer_calls: 0 });
  await info.attach(label, { body: JSON.stringify(value, null, 2), contentType: "application/json" });
  return value;
}


async function approvePgPromotion(page: Page, scenario: PgScenario) {
  await reopenIntakeAs(page, scenario.intake_id, scenario.reviewer_id, "expansion-manager", scenario.tenant_id);
  await expect(page.getByTestId("promotion-second-actor-ok")).toBeVisible();
  await page.getByTestId("promotion-review-reason").fill("明示 synthetic PostgreSQL fixture：獨立覆核來源與量測資料後受理真實工作。");
  await page.getByTestId("promotion-review-ack").check();
  await page.getByTestId("promotion-approve-btn").click();
  await expect(page.getByTestId("promotion-confirmation-dialog")).toBeVisible();
  await expect(page.getByTestId("promotion-review-summary")).toContainText(scenario.reviewer_id);
  await expect(page.getByTestId("promotion-review-summary")).toContainText(SYNTHETIC_TARGET_FORMAT);
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/actions/review") && r.request().method() === "POST"),
    page.getByTestId("promotion-confirm-approve-btn").click(),
  ]);
  expect([200, 202], await response.text()).toContain(response.status());
  const receipt = await response.json();
  expect(receipt.status).toBe("SCORE_QUEUED");
  expect(receipt.target_format_code).toBe(SYNTHETIC_TARGET_FORMAT);
  await expect(page.getByTestId("promotion-receipt-status")).toContainText("SCORE_QUEUED");
  await expect(page.getByTestId("sitescore-job-state")).toContainText("QUEUED");
  return receipt;
}

async function drainPgWorker(api: APIRequestContext, scenario: PgScenario) {
  const response = await api.post("/_synthetic/worker/drain", { data: { scenario_id: scenario.scenario_id }, timeout: 45_000 });
  expect(response.ok(), await response.text()).toBe(true);
}

async function refreshPromotion(page: Page) {
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/promotion-decisions/") && r.request().method() === "GET"),
    page.getByTestId("promotion-refresh-btn").click(),
  ]);
  expect(response.status()).toBe(200);
}

function assertRealScoredResult(value: PgEvidence, candidateId: string, jobId: string) {
  expect(value.promotion?.status).toBe("COMPLETED");
  expect(value.job?.status).toBe("SUCCEEDED");
  expect(value.job?.job_id).toBe(jobId);
  expect(value.delivery).toMatchObject({ job_id: jobId, status: "succeeded" });
  expect(value.candidates).toHaveLength(1);
  expect(value.candidates[0].id).toBe(candidateId);
  expect(value.candidates[0].score).toBeNull();
  expect(value.reports).toHaveLength(1);
  expect(value.reports[0]).toMatchObject({ candidate_site_id: candidateId, model_version: "sitescore:synthetic-browser-v1" });
  expect(value.job?.result?.report_id).toBe(value.reports[0].report_id);
  expect(value.job?.result?.sitescore_run_id).toBe(value.reports[0].sitescore_run_id);
  expect(value.job?.result?.prediction_run_id).toBeTruthy();
  expect(value.lineage).toEqual({ prediction_run_exists: true, sitescore_run_exists: true, prediction_count: 1 });
  expect(value.inferences).toHaveLength(1);
  expect(value.inferences[0]).toMatchObject({ job_status: "RUNNING", promotion_status: "COMPLETED", report_count_before_inference: 0 });
  expect(value.event_types.filter((t) => t === "candidate.promotion_completed")).toHaveLength(1);
}

test("canonical 4: independent reviewer completes promotion with durable Candidate and SiteScore receipts", async ({ page }, info) => {
  const browserErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  const { api, scenario } = await openPgScenario(page, { entry: "radar" });
  try {
    await requestPromotion(page);
    await expect(page.getByTestId("promotion-self-review-denied")).toContainText("SELF_REVIEW_DENIED");
    await expect(page.getByTestId("promotion-candidate-pending")).toBeVisible();
    await expect(page.getByTestId("promotion-score-job-pending")).toBeVisible();
    const receipt = await approvePgPromotion(page, scenario);
    const queued = await pgEvidence(api, scenario, info, "actual-pg-queued");
    expect(queued.job).toMatchObject({ job_id: receipt.site_score_job_id, status: "QUEUED", attempt: 0 });
    expect(queued.candidates).toHaveLength(1);
    expect(queued.candidates[0].model_version).toBeNull();
    expect(queued.reports).toEqual([]);
    await drainPgWorker(api, scenario);
    const result = await pgEvidence(api, scenario, info, "actual-pg-worker-result");
    assertRealScoredResult(result, receipt.candidate_site_id, receipt.site_score_job_id);
    await refreshPromotion(page);
    await expect(page.getByTestId("promotion-receipt-status")).toContainText("COMPLETED");
    await expect(page.getByTestId("sitescore-job-state")).toContainText("SUCCEEDED");
    await expect(page.getByTestId("promotion-receipt-reviewer")).toContainText(scenario.reviewer_id);
    await expect(page.getByTestId("promotion-audit-event-id")).not.toHaveText("");
    await expect(page.getByTestId("promotion-correlation-id")).not.toHaveText("");
    await page.reload();
    await expect(page.getByTestId("sitescore-job-id")).toHaveText(receipt.site_score_job_id);
    await expect(page.getByTestId("sitescore-job-state")).toContainText("SUCCEEDED");
    for (const [width, height] of [[1440, 1000], [1024, 900], [390, 844]]) {
      await page.setViewportSize({ width, height });
      await page.getByTestId("sitescore-job-state").scrollIntoViewIfNeeded();
      await expect(page.getByTestId("sitescore-job-state")).toBeVisible();
      const screenshot = info.outputPath(`synthetic-pg-worker-success-${width}.png`);
      await page.screenshot({ path: screenshot });
      await info.attach(`synthetic-pg-worker-success-${width}`, { path: screenshot, contentType: "image/png" });
    }
    // The same actual listing now points to the independently approved candidate.
    // Reads must select that exact candidate, including after reload; no legacy
    // convert command or arbitrary first-card selection may replace this path.
    await page.setViewportSize({ width: 1440, height: 1000 });
    const control = await createPgNavigationControl(api);
    await info.attach("actual-pg-navigation-control", { body: JSON.stringify(control.evidence, null, 2), contentType: "application/json" });
    expect(control.candidateId).not.toBe(receipt.candidate_site_id);
    const navigationWrites: string[] = [];
    const observeNavigation = (request: import("@playwright/test").Request) => {
      if (request.method() !== "GET" && request.url().includes("/api/v1/")) navigationWrites.push(request.url());
    };
    page.on("request", observeNavigation);
    try {
      // Select both actual candidates. At least one is not the first row,
      // regardless of the API's ordering; neither has a fixed fixture ID.
      for (const [record, candidateId] of [[control.scenario, control.candidateId], [scenario, receipt.candidate_site_id]] as const) {
        await openPgRadar(page, record);
        const action = page.getByTestId(`listing-navigate-${record.listing_id}`);
        await expect(action).toBeEnabled({ timeout: 30_000 });
        await expect(action).toHaveText("前往候選點");
        await action.click();
        await expect(page.getByTestId("network-panel-candidates")).toBeVisible({ timeout: 30_000 });
        // The panel updates before Next finishes committing its URL transition.
        await expect(page).toHaveURL((url) => url.pathname === "/operator" && url.searchParams.get("candidate") === candidateId);
        expect(new URL(page.url()).pathname).toBe("/operator");
        expect(new URL(page.url()).searchParams.get("candidate")).toBe(candidateId);
        await expect(page.getByTestId(`candidate-row-${candidateId}`)).toHaveAttribute("data-active", "true");
        await expect(page.locator('[data-testid^="candidate-row-"][data-active="true"]')).toHaveCount(1);
        await page.reload();
        await expect(page).toHaveURL((url) => url.pathname === "/operator" && url.searchParams.get("candidate") === candidateId);
        await expect(page.getByTestId(`candidate-row-${candidateId}`)).toHaveAttribute("data-active", "true", { timeout: 30_000 });
        await expect(page.getByTestId("network-candidate-table").locator('[data-testid^="candidate-row-"]')).toHaveCount(2);
      }
      expect(navigationWrites).toEqual([]);
    } finally {
      page.off("request", observeNavigation);
    }
    for (const [width, height] of [[1440, 1000], [1024, 900], [390, 844]]) {
      await page.setViewportSize({ width, height });
      const candidate = page.getByTestId(`candidate-row-${receipt.candidate_site_id}`);
      await candidate.scrollIntoViewIfNeeded();
      await expect(candidate).toHaveAttribute("data-active", "true");
      const screenshot = info.outputPath(`synthetic-pg-radar-candidate-${width}.png`);
      await page.screenshot({ path: screenshot });
      await info.attach(`synthetic-pg-radar-candidate-${width}`, { path: screenshot, contentType: "image/png" });
    }
    const afterNavigation = await pgEvidence(api, scenario, info, "actual-pg-navigation-readonly");
    assertRealScoredResult(afterNavigation, receipt.candidate_site_id, receipt.site_score_job_id);
    await info.attach("synthetic-pg-browser-console", { body: JSON.stringify({
      scope: "Synthetic fixture with real PostgreSQL and worker; not production UAT", browserErrors, consoleErrors,
    }, null, 2), contentType: "application/json" });
    expect(browserErrors).toEqual([]);
    expect(consoleErrors.filter((text) => /TypeError|ReferenceError|Unhandled|shell route error/.test(text))).toEqual([]);
  } finally { await api.dispose(); }
});

test("canonical 5 and 6: SCORE_FAILED retains Candidate and same-key replay queues only once", async ({ page }, info) => {
  const { api, scenario } = await openPgScenario(page);
  try {
    await requestPromotion(page);
    const receipt = await approvePgPromotion(page, scenario);
    const policyUrl = `/_synthetic/scenarios/${scenario.scenario_id}/source-policy`;
    expect((await api.post(policyUrl, { data: { production_enabled: false } })).ok()).toBe(true);
    await drainPgWorker(api, scenario);
    const failed = await pgEvidence(api, scenario, info, "actual-pg-source-revoked-before-acceptance");
    expect(failed.promotion?.status).toBe("SCORE_FAILED");
    expect(failed.job?.status).toBe("FAILED");
    expect(failed.candidates).toHaveLength(1);
    expect(failed.candidates[0].id).toBe(receipt.candidate_site_id);
    expect(failed.reports).toEqual([]);
    expect(failed.inferences).toEqual([]);
    await refreshPromotion(page);
    await expect(page.getByTestId("candidate-retained-note")).toContainText(receipt.candidate_site_id);
    await expect(page.getByTestId("sitescore-job-state")).toContainText("FAILED");
    expect((await api.post(policyUrl, { data: { production_enabled: true } })).ok()).toBe(true);
    await page.getByTestId("sitescore-replay-reason").fill("Synthetic source registry approval fixture restored; authorized replay of the same real job.");
    await page.getByTestId("sitescore-replay-ack").check();
    const [sent, response] = await Promise.all([
      page.waitForRequest((r) => r.url().includes(`/jobs/${receipt.site_score_job_id}/retry`)),
      page.waitForResponse((r) => r.url().includes(`/jobs/${receipt.site_score_job_id}/retry`)),
      page.getByTestId("sitescore-replay-btn").click(),
    ]);
    expect(response.status(), await response.text()).toBe(202);
    expect(response.headers()["idempotency-replayed"]).toBe("false");
    const first = await response.json();
    const sentHeaders = await sent.allHeaders();
    const repeated = await api.post(`/api/v1/jobs/${receipt.site_score_job_id}/retry`, {
      data: sent.postDataJSON(), headers: { ...MANAGER_HEADERS, "x-tenant-id": scenario.tenant_id,
        "x-subject-id": scenario.reviewer_id, "X-Correlation-Id": sentHeaders["x-correlation-id"],
        "Idempotency-Key": sentHeaders["idempotency-key"], "If-Match": sentHeaders["if-match"] },
    });
    expect(repeated.status(), await repeated.text()).toBe(202);
    expect(repeated.headers()["idempotency-replayed"]).toBe("true");
    expect(await repeated.json()).toEqual(first);
    const requeued = await pgEvidence(api, scenario, info, "actual-pg-single-replay");
    expect(requeued.job?.status).toBe("QUEUED");
    expect(requeued.job?.attempt).toBe(failed.job?.attempt);
    expect(requeued.job?.generation).toBe((failed.job?.generation ?? 0) + 1);
    expect(requeued.candidates.map((c) => c.id)).toEqual([receipt.candidate_site_id]);
    await drainPgWorker(api, scenario);
    const result = await pgEvidence(api, scenario, info, "actual-pg-replayed-result");
    assertRealScoredResult(result, receipt.candidate_site_id, receipt.site_score_job_id);
    expect(result.job?.attempt).toBe((failed.job?.attempt ?? 0) + 1);
    await refreshPromotion(page);
    await expect(page.getByTestId("promotion-receipt-status")).toContainText("COMPLETED");
    await expect(page.getByTestId("promotion-candidate-id")).toHaveText(receipt.candidate_site_id);
    await expect(page.getByTestId("sitescore-job-state")).toContainText("SUCCEEDED");
  } finally { await api.dispose(); }
});

test("source policy fails closed for blocked and unknown sources", async ({
  page,
}) => {
  await openRadar(page);
  for (const [url, policy] of [
    [URLS.blocked, "來源封鎖"],
    [URLS.unknown, "政策未知"],
  ] as const) {
    await submitUrl(page, url);
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已隔離");
    await expect(page.getByTestId("intake-detail-dialog")).toContainText(
      policy,
    );
    await expect(page.getByTestId("decide-action-steward")).toBeVisible();
    await page.getByTestId("intake-return-button").click();
  }
});

test("retryable retrieval failure exposes code, correlation, recovery and preserved durable state", async ({
  page,
}) => {
  await openRadar(page);
  await submitUrl(page, URLS.timeout);
  await expect(page.getByTestId("intake-detail-stage")).toHaveText("處理失敗");
  // Canonical Package 10 recovery surface (IntakeErrorRecovery).
  const failure = page.getByTestId("intake-error-recovery");
  await expect(failure).toContainText("ODP-INTAKE-RETRIEVAL-TIMEOUT");
  await expect(page.getByTestId("error-retryable-badge")).toContainText(
    "可自動重試",
  );
  await expect(page.getByTestId("error-correlation-id")).not.toHaveText("—");
  await expect(page.getByTestId("error-correlation-id")).not.toHaveText(
    "UNAVAILABLE",
  );
  await expect(page.getByTestId("error-action-retry")).toBeVisible();
});

test("governance reviewer gets masked read-only intake while unrelated roles fail closed", async ({
  page,
}) => {
  await openRadar(page, "expansion-manager", "qa-role-seed-manager");
  const intakeId = await submitUrl(page, URLS.possible);
  await page.getByTestId("intake-return-button").click();
  // Returning to the inbox rewrites the durable URL asynchronously; reloading
  // before it settles restores the full-page detail view without the tab bar.
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible({
    timeout: 15_000,
  });

  await setOperatorSession(page, "pm-audit", "qa-governance-reviewer");
  await page.reload();
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-read-only")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("intake-add-button")).toHaveCount(0);
  // The durable intake deep link can restore the detail after a role switch.
  // Only open the row when that restoration did not already happen.
  if (!(await page.getByTestId("intake-detail-dialog").isVisible())) {
    await page.getByTestId(`intake-inbox-row-${intakeId}`).click();
  }
  await expect(page.getByTestId("intake-detail-dialog")).toBeVisible();
  await expect(page.getByTestId("intake-detail-actions")).toHaveCount(0);
  await expect(page.getByTestId("fix-field-address")).toHaveCount(0);
  // Canonical Package 10 lineage grid renders server-masked fields as
  // "•••• [MASKED]" inside lineage-row-<key>; the masking view checkbox is
  // off by default, so [MASKED] here proves the API masked contactPhone.
  await expect(
    page.getByTestId("lineage-row-contactPhone").first(),
  ).toContainText("[MASKED]");
  await page.getByTestId("intake-return-button").click();
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible({
    timeout: 15_000,
  });

  for (const [role, systemRole] of [
    ["ops-lead", "operations_manager"],
    ["cs-lead", "operations_manager"],
    ["field-lead", "regional_supervisor"],
    ["marketing-manager", "marketing_manager"],
  ] as const) {
    const denied = await page.request.get(
      "/api/v1/operator/network-listings/intake",
      {
        headers: {
          "x-operator-role": role,
          "x-roles": systemRole,
          "x-subject-id": `qa-${role}`,
          "x-tenant-id": "tenant-a",
        },
      },
    );
    expect(denied.status()).toBe(403);

    await setOperatorSession(page, role, `qa-${role}`);
    await page.reload();
    const networkWorkspace = page.getByRole("button", { name: /展店與店網/ });
    await expect(networkWorkspace).toHaveAttribute("aria-disabled", "true");
    await expect(page.getByTestId("intake-add-button")).toHaveCount(0);
  }
});
