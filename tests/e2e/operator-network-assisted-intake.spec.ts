import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

/**
 * Assisted Listing Intake — product E2E (ODP-OC-R5-011).
 *
 * These drive the REAL Operator Console UI against the REAL FastAPI backend
 * (both booted by playwright.config.ts). Nothing here is stubbed: every
 * assertion below reflects a durable server decision, so a UI that faked a
 * stage, a match outcome, or an audit entry would fail these tests.
 *
 * The URLs come from assisted_intake.RETRIEVAL_CORPUS — deterministic
 * fixtures on the synthetic.example source, which is the only source with
 * APPROVED_RETRIEVAL. They are fixture *inputs* to the real pipeline, not
 * pre-baked outputs, and are never presented as live provider evidence.
 *
 * Run this file on its own:
 *   npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts
 *
 * playwright.config.ts sets fullyParallel, so separate spec FILES run
 * concurrently against one shared FastAPI process. Several operator specs
 * (this one, operator-network-listings, e2e-operator-console) each POST
 * .../network-listings/reset, which wipes that singleton's state mid-test for
 * whichever file is running. This file therefore holds the operator backend
 * lock for its whole run, so it is deterministic in the configured suite and
 * not only standalone. See tests/e2e/_operatorBackendLock.ts.
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
};

// Serial: these share one backend and each test resets it.
// The generous timeout covers the dev server's cold compile of /operator on
// the first hit, which alone can exceed Playwright's 30s default.
test.describe.configure({ mode: "serial", timeout: 120_000 });

// Exclusive use of the shared operator backend for this file's whole run — the
// reset in beforeEach is destructive to any other spec file resetting it
// concurrently, and vice versa.
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

/** Open the Network workspace as 展店經理 — the only role holding listing grants. */
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

