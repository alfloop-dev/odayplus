import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

/**
 * Assisted Listing Intake product gates (ODP-INTAKE-UX-QA-001).
 *
 * Every canonical flow uses the mounted Operator Console and the real FastAPI
 * service. The only fault control is the CI-only score queue failure header;
 * it still runs the real promotion saga, repositories, compensation branch,
 * durable receipts, and retry endpoint. No Playwright route is stubbed.
 */

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

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

test.beforeEach(async () => {
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
) {
  await page.evaluate(
    ({ roleId, subjectId }) => {
      window.sessionStorage.setItem("oday.operator.role", roleId);
      window.localStorage.setItem("oday.operator.role", roleId);
      window.sessionStorage.setItem("oday.operator.subject", subjectId);
    },
    { roleId: role, subjectId: subject },
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
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible({ timeout: 15_000 });
}

async function submitUrl(page: Page, url: string) {
  await page.getByTestId("intake-add-button").click();
  await expect(page.getByTestId("intake-add-dialog")).toBeVisible();
  await page.getByTestId("intake-url-input").fill(url);
  await page.getByTestId("intake-submit-button").click();
  await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
  return (await page.getByTestId("intake-detail-id").textContent())?.trim() ?? "";
}

async function decideCreate(page: Page) {
  await page.getByTestId("intake-decide-create").click();
  await page.getByTestId("intake-decide-reason").fill(
    "QA 已核對來源、地址、租金與比對證據，建立獨立物件。",
  );
  await page.getByTestId("intake-decide-risk-ack").check();
  await page.getByTestId("intake-decide-submit").click();
  await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
  await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
}

async function requestPromotion(page: Page) {
  await expect(page.getByTestId("promotion-request-form")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("promotion-request-reason").fill(
    "商圈缺口、租金與坪效門檻已核對，提出 Candidate Site 晉升申請。",
  );
  await page.getByTestId("promotion-request-ack").check();
  const [request, response] = await Promise.all([
    page.waitForRequest((candidate) => candidate.url().includes("/promotion-requests")),
    page.waitForResponse((candidate) => candidate.url().includes("/promotion-requests")),
    page.getByTestId("promotion-request-submit").click(),
  ]);
  const headers = await request.allHeaders();
  expect(headers["x-operator-role"]).toBe("expansion-manager");
  expect(headers["x-roles"]?.split(",").sort()).toEqual(
    ["expansion_user", "site_reviewer"].sort(),
  );
  expect(
    response.status(),
    `promotion request failed: ${await response.text()}`,
  ).toBe(202);
  await expect(page.getByTestId("promotion-receipt")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("promotion-receipt-status")).toContainText("PENDING_REVIEW");
  return {
    decisionId: (await page.getByTestId("promotion-decision-id").textContent())?.trim() ?? "",
    etag: (await page.getByTestId("promotion-version").textContent())?.trim() ?? "",
  };
}

async function reopenIntakeAs(
  page: Page,
  intakeId: string,
  subject: string,
  role = "expansion-manager",
) {
  await setOperatorSession(page, role, subject);
  await page.reload();
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible({ timeout: 15_000 });
  const detailDialog = page.getByTestId("intake-detail-dialog");
  const restoredFromUrl = await detailDialog
    .waitFor({ state: "visible", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  if (!restoredFromUrl) {
    await page.getByTestId(`intake-inbox-row-${intakeId}`).click();
  }
  await expect(detailDialog).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("intake-detail-id")).toHaveText(intakeId);
}

function parseEtag(text: string) {
  const match = text.match(/W\/"\d+"/);
  expect(match, `expected an ETag in ${text}`).not.toBeNull();
  return match![0];
}

function uniqueKey(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

test("canonical 1: exact URL duplicate is intercepted before retrieval", async ({ page }) => {
  await openRadar(page);
  const firstId = await submitUrl(page, URLS.clean);
  await page.getByRole("button", { name: "關閉" }).click();

  const secondId = await submitUrl(page, URLS.clean);
  expect(secondId).toBe(firstId);
  await page.getByRole("button", { name: "關閉" }).click();
  await expect(page.getByTestId(`intake-inbox-row-${firstId}`)).toHaveCount(1);
});

test("canonical 2: assisted-entry-only keeps URL and validates durable manual input", async ({
  page,
}) => {
  await openRadar(page);
  await submitUrl(page, URLS.assistedOnly);

  await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工補錄");
  await expect(page.getByTestId("intake-policy-chip")).toHaveText("僅人工補錄");
  await page.getByTestId("assisted-save").click();
  await expect(page.getByTestId("intake-assisted-error")).toContainText("地址、租金、坪數");

  await page.getByTestId("assisted-address").fill("新北市板橋區府中路 26 號 1F");
  await page.getByTestId("assisted-rent").fill("54000");
  await page.getByTestId("assisted-area").fill("22");
  await page.getByTestId("assisted-save").click();
  await expect(page.getByTestId("intake-assisted-error")).toContainText("了解人工補錄的風險");

  await page.getByTestId("assisted-risk-ack").check();
  await page.getByTestId("assisted-save").click();
  await expect(page.getByTestId("intake-fields-grid")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("intake-timeline")).toContainText("人工補錄");
});

test("canonical 3: possible match requires explicit reason and risk acknowledgement", async ({
  page,
}) => {
  await openRadar(page);
  await submitUrl(page, URLS.possible);

  await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工覆核");
  await expect(page.getByTestId("intake-detail-match")).toHaveText("疑似重複");
  await expect(page.getByTestId("intake-no-auto-note")).toContainText("不會自動合併");

  await page.getByTestId("intake-decide-create").click();
  await page.getByTestId("intake-decide-submit").click();
  await expect(page.getByTestId("intake-decide-error")).toContainText("必須填寫原因");
  await page.getByTestId("intake-decide-reason").fill(
    "現場確認樓層與來源物件 ID 均不同，判定為獨立物件。",
  );
  await page.getByTestId("intake-decide-submit").click();
  await expect(page.getByTestId("intake-decide-error")).toContainText("了解此決策的影響");
  await page.getByTestId("intake-decide-risk-ack").check();
  await page.getByTestId("intake-decide-submit").click();

  await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
  await expect(page.getByTestId("intake-timeline")).toContainText("現場確認樓層");
});

test("canonical 4: independent reviewer completes promotion with durable Candidate and SiteScore receipts", async ({
  page,
}) => {
  const proposer = "11111111-1111-4111-8111-111111111111";
  const reviewer = "22222222-2222-4222-8222-222222222222";
  await openRadar(page, "expansion-manager", proposer);
  const intakeId = await submitUrl(page, URLS.possible);
  await decideCreate(page);
  await requestPromotion(page);

  await expect(page.getByTestId("promotion-self-review-denied")).toContainText(
    "SELF_REVIEW_DENIED",
  );
  await expect(page.getByTestId("promotion-candidate-pending")).toBeVisible();
  await expect(page.getByTestId("promotion-score-job-pending")).toBeVisible();

  await reopenIntakeAs(page, intakeId, reviewer);
  await expect(page.getByTestId("promotion-second-actor-ok")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("promotion-review-reason").fill(
    "獨立覆核 gate snapshot、商圈需求與來源證據，核准建立 Candidate。",
  );
  await page.getByTestId("promotion-review-ack").check();
  await page.getByTestId("promotion-approve-btn").click();

  await expect(page.getByTestId("promotion-receipt-status")).toContainText("COMPLETED", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("promotion-candidate-id")).not.toHaveText("");
  await expect(page.getByTestId("promotion-score-job-id")).not.toHaveText("");
  await expect(page.getByTestId("sitescore-job-state")).toContainText("SUCCEEDED");
  await expect(page.getByTestId("promotion-receipt-reviewer")).toContainText(reviewer);
  await expect(page.getByTestId("promotion-audit-event-id")).not.toHaveText("");
  await expect(page.getByTestId("promotion-correlation-id")).not.toHaveText("");
});

test("canonical 5 and 6: SCORE_FAILED retains Candidate and same-key replay queues only once", async ({
  page,
}) => {
  const proposer = "33333333-3333-4333-8333-333333333333";
  const reviewer = "44444444-4444-4444-8444-444444444444";
  await openRadar(page, "expansion-manager", proposer);
  const intakeId = await submitUrl(page, URLS.possible);
  await decideCreate(page);
  const requested = await requestPromotion(page);

  const reviewerApi = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      ...MANAGER_HEADERS,
      "x-subject-id": reviewer,
    },
  });
  const failedReview = await reviewerApi.post(
    `/api/v1/promotion-decisions/${requested.decisionId}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "核准 Candidate，並以 CI fault 驗證評分失敗後的可恢復收據。",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": uniqueKey("qa-score-failure-review"),
        "If-Match": parseEtag(requested.etag),
        "X-ODP-Test-Fault": "score-failure",
      },
    },
  );
  expect(failedReview.status()).toBe(422);
  expect(await failedReview.text()).toContain("ODP_TEST_SCORE_FAILURE");

  await reopenIntakeAs(page, intakeId, reviewer);
  await expect(page.getByTestId("promotion-receipt-status")).toContainText("SCORE_FAILED", {
    timeout: 15_000,
  });
  const candidateId = (await page.getByTestId("promotion-candidate-id").textContent())?.trim() ?? "";
  const jobId = (await page.getByTestId("promotion-score-job-id").textContent())?.trim() ?? "";
  expect(candidateId).not.toBe("");
  expect(jobId).not.toBe("");
  await expect(page.getByTestId("candidate-retained-note")).toContainText(candidateId);
  await expect(page.getByTestId("sitescore-job-state")).toContainText("FAILED");

  const jobResponse = await reviewerApi.get(`/api/v1/jobs/${jobId}/receipt`);
  expect(jobResponse.status()).toBe(200);
  const failedJob = await jobResponse.json();
  expect(failedJob.status).toBe("FAILED");

  const replayKey = uniqueKey("qa-score-replay");
  const replayBody = {
    checkpoint: "SCORE_QUEUED",
    reason: "評分依賴已恢復，從 durable checkpoint 重新排入。",
    risk_acknowledged: true,
  };
  const replayHeaders = {
    "Idempotency-Key": replayKey,
    "If-Match": `W/"${failedJob.version}"`,
  };
  const firstReplay = await reviewerApi.post(`/api/v1/jobs/${jobId}/retry`, {
    data: replayBody,
    headers: replayHeaders,
  });
  expect(firstReplay.status()).toBe(202);
  expect(firstReplay.headers()["idempotency-replayed"]).toBe("false");
  const firstReceipt = await firstReplay.json();

  // Simulate a lost client response: repeat the identical command with the
  // same actor, key, body and original If-Match.
  const secondReplay = await reviewerApi.post(`/api/v1/jobs/${jobId}/retry`, {
    data: replayBody,
    headers: replayHeaders,
  });
  expect(secondReplay.status()).toBe(202);
  expect(secondReplay.headers()["idempotency-replayed"]).toBe("true");
  expect(await secondReplay.json()).toEqual(firstReceipt);
  expect(firstReceipt.attempt).toBe(failedJob.attempt + 1);

  const finalJob = await reviewerApi.get(`/api/v1/jobs/${jobId}/receipt`);
  const finalReceipt = await finalJob.json();
  expect(finalReceipt.attempt).toBe(firstReceipt.attempt);
  expect(finalReceipt.status).toBe("QUEUED");
  await reviewerApi.dispose();

  await reopenIntakeAs(page, intakeId, reviewer);
  await expect(page.getByTestId("candidate-retained-id")).toHaveText(candidateId);
  await expect(page.getByTestId("sitescore-job-state")).toContainText("QUEUED");
  await expect(page.getByTestId("sitescore-job-attempt")).toContainText(
    String(firstReceipt.attempt),
  );
});

test("source policy fails closed for blocked and unknown sources", async ({ page }) => {
  await openRadar(page);
  for (const [url, policy] of [
    [URLS.blocked, "來源封鎖"],
    [URLS.unknown, "政策未知"],
  ] as const) {
    await submitUrl(page, url);
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已隔離");
    await expect(page.getByTestId("intake-policy-chip")).toHaveText(policy);
    await expect(page.getByTestId("intake-decide-steward")).toBeVisible();
    await page.getByRole("button", { name: "關閉" }).click();
  }
});

test("retryable retrieval failure exposes code, correlation, recovery and preserved durable state", async ({
  page,
}) => {
  await openRadar(page);
  await submitUrl(page, URLS.timeout);
  await expect(page.getByTestId("intake-detail-stage")).toHaveText("處理失敗");
  await expect(page.getByTestId("intake-failure-panel")).toContainText(
    "ODP-INTAKE-RETRIEVAL-TIMEOUT",
  );
  await expect(page.getByTestId("intake-failure-panel")).toContainText("可重試");
  await expect(page.getByTestId("intake-correlation-id")).not.toHaveText("—");
  await expect(page.getByTestId("intake-retry-button")).toBeVisible();
});

test("governance reviewer gets masked read-only intake while unrelated roles fail closed", async ({
  page,
}) => {
  await openRadar(page, "expansion-manager", "qa-role-seed-manager");
  const intakeId = await submitUrl(page, URLS.possible);
  await page.getByRole("button", { name: "關閉" }).click();

  await setOperatorSession(page, "pm-audit", "qa-governance-reviewer");
  await page.reload();
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-read-only")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("intake-add-button")).toHaveCount(0);
  // The durable intake deep link can restore the detail after a role switch.
  // Only open the row when that restoration did not already happen.
  if (!(await page.getByTestId("intake-detail-dialog").isVisible())) {
    await page.getByTestId(`intake-inbox-row-${intakeId}`).click();
  }
  await expect(page.getByTestId("intake-detail-dialog")).toBeVisible();
  await expect(page.getByTestId("intake-decide-denied")).toBeVisible();
  await expect(page.getByTestId("intake-fix-address")).toBeDisabled();
  await expect(page.getByTestId("intake-masked-contactPhone").first()).toContainText(
    "FIELD_MASKED",
  );
  await page.getByRole("button", { name: "關閉" }).click();

  for (const [role, systemRole] of [
    ["ops-lead", "operations_manager"],
    ["cs-lead", "operations_manager"],
    ["field-lead", "regional_supervisor"],
    ["marketing-manager", "marketing_manager"],
  ] as const) {
    const denied = await page.request.get("/api/v1/operator/network-listings/intake", {
      headers: {
        "x-operator-role": role,
        "x-roles": systemRole,
        "x-subject-id": `qa-${role}`,
        "x-tenant-id": "tenant-a",
      },
    });
    expect(denied.status()).toBe(403);

    await setOperatorSession(page, role, `qa-${role}`);
    await page.reload();
    const networkWorkspace = page.getByRole("button", { name: /展店與店網/ });
    if (role === "ops-lead") {
      await expect(networkWorkspace).toHaveAttribute("aria-disabled", "false");
      await networkWorkspace.click();
      await page.getByTestId("network-tab-1").click();
      await expect(page.getByTestId("intake-no-access")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("intake-add-button")).toHaveCount(0);
    } else {
      await expect(networkWorkspace).toHaveAttribute("aria-disabled", "true");
    }
  }
});
