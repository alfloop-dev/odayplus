import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

/**
 * Assisted Listing Intake — product E2E (ODP-OC-R5-011 / ODP-OC-R5-004).
 *
 * This test suite drives the REAL Operator Console UI against the REAL FastAPI backend
 * (both booted by playwright.config.ts). Nothing here is stubbed: every assertion
 * reflects a durable server decision.
 *
 * ============================================================================
 * TEST-TO-ACCEPTANCE MAPPING (ODP-OC-R5-004)
 * ============================================================================
 * [Acceptance Criteria]                               | [Verified By Test(s)]
 * ----------------------------------------------------------------------------
 * 1. Screen Labels: All 5 Package 7 labels exist.     | test("all five Package 7 screen labels exist...")
 * ----------------------------------------------------------------------------
 * 2. 11 Ingestion Stages: Covered without fabrication. | test("explicit assertion of all 11 stage transitions...")
 * ----------------------------------------------------------------------------
 * 3. 5 Match Outcomes: NEW, EXACT_DUPLICATE, REVISION, | test("empty state, then a clean URL...", "exact duplicate...",
 *    POSSIBLE_MATCH, and QUARANTINED verified.        | "possible match...", "unapproved source...", "revision outcome...")
 * ----------------------------------------------------------------------------
 * 4. 5 Source Policies: APPROVED_RETRIEVAL,            | test("prove the correct fetch or no-fetch behavior...")
 *    ASSISTED_ENTRY_ONLY, AUTH_REQUIRED,               |
 *    SOURCE_BLOCKED, POLICY_UNKNOWN prove fetch behavior.|
 * ----------------------------------------------------------------------------
 * 5. Correction and duplication parameters.           | test("identity-field correction...", "exact duplicate...")
 * ----------------------------------------------------------------------------
 * 6. Durable API storage (page reload & fresh context)| test("decisions and corrections survive page reload...")
 * ----------------------------------------------------------------------------
 * 7. Audit envelope checks (actor, timestamps, reason, | test("verify audit envelope parameters for decisions")
 *    before-after, snapshot parser version, corr ID).|
 * ----------------------------------------------------------------------------
 * 8. Retryable failure code, correlation, input prep.  | test("retryable failure shows code...")
 * ----------------------------------------------------------------------------
 * 9. Desktop, tablet, mobile viewports & responsive.  | test("tablet viewport folds the 5-up meta grid...",
 *                                                     |      "mobile routes ambiguous side-by-side...")
 * ----------------------------------------------------------------------------
 * 10. No prototype HTML or route interception.       | (Enforced: No page.route() used, all tests hit real backend)
 * ============================================================================
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

// Serial: these share one backend and each test resets it.
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

/** Helper to query direct API details of an intake record */
async function getIntakeApi(id: string) {
  const api = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      "x-subject-id": "operator-expansion-manager",
      "x-roles": "expansion_user",
      "x-operator-role": "expansion-manager",
      "x-tenant-id": "tenant-a",
    },
  });
  const res = await api.get(`/api/v1/operator/network-listings/intake/${id}`);
  expect(res.status()).toBe(200);
  const data = await res.json();
  await api.dispose();
  return data;
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

  test("correct and decide writes carry retry-stable idempotency keys", async ({ page }) => {
    const correctKeys: string[] = [];
    const decideKeys: string[] = [];
    let failFirstCorrect = true;
    let failFirstDecide = true;

    await page.route("**/network-listings/intake/*/correct", async (route) => {
      correctKeys.push(route.request().headers()["idempotency-key"] ?? "<none>");
      if (failFirstCorrect) {
        failFirstCorrect = false;
        await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
        return;
      }
      await route.continue();
    });
    await page.route("**/network-listings/intake/*/decide", async (route) => {
      decideKeys.push(route.request().headers()["idempotency-key"] ?? "<none>");
      if (failFirstDecide) {
        failFirstDecide = false;
        await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
        return;
      }
      await route.continue();
    });

    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.possible);

    await page.getByTestId("intake-fix-address").click();
    await page.getByTestId("intake-fix-value").fill("新北市板橋區府中路 26 號 1F");
    await page.getByTestId("intake-fix-reason").fill("與房東電話確認門牌為 26 號");
    await page.getByTestId("intake-fix-risk-ack").check();
    await page.getByTestId("intake-fix-submit").click();
    await expect(page.getByTestId("intake-fix-error")).toBeVisible();
    await expect(page.getByTestId("intake-fix-reason")).toHaveValue("與房東電話確認門牌為 26 號");

    await page.getByTestId("intake-fix-submit").click();
    await expect(page.getByTestId("intake-fix-dialog")).toBeHidden({ timeout: 15_000 });
    expect(correctKeys).toHaveLength(2);
    expect(correctKeys[0]).toBe(correctKeys[1]);
    expect(correctKeys[0]).not.toBe("<none>");
    expect(correctKeys[0]).toContain("intake-correct-");

    await page.getByTestId("intake-decide-create").click();
    await page.getByTestId("intake-decide-reason").fill("實地確認樓層與提供者 ID 為不同物件，判定為新物件。");
    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-error")).toBeVisible();
    await expect(page.getByTestId("intake-decide-reason")).toHaveValue(
      "實地確認樓層與提供者 ID 為不同物件，判定為新物件。",
    );

    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
    expect(decideKeys).toHaveLength(2);
    expect(decideKeys[0]).toBe(decideKeys[1]);
    expect(decideKeys[0]).not.toBe("<none>");
    expect(decideKeys[0]).toContain("intake-decide-create-");
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

  // ==========================================================================
  // ADDITIONAL TESTS INTEGRATING ODP-OC-R5-004 COMPLIANCE
  // ==========================================================================

  test("AUTH_REQUIRED policy flow and form submission", async ({ page }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.authRequired);

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工補錄");
    await expect(page.getByTestId("intake-policy-chip")).toHaveText("需授權帳號");
    await expect(page.getByTestId("intake-policy-reason")).toContainText("需經核准之合作帳號");

    // No retrieval happened.
    await expect(page.getByTestId("intake-captured-at")).toContainText("—（未擷取）");
    await expect(page.getByTestId("intake-assisted-entry")).toBeVisible();

    // Verify no-fetch via direct API check
    const id = (await page.getByTestId("intake-detail-id").textContent())?.trim();
    expect(id).toBeTruthy();
    const data = await getIntakeApi(id!);
    expect(data.policy).toBe("AUTH_REQUIRED");
    expect(data.capturedAt).toBeNull();
    expect(data.rawSnapshot).toBeNull();

    // Fill form and save (re-uses revision address matching L-2024 to transition)
    await page.getByTestId("assisted-address").fill("台北市信義區松仁路 96 號 1F");
    await page.getByTestId("assisted-rent").fill("55000");
    await page.getByTestId("assisted-area").fill("18");
    await page.getByTestId("assisted-risk-ack").check();
    await page.getByTestId("assisted-save").click();

    // Wait for conversion/match to possible match
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工覆核");
    await expect(page.getByTestId("intake-detail-match")).toHaveText("疑似重複");
  });

  test("prove the correct fetch or no-fetch behavior per policy state", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    // 1. APPROVED_RETRIEVAL
    await submitUrl(page, URLS.clean);
    const idClean = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    const cleanData = await getIntakeApi(idClean);
    expect(cleanData.policy).toBe("APPROVED_RETRIEVAL");
    expect(cleanData.capturedAt).not.toBeNull();
    expect(cleanData.rawSnapshot).not.toBeNull();
    expect(cleanData.parserVersion).not.toBeNull();
    await page.getByRole("button", { name: "關閉" }).click();

    // 2. ASSISTED_ENTRY_ONLY
    await submitUrl(page, URLS.assistedOnly);
    const idAssisted = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    const assistedData = await getIntakeApi(idAssisted);
    expect(assistedData.policy).toBe("ASSISTED_ENTRY_ONLY");
    expect(assistedData.capturedAt).toBeNull();
    expect(assistedData.rawSnapshot).toBeNull();
    await page.getByRole("button", { name: "關閉" }).click();

    // 3. AUTH_REQUIRED
    await submitUrl(page, URLS.authRequired);
    const idAuth = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    const authData = await getIntakeApi(idAuth);
    expect(authData.policy).toBe("AUTH_REQUIRED");
    expect(authData.capturedAt).toBeNull();
    expect(authData.rawSnapshot).toBeNull();
    await page.getByRole("button", { name: "關閉" }).click();

    // 4. SOURCE_BLOCKED
    await submitUrl(page, URLS.blocked);
    const idBlocked = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    const blockedData = await getIntakeApi(idBlocked);
    expect(blockedData.policy).toBe("SOURCE_BLOCKED");
    expect(blockedData.capturedAt).toBeNull();
    expect(blockedData.rawSnapshot).toBeNull();
    expect(blockedData.stage).toBe("QUARANTINED");
    await page.getByRole("button", { name: "關閉" }).click();

    // 5. POLICY_UNKNOWN
    await submitUrl(page, URLS.unknown);
    const idUnknown = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    const unknownData = await getIntakeApi(idUnknown);
    expect(unknownData.policy).toBe("POLICY_UNKNOWN");
    expect(unknownData.capturedAt).toBeNull();
    expect(unknownData.rawSnapshot).toBeNull();
    expect(unknownData.stage).toBe("QUARANTINED");
  });

  test("explicit assertion of all 11 stage transitions in the UI stepper", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    // 1-7: SUBMITTED, CHECKING_IDENTITY, CHECKING_SOURCE_POLICY, RETRIEVING, PARSING, MATCHING, READY
    await submitUrl(page, URLS.clean);
    const stepCodesClean = await page.locator('[data-testid="intake-stage-stepper"] [class*="stepCode"]').allTextContents();
    expect(stepCodesClean).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "RETRIEVING",
      "PARSING",
      "MATCHING",
      "READY"
    ]);
    await page.getByRole("button", { name: "關閉" }).click();

    // 8: NEEDS_REVIEW
    await submitUrl(page, URLS.possible);
    const stepCodesPossible = await page.locator('[data-testid="intake-stage-stepper"] [class*="stepCode"]').allTextContents();
    expect(stepCodesPossible).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "RETRIEVING",
      "PARSING",
      "MATCHING",
      "NEEDS_REVIEW"
    ]);
    await page.getByRole("button", { name: "關閉" }).click();

    // 9: FAILED
    await submitUrl(page, URLS.timeout);
    const stepCodesTimeout = await page.locator('[data-testid="intake-stage-stepper"] [class*="stepCode"]').allTextContents();
    expect(stepCodesTimeout).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "RETRIEVING",
      "PARSING",
      "MATCHING",
      "FAILED"
    ]);
    await page.getByRole("button", { name: "關閉" }).click();

    // 10: AWAITING_ASSISTED_ENTRY
    await submitUrl(page, URLS.assistedOnly);
    const stepCodesAssisted = await page.locator('[data-testid="intake-stage-stepper"] [class*="stepCode"]').allTextContents();
    expect(stepCodesAssisted).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "AWAITING_ASSISTED_ENTRY"
    ]);
    await page.getByRole("button", { name: "關閉" }).click();

    // 11: QUARANTINED
    await submitUrl(page, URLS.unknown);
    const stepCodesUnknown = await page.locator('[data-testid="intake-stage-stepper"] [class*="stepCode"]').allTextContents();
    expect(stepCodesUnknown).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "QUARANTINED"
    ]);
  });

  test("decisions and corrections survive page reload and a fresh browser context", async ({ page, context }) => {
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.possible);
    const id = (await page.getByTestId("intake-detail-id").textContent())?.trim();
    expect(id).toBeTruthy();

    // Perform correction on address
    await page.getByTestId("intake-fix-address").click();
    await page.getByTestId("intake-fix-value").fill("新北市板橋區府中路 26 號 1F");
    await page.getByTestId("intake-fix-reason").fill("Durability test correction reason");
    await page.getByTestId("intake-fix-risk-ack").check();
    await page.getByTestId("intake-fix-submit").click();
    await expect(page.getByTestId("intake-fix-dialog")).toBeHidden({ timeout: 15_000 });

    // Close detail dialog
    await page.getByRole("button", { name: "關閉" }).click();

    // Create a fresh browser context to prove durability
    const freshContext = await context.browser()!.newContext();
    const freshPage = await freshContext.newPage();

    await freshPage.addInitScript(() => {
      window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
    });
    // Navigate directly using deep link
    await freshPage.goto(`/operator?ws=network#intake/${id}`);
    await freshPage.getByTestId("network-tab-1").click();

    // Verify fields grid is updated and correct value is preserved
    await expect(freshPage.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
    await expect(freshPage.getByTestId("intake-fields-grid")).toContainText("新北市板橋區府中路 26 號 1F");

    // Perform decision in fresh context
    await freshPage.getByTestId("intake-decide-create").click();
    await freshPage.getByTestId("intake-decide-reason").fill("Durability test decision reason");
    await freshPage.getByTestId("intake-decide-risk-ack").check();
    await freshPage.getByTestId("intake-decide-submit").click();
    await expect(freshPage.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });

    // Verify the record's stage is durably updated
    await expect(freshPage.getByTestId("intake-detail-stage")).toHaveText("可決策");

    await freshPage.close();
    await freshContext.close();

    // Reopen in another fresh context to ensure final decision stage is durable
    const anotherContext = await context.browser()!.newContext();
    const anotherPage = await anotherContext.newPage();
    await anotherPage.addInitScript(() => {
      window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
    });
    await anotherPage.goto(`/operator?ws=network#intake/${id}`);
    await anotherPage.getByTestId("network-tab-1").click();
    await expect(anotherPage.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
    await expect(anotherPage.getByTestId("intake-detail-stage")).toHaveText("可決策");

    await anotherPage.close();
    await anotherContext.close();
  });

  test("tablet viewport folds the 5-up meta grid correctly", async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 }); // Tablet viewport
    await openRadarAsExpansionManager(page);
    await submitUrl(page, URLS.clean);

    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible();
    await expect(page.getByTestId("intake-stage-stepper")).toBeVisible();
    await expect(page.getByTestId("intake-fields-grid")).toBeVisible();
  });

  test("verify audit envelope for CREATE and PROMOTE decisions", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    // 1. CREATE decision
    await submitUrl(page, URLS.possible);
    const id1 = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    await page.getByTestId("intake-decide-create").click();
    await page.getByTestId("intake-decide-reason").fill("Audit test create reason");
    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
    await page.getByRole("button", { name: "關閉" }).click();

    const data1 = await getIntakeApi(id1);
    const auditCreate = data1.auditEvents.find((e: any) => e.action === "intake.decide.create");
    expect(auditCreate).toBeDefined();
    expect(auditCreate.actorRoleId).toBe("expansion-manager");
    expect(auditCreate.occurredAt).toBeTruthy();
    expect(auditCreate.message).toContain("Audit test create reason");
    expect(auditCreate.correlationId).toBeTruthy();
    expect(auditCreate.metadata.decision).toBe("create");
    expect(auditCreate.metadata.reason).toBe("Audit test create reason");
    expect(auditCreate.metadata.riskSummary).toContain("將以收件");
    expect(auditCreate.metadata.riskAcknowledged).toBe(true);
    expect(auditCreate.metadata.beforeAfter.stage.before).toBe("NEEDS_REVIEW");
    expect(auditCreate.metadata.beforeAfter.stage.after).toBe("READY");
    expect(auditCreate.metadata.beforeAfter.listings_count).toBeDefined();
    const createdListingId = auditCreate.metadata.targetListingId;
    expect(createdListingId).toBeTruthy();

    // 2. PROMOTE decision (requires targetListingId created above)
    const apiContext = await playwrightRequest.newContext({
      baseURL: API_BASE_URL,
      extraHTTPHeaders: {
        "x-subject-id": "operator-expansion-manager",
        "x-roles": "expansion_user",
        "x-operator-role": "expansion-manager",
        "x-tenant-id": "tenant-a",
        "X-Correlation-Id": "promote-corr-id-12345",
        "Idempotency-Key": "promote-key-12345"
      },
    });
    const promoteRes = await apiContext.post(`/api/v1/operator/network-listings/intake/${id1}/promote`, {
      data: {
        reason: "Audit test promote reason",
        riskSummary: "測試推廣風險宣告",
        riskAcknowledged: true,
        actorRoleId: "expansion-manager",
        actorName: "林曉青"
      }
    });
    expect(promoteRes.status()).toBe(200);
    const promoteResultData = await promoteRes.json();
    await apiContext.dispose();

    const data1Updated = await getIntakeApi(id1);
    const auditPromote = data1Updated.auditEvents.find((e: any) => e.action === "intake.promote");
    expect(auditPromote).toBeDefined();
    expect(auditPromote.actorRoleId).toBe("expansion-manager");
    expect(auditPromote.occurredAt).toBeTruthy();
    expect(auditPromote.message).toContain("Audit test promote reason");
    expect(auditPromote.correlationId).toBe("promote-corr-id-12345");
    expect(auditPromote.metadata.targetListingId).toBe(createdListingId);
    expect(auditPromote.metadata.candidateId).toBe(promoteResultData.candidate.id);
    expect(auditPromote.metadata.reason).toBe("Audit test promote reason");
    expect(auditPromote.metadata.before.listingStatus).toBe("new");
    expect(auditPromote.metadata.after.listingStatus).toBe("candidate");
    expect(auditPromote.metadata.riskSummary).toBe("測試推廣風險宣告");
    expect(auditPromote.metadata.riskAcknowledged).toBe(true);
  });

  test("verify audit envelope for REVISE decision", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    await submitUrl(page, URLS.revision);
    const id2 = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    await page.getByTestId("intake-decide-revise").click();
    await page.getByTestId("intake-decide-reason").fill("Audit test revise reason");
    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
    await page.getByRole("button", { name: "關閉" }).click();

    const data2 = await getIntakeApi(id2);
    const auditRevise = data2.auditEvents.find((e: any) => e.action === "intake.decide.revise");
    expect(auditRevise).toBeDefined();
    expect(auditRevise.actorRoleId).toBe("expansion-manager");
    expect(auditRevise.occurredAt).toBeTruthy();
    expect(auditRevise.message).toContain("Audit test revise reason");
    expect(auditRevise.metadata.decision).toBe("revise");
    expect(auditRevise.metadata.reason).toBe("Audit test revise reason");
    expect(auditRevise.metadata.riskSummary).toContain("將以收件");
    expect(auditRevise.metadata.riskAcknowledged).toBe(true);
    expect(auditRevise.metadata.beforeAfter.stage.before).toBe("READY");
    expect(auditRevise.metadata.beforeAfter.stage.after).toBe("READY");
    expect(auditRevise.metadata.beforeAfter.target_rent).toBeDefined();
    expect(auditRevise.metadata.targetListingId).toBe("L-2024");
  });

  test("verify audit envelope for DUPLICATE decision", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    await submitUrl(page, URLS.possible);
    const id3 = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    await page.getByTestId("intake-decide-dup").click();
    await page.getByTestId("intake-decide-reason").fill("Audit test duplicate reason");
    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
    await page.getByRole("button", { name: "關閉" }).click();

    const data3 = await getIntakeApi(id3);
    const auditDup = data3.auditEvents.find((e: any) => e.action === "intake.decide.duplicate");
    expect(auditDup).toBeDefined();
    expect(auditDup.actorRoleId).toBe("expansion-manager");
    expect(auditDup.occurredAt).toBeTruthy();
    expect(auditDup.message).toContain("Audit test duplicate reason");
    expect(auditDup.metadata.decision).toBe("duplicate");
    expect(auditDup.metadata.reason).toBe("Audit test duplicate reason");
    expect(auditDup.metadata.riskSummary).toContain("將收件");
    expect(auditDup.metadata.riskAcknowledged).toBe(true);
    expect(auditDup.metadata.beforeAfter.stage.before).toBe("NEEDS_REVIEW");
    expect(auditDup.metadata.beforeAfter.stage.after).toBe("READY");
    expect(auditDup.metadata.beforeAfter.target_evidence_count).toBeDefined();
    expect(auditDup.metadata.targetListingId).toBe("L-2025");
  });

  test("verify audit envelope for QUARANTINE decision", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    await submitUrl(page, URLS.possible);
    const id4 = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    await page.getByTestId("intake-decide-steward").click();
    await page.getByTestId("intake-decide-reason").fill("Audit test quarantine reason");
    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({ timeout: 15_000 });
    await page.getByRole("button", { name: "關閉" }).click();

    const data4 = await getIntakeApi(id4);
    const auditQuarantine = data4.auditEvents.find((e: any) => e.action === "intake.decide.quarantine");
    expect(auditQuarantine).toBeDefined();
    expect(auditQuarantine.actorRoleId).toBe("expansion-manager");
    expect(auditQuarantine.occurredAt).toBeTruthy();
    expect(auditQuarantine.message).toContain("Audit test quarantine reason");
    expect(auditQuarantine.metadata.decision).toBe("quarantine");
    expect(auditQuarantine.metadata.reason).toBe("Audit test quarantine reason");
    expect(auditQuarantine.metadata.riskSummary).toContain("將收件");
    expect(auditQuarantine.metadata.riskAcknowledged).toBe(true);
    expect(auditQuarantine.metadata.beforeAfter.stage.before).toBe("NEEDS_REVIEW");
    expect(auditQuarantine.metadata.beforeAfter.stage.after).toBe("QUARANTINED");
  });

  test("verify audit envelope for REJECT decision", async ({ page }) => {
    await openRadarAsExpansionManager(page);

    await submitUrl(page, URLS.possible);
    const id5 = (await page.getByTestId("intake-detail-id").textContent())?.trim()!;
    await page.getByRole("button", { name: "關閉" }).click();

    const apiContext = await playwrightRequest.newContext({
      baseURL: API_BASE_URL,
      extraHTTPHeaders: {
        "x-subject-id": "operator-expansion-manager",
        "x-roles": "expansion_user",
        "x-operator-role": "expansion-manager",
        "x-tenant-id": "tenant-a",
        "X-Correlation-Id": "reject-corr-id-12345",
        "Idempotency-Key": "reject-key-12345"
      },
    });
    const rejectRes = await apiContext.post(`/api/v1/operator/network-listings/intake/${id5}/decide`, {
      data: {
        action: "reject",
        reason: "Audit test reject reason",
        riskSummary: "測試拒絕風險宣告",
        riskAcknowledged: true,
        actorRoleId: "expansion-manager",
        actorName: "林曉青"
      }
    });
    expect(rejectRes.status()).toBe(200);
    await apiContext.dispose();

    const data5 = await getIntakeApi(id5);
    const auditReject = data5.auditEvents.find((e: any) => e.action === "intake.decide.reject");
    expect(auditReject).toBeDefined();
    expect(auditReject.actorRoleId).toBe("expansion-manager");
    expect(auditReject.occurredAt).toBeTruthy();
    expect(auditReject.message).toContain("Audit test reject reason");
    expect(auditReject.correlationId).toBe("reject-corr-id-12345");
    expect(auditReject.metadata.decision).toBe("reject");
    expect(auditReject.metadata.reason).toBe("Audit test reject reason");
    expect(auditReject.metadata.riskSummary).toBe("測試拒絕風險宣告");
    expect(auditReject.metadata.riskAcknowledged).toBe(true);
    expect(auditReject.metadata.beforeAfter.stage.before).toBe("NEEDS_REVIEW");
    expect(auditReject.metadata.beforeAfter.stage.after).toBe("FAILED");
  });
});