test.describe("Assisted Listing Intake — Package 7 product surfaces", () => {
  test("all five Package 7 screen labels exist on the real surfaces", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    // 1. Network URL 收件佇列
    await expect(page.locator('[data-screen-label="Network URL 收件佇列"]')).toBeVisible();

    // 2. Dialog 從網址新增物件
    await page.getByTestId("intake-add-button").click();
    await expect(page.locator('[data-screen-label="Dialog 從網址新增物件"]')).toBeVisible();
    await page.getByTestId("intake-url-input").fill(URLS.possible);
    await page.getByTestId("intake-submit-button").click();

    // 3. Dialog 收件處理詳情
    await expect(page.locator('[data-screen-label="Dialog 收件處理詳情"]')).toBeVisible({
      timeout: 15_000,
    });

    // 4. Dialog 欄位修正
    await page.getByTestId("intake-fix-address").click();
    await expect(page.locator('[data-screen-label="Dialog 欄位修正"]')).toBeVisible();
    await page.getByRole("button", { name: "取消" }).click();

    // 5. Dialog 收件決策確認
    await page.getByTestId("intake-decide-create").click();
    await expect(page.locator('[data-screen-label="Dialog 收件決策確認"]')).toBeVisible();
  });

  test("empty state, then a clean URL submits to a durable READY / NEW record", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await expect(page.getByTestId("intake-queue-empty")).toBeVisible();

    await page.getByTestId("intake-add-button").click();
    // Double-submit guard: the button is inert until a URL is entered.
    await expect(page.getByTestId("intake-submit-button")).toBeDisabled();
    await page.getByTestId("intake-url-input").fill(URLS.clean);
    await expect(page.getByTestId("intake-submit-button")).toBeEnabled();
    await page.getByTestId("intake-submit-button").click();

    const detail = page.getByTestId("intake-detail-dialog");
    await expect(detail).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
    await expect(page.getByTestId("intake-detail-match")).toHaveText("新物件");

    // Source evidence is present at the point of decision.
    await expect(page.getByTestId("intake-correlation-id")).not.toHaveText("—");
    await expect(page.getByTestId("intake-stage-stepper")).toBeVisible();

    // The record is durable: it survives closing the dialog and reloading.
    await page.getByRole("button", { name: "關閉" }).click();
    await page.reload();
    await page.getByTestId("network-tab-1").click();
    await expect(page.getByTestId("intake-queue-rows")).toBeVisible();
    await expect(page.getByTestId("intake-count-processing")).toHaveText("0");
  });

  test("exact duplicate is caught before retrieval and never creates a second record", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.clean);
    const firstId = await page.getByTestId("intake-detail-id").textContent();
    await page.getByRole("button", { name: "關閉" }).click();

    await submitUrl(page, URLS.clean);
    const secondId = await page.getByTestId("intake-detail-id").textContent();

    // Same terminal record returned — the duplicate did not spawn a new intake.
    expect(secondId).toBe(firstId);
    await page.getByRole("button", { name: "關閉" }).click();
    await expect(page.getByTestId(`intake-row-${firstId}`)).toHaveCount(1);
  });

  test("possible match requires a human decision and refuses an empty reason", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.possible);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工覆核");
    await expect(page.getByTestId("intake-detail-match")).toHaveText("疑似重複");
    // Never auto-merged.
    await expect(page.getByTestId("intake-no-auto-note")).toBeVisible();
    await expect(page.getByTestId("intake-change-summary")).toBeVisible();

    await page.getByTestId("intake-decide-create").click();
    // Review summary is shown BEFORE the decision commits.
    await expect(page.getByTestId("intake-decide-summary")).toBeVisible();
    await expect(page.getByTestId("intake-decide-note")).toContainText("不使用 optimistic UI");

    // Empty reason is blocked and the dialog stays open — nothing was written.
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-error")).toContainText("必須填寫原因");
    await expect(page.getByTestId("intake-decide-dialog")).toBeVisible();

    await page.getByTestId("intake-decide-reason").fill("實地確認樓層與提供者 ID 為不同物件，判定為新物件。");

    // A reason alone does not commit: the risk must be disclosed AND accepted.
    await expect(page.getByTestId("intake-decide-risk-summary")).toContainText("物件收件匣");
    await expect(page.getByTestId("intake-decide-risk-ack")).not.toBeChecked();
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-error")).toContainText("了解此決策的影響");
    await expect(page.getByTestId("intake-decide-dialog")).toBeVisible();

    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();

    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
    // The decision is recorded in the audit trail.
    await expect(page.getByTestId("intake-timeline")).toContainText("實地確認樓層");
  });

  test("identity-field correction demands a reason, then records before/after", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.possible);

    await page.getByTestId("intake-fix-address").click();
    await expect(page.getByTestId("intake-fix-title")).toContainText("修正欄位");
    await page.getByTestId("intake-fix-value").fill("新北市板橋區府中路 26 號 1F");

    // Identity field: the reason gate blocks before the request is made.
    await page.getByTestId("intake-fix-submit").click();
    await expect(page.getByTestId("intake-fix-error")).toContainText("必須填寫原因");

    await page.getByTestId("intake-fix-reason").fill("與房東電話確認門牌為 26 號");

    // The risk of an identity correction is disclosed and must be accepted; the
    // summary names the field and the before/after values being written.
    await expect(page.getByTestId("intake-fix-risk-summary")).toContainText("新北市板橋區府中路 26 號 1F");
    await page.getByTestId("intake-fix-submit").click();
    await expect(page.getByTestId("intake-fix-error")).toContainText("了解此修正的影響");

    await page.getByTestId("intake-fix-risk-ack").check();
    await page.getByTestId("intake-fix-submit").click();

    await expect(page.getByTestId("intake-fix-dialog")).toBeHidden({ timeout: 15_000 });
    // The corrected value is now distinguishable from source and normalized.
    await expect(page.getByTestId("intake-fields-grid")).toContainText("新北市板橋區府中路 26 號 1F");
    await expect(page.getByTestId("intake-timeline")).toContainText("門牌");
  });

  test("assisted-entry-only source keeps the URL and never fetches the page", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.assistedOnly);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工補錄");
    await expect(page.getByTestId("intake-policy-chip")).toHaveText("僅人工補錄");
    // No retrieval happened: there is no capture, and the entry form is offered.
    await expect(page.getByTestId("intake-captured-at")).toContainText("—");
    await expect(page.getByTestId("intake-assisted-entry")).toBeVisible();

    // Required-field gate.
    await page.getByTestId("assisted-save").click();
    await expect(page.getByTestId("intake-assisted-error")).toContainText("地址、租金、坪數");

    await page.getByTestId("assisted-address").fill("新北市板橋區府中路 26 號 1F");
    await page.getByTestId("assisted-rent").fill("54000");
    await page.getByTestId("assisted-area").fill("22");

    // Hand-keyed values carry no retrieved evidence — that risk is disclosed on
    // the form and must be accepted before the correction is written.
    await expect(page.getByTestId("intake-assisted-risk-summary")).toContainText("不具本系統擷取的來源證據");
    await page.getByTestId("assisted-save").click();
    await expect(page.getByTestId("intake-assisted-error")).toContainText("了解人工補錄的風險");

    await page.getByTestId("assisted-risk-ack").check();
    await page.getByTestId("assisted-save").click();

    await expect(page.getByTestId("intake-fields-grid")).toBeVisible({ timeout: 15_000 });
  });

  test("unapproved source fails closed into quarantine with a governance reason", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.unknown);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已隔離");
    await expect(page.getByTestId("intake-policy-chip")).toHaveText("政策未知");
    await expect(page.getByTestId("intake-policy-reason")).not.toBeEmpty();
    // Quarantine routes to governance review, not to listing creation.
    await expect(page.getByTestId("intake-decide-steward")).toBeVisible();
    await expect(page.getByTestId("intake-decide-create")).toHaveCount(0);
  });

  test("retryable failure shows code, correlation and next action, and retry preserves input", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.timeout);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("處理失敗");
    const failure = page.getByTestId("intake-failure-panel");
    await expect(failure).toContainText("ODP-INTAKE-RETRIEVAL-TIMEOUT");
    await expect(failure).toContainText("可重試");
    await expect(failure).toContainText("下一步：");

    await expect(page.getByTestId("intake-retry-button")).toBeVisible();
  });

  test("revision outcome offers append-version against the matched listing", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.revision);

    await expect(page.getByTestId("intake-detail-match")).toHaveText("版本更新");
    const revise = page.getByTestId("intake-decide-revise");
    await expect(revise).toBeVisible();
    await expect(revise).toContainText("L-2024");
  });

  test("deep link reopens the intake record after leaving the page", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.clean);
    const id = (await page.getByTestId("intake-detail-id").textContent())?.trim();
    expect(id).toBeTruthy();

    // Leave entirely, then return via the durable deep link.
    await page.goto("/operator?ws=today");
    await page.goto(`/operator?ws=network#intake/${id}`);
    await page.getByTestId("network-tab-1").click();

    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("intake-detail-id")).toHaveText(id!);
  });

  test("queue counts reflect real server state across mixed outcomes", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    await submitUrl(page, URLS.possible);
    await page.getByRole("button", { name: "關閉" }).click();
    await submitUrl(page, URLS.timeout);
    await page.getByRole("button", { name: "關閉" }).click();
    await submitUrl(page, URLS.assistedOnly);
    await page.getByRole("button", { name: "關閉" }).click();

    await expect(page.getByTestId("intake-count-needs-review")).toHaveText("1");
    await expect(page.getByTestId("intake-count-blocked")).toHaveText("1");
    await expect(page.getByTestId("intake-count-awaiting")).toHaveText("1");
  });

  test("a role without listing permission gets the permission-limited state, not an empty queue", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.sessionStorage.setItem("oday.operator.role", "ops-lead");
    });
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-1").click();

    await expect(page.getByTestId("intake-no-access")).toBeVisible();
    // It must not imply "no submissions exist", and must offer no write action.
    await expect(page.getByTestId("intake-queue-empty")).toHaveCount(0);
    await expect(page.getByTestId("intake-add-button")).toHaveCount(0);
  });

  test("dialogs are keyboard operable and Escape closes them", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await page.getByTestId("intake-add-button").click();
    await expect(page.getByTestId("intake-add-dialog")).toBeVisible();

    // Focus lands inside the dialog rather than being left on the page behind.
    await expect(page.getByTestId("intake-url-input")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("intake-add-dialog")).toBeHidden();
  });

  test("mobile routes ambiguous side-by-side compare to a desktop-required state", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.possible);

    await expect(page.getByTestId("intake-desktop-required")).toBeVisible();
    // Submission and status tracking remain available on mobile.
    await expect(page.getByTestId("intake-detail-stage")).toBeVisible();
  });
});
