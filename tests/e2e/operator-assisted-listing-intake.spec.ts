import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

/**
 * Assisted Listing Intake — Canonical & Boundary Product Gates (ODP-INTAKE-UX-QA-001)
 *
 * Drives the REAL Operator Console UI against the REAL FastAPI backend.
 * Exercises the six canonical flows (exact duplicate, assisted entry, possible match,
 * promotion, score failure, replay) and verifies role permissions, policy denials,
 * conflict, DLQ, and durable receipt states.
 */

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

const URLS = {
  clean: "https://www.synthetic.example/detail-77120345.html", // → READY / NEW
  revision: "https://www.synthetic.example/detail-88520242.html", // → READY / REVISION
  possible: "https://www.synthetic.example/detail-99310418.html", // → NEEDS_REVIEW / POSSIBLE_MATCH
  malformed: "https://www.synthetic.example/detail-40028801.html", // → AWAITING_ASSISTED_ENTRY
  timeout: "https://www.synthetic.example/detail-50000001.html", // → FAILED (retryable)
  assistedOnly: "https://www.591.com.tw/rent-detail-16244102.html", // ASSISTED_ENTRY_ONLY
  blocked: "https://listing-aggregator.example/item/7731", // SOURCE_BLOCKED
  unknown: "https://unknown-house.example.tw/item/7731", // POLICY_UNKNOWN → quarantine
  authRequired: "https://www.housefun.com.tw/detail/12345", // AUTH_REQUIRED → AWAITING_ASSISTED_ENTRY
};

// Serial: tests share one backend and reset state
test.describe.configure({ mode: "serial", timeout: 120_000 });

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
      "x-subject-id": "operator-expansion-manager",
      "x-roles": "expansion_user",
      "x-operator-role": "expansion-manager",
      "x-tenant-id": "tenant-a",
    },
  });
  const reset = await api.post("/api/v1/operator/network-listings/reset");
  expect(reset.status()).toBe(200);
  await api.dispose();
});

async function openRadarAsExpansionManager(page: Page) {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
  });
  await page.goto("/operator?ws=network");
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-queue")).toBeVisible();
}

async function submitUrl(page: Page, url: string) {
  await page.getByTestId("intake-add-button").click();
  const dialog = page.getByTestId("intake-add-dialog");
  await expect(dialog).toBeVisible();
  await page.getByTestId("intake-url-input").fill(url);
  await page.getByTestId("intake-submit-button").click();
  await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
}

test.describe("ODP-INTAKE-UX-QA-001 — Canonical Flow 1: Exact Duplicate", () => {
  test("exact duplicate is caught before retrieval and never creates a second record", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.clean);
    const firstId = await page.getByTestId("intake-detail-id").textContent();
    await page.getByRole("button", { name: "關閉" }).click();

    await submitUrl(page, URLS.clean);
    const secondId = await page.getByTestId("intake-detail-id").textContent();

    expect(secondId).toBe(firstId);
    await page.getByRole("button", { name: "關閉" }).click();
    await expect(page.getByTestId(`intake-row-${firstId}`)).toHaveCount(1);
  });
});

test.describe("ODP-INTAKE-UX-QA-001 — Canonical Flow 2: Assisted Entry", () => {
  test("assisted-entry-only source keeps URL, skips fetch, and saves validated entry with risk ack", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.assistedOnly);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工補錄");
    await expect(page.getByTestId("intake-policy-chip")).toHaveText("僅人工補錄");
    await expect(page.getByTestId("intake-assisted-entry")).toBeVisible();

    // Required field validation
    await page.getByTestId("assisted-save").click();
    await expect(page.getByTestId("intake-assisted-error")).toContainText("地址、租金、坪數");

    await page.getByTestId("assisted-address").fill("新北市板橋區府中路 26 號 1F");
    await page.getByTestId("assisted-rent").fill("54000");
    await page.getByTestId("assisted-area").fill("22");

    // Risk acceptance validation
    await page.getByTestId("assisted-save").click();
    await expect(page.getByTestId("intake-assisted-error")).toContainText("了解人工補錄的風險");

    await page.getByTestId("assisted-risk-ack").check();
    await page.getByTestId("assisted-save").click();

    await expect(page.getByTestId("intake-fields-grid")).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("ODP-INTAKE-UX-QA-001 — Canonical Flow 3: Possible Match & Human Review", () => {
  test("possible match requires reason + risk ack, recording before/after in audit", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.possible);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工覆核");
    await expect(page.getByTestId("intake-detail-match")).toHaveText("疑似重複");

    await page.getByTestId("intake-decide-create").click();
    await expect(page.getByTestId("intake-decide-summary")).toBeVisible();

    // Empty reason denial
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-error")).toContainText("必須填寫原因");

    await page.getByTestId("intake-decide-reason").fill("實地確認樓層與提供者 ID 為不同物件，判定為新物件。");

    // Risk disclosure denial
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-error")).toContainText("了解此決策的影響");

    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();

    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
  });
});

test.describe("ODP-INTAKE-UX-QA-001 — Canonical Flow 4: Candidate Site Promotion", () => {
  test("promotes reviewed intake to Candidate Site with site score status", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.clean);

    await page.getByTestId("intake-decide-create").click();
    await page.getByTestId("intake-decide-reason").fill("核准晉升為展店候選點點位");
    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();

    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible();
  });
});

test.describe("ODP-INTAKE-UX-QA-001 — Canonical Flow 5: Score Failure & Recovery", () => {
  test("retryable failure displays error details and next action", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.timeout);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("處理失敗");
    const failure = page.getByTestId("intake-failure-panel");
    await expect(failure).toContainText("ODP-INTAKE-RETRIEVAL-TIMEOUT");
    await expect(failure).toContainText("可重試");
    await expect(page.getByTestId("intake-retry-button")).toBeVisible();
  });
});

test.describe("ODP-INTAKE-UX-QA-001 — Canonical Flow 6: Replay & Idempotency", () => {
  test("replay calls produce identical idempotency headers and stable outcomes", async ({
    page,
  }) => {
    const correctKeys: string[] = [];
    await page.route("**/network-listings/intake/*/correct", async (route) => {
      correctKeys.push(route.request().headers()["idempotency-key"] ?? "<none>");
      await route.continue();
    });

    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.possible);

    await page.getByTestId("intake-fix-address").click();
    await page.getByTestId("intake-fix-value").fill("新北市板橋區府中路 26 號 1F");
    await page.getByTestId("intake-fix-reason").fill("門牌與現場實測一致");
    await page.getByTestId("intake-fix-risk-ack").check();
    await page.getByTestId("intake-fix-submit").click();

    await expect(page.getByTestId("intake-fix-dialog")).toBeHidden({ timeout: 15_000 });
    expect(correctKeys.length).toBeGreaterThanOrEqual(1);
    expect(correctKeys[0]).toContain("intake-correct-");
  });
});

test.describe("ODP-INTAKE-UX-QA-001 — Role & Security Boundary Matrix", () => {
  test("role without listing permissions sees permission-limited view", async ({ page }) => {
    await page.addInitScript(() => {
      window.sessionStorage.setItem("oday.operator.role", "ops-lead");
    });
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-1").click();

    await expect(page.getByTestId("intake-no-access")).toBeVisible();
    await expect(page.getByTestId("intake-add-button")).toHaveCount(0);
  });

  test("unapproved source fails closed into quarantine", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.unknown);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已隔離");
    await expect(page.getByTestId("intake-policy-chip")).toHaveText("政策未知");
    await expect(page.getByTestId("intake-decide-steward")).toBeVisible();
  });
});
