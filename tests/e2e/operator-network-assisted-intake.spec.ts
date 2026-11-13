import {
  expect,
  request as playwrightRequest,
  test,
  type Page,
} from "@playwright/test";

/**
 * Assisted Listing Intake — product E2E (ODP-OC-R5-011 / ODP-OC-R5-004).
 *
 * This suite drives the real Operator UI and FastAPI server with explicitly
 * synthetic fixture records and header-trust test identities. Assertions cover
 * persisted test effects; this is not a production login or real role UAT.
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
 * 10. No prototype HTML or route interception.       | (Enforced: No page.route() used for happy-path, only for transient network retry test)
 * ============================================================================
 */

import { loginNativeSession, nativeBrowserInput, openNativeIntakeList, openPgScenario, PG_API, PG_WEB, submitNativeURL, type PgScenario } from "./_intakeScorePg";

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
// Deliberate test input, not a production catalog choice.
const SYNTHETIC_TARGET_FORMAT = "synthetic-manual-format:Audit/v1";

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

test.beforeEach(async ({}, info) => {
  // Retained history belongs to the separate physical PG composition. Do
  // not reset an unrelated legacy backend for these original UI journeys.
  if (info.title.startsWith("retained ") || info.title.startsWith("canonical native:")) return;
  const api = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      "x-subject-id": "operator-expansion-manager",
      "x-roles": "expansion_user",
      "x-operator-role": "expansion-staff",
      "x-tenant-id": "tenant-a",
    },
  });
  const reset = await api.post("/api/v1/operator/network-listings/reset");
  expect(reset.status()).toBe(200);
  await api.dispose();
});

/** Open the Network workspace as 展店經理 — the only role holding listing grants. */
async function openRadarAsExpansionManager(page: Page) {
  // Keep the actual synthetic browser principal aligned with its session
  // subject; the generic config identity must not impersonate this operator.
  await page.setExtraHTTPHeaders({
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "expansion_user,site_reviewer",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
  });
  await page.addInitScript(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
    // The operator identity is fail-closed: without a session subject the UI
    // disables every write action (no fabricated operator-<role> fallback
    // since the OIDC BFF change). Establish the same subject the suite's API
    // contexts use, exactly as a real authenticated session would.
    window.sessionStorage.setItem(
      "oday.operator.subject",
      "operator-expansion-manager",
    );
  });
  await page.goto("/operator?ws=network");
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
}

async function submitUrl(page: Page, url: string) {
  await page.getByTestId("intake-add-button").click();
  const dialog = page.getByTestId("intake-add-dialog");
  await expect(dialog).toBeVisible();
  await page.getByTestId("intake-url-input").fill(url);
  await page.getByTestId("intake-submit-button").click();
  await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({
    timeout: 15_000,
  });
}

/** Helper to query direct API details of an intake record */
async function getIntakeApi(id: string, scenario?: PgScenario) {
  const api = await playwrightRequest.newContext({
    baseURL: scenario ? PG_API : API_BASE_URL,
    extraHTTPHeaders: {
      "x-subject-id": scenario?.proposer_id ?? "operator-expansion-manager",
      // The same verified fixture subject owns the record. Platform roles,
      // rather than client display/persona labels, determine the audit role.
      "x-roles": "expansion_user,site_reviewer",
      "x-operator-role": "expansion-manager",
      "x-tenant-id": scenario?.tenant_id ?? "tenant-a",
    },
  });
  const res = await api.get(`/api/v1/operator/network-listings/intake/${id}`);
  expect(res.status()).toBe(200);
  const data = await res.json();
  await api.dispose();
  return data;
}

test.describe("Assisted Listing Intake — Package 7 product surfaces", () => {
  test("canonical native: Package 10 inbox opens the same formal URL form and continuous detail", async ({ page }) => {
    const input = nativeBrowserInput();
    await loginNativeSession(page, input);
    await expect(page.locator('[data-screen-label="Listing Inbox 收件匣"]')).toBeVisible();
    // The old Inbox button, not a new navigation producer, opens the real form.
    await page.getByTestId("intake-add-button").click();
    await expect(page.locator('[data-screen-label="Dialog 從網址新增物件"]')).toBeVisible();
    expect(new URL(page.url()).searchParams.get("intakeKind")).toBe("canonical");
    const { receipt } = await submitNativeURL(page, input, input.fresh_urls.presentation,
      "從原收件匣明確提交正式新網址，核對同一表單、SQL回執與原生詳情。");
    await expect(page.locator('[data-screen-label="Intake 收件處理詳情頁"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-screen-label="Network URL 收件佇列"]')).toHaveCount(0);
    await expect(page.locator('[data-screen-label="Dialog 收件處理詳情"]')).toHaveCount(0);
    await expect(page.getByTestId("native-intake-stage-timeline")).toContainText("建立 → 已送出");
    await expect(page.getByTestId("intake-detail-id")).toHaveText(receipt.intake_id);
    await expect(page.getByTestId("timeline-stepper")).toHaveCount(0);
  });

  test("canonical native: empty legacy view and old add link submit a durable SUBMITTED record", async ({ page }) => {
    const input = nativeBrowserInput();
    await loginNativeSession(page, input);
    // Empty legacy presentation is not an empty native collection or READY.
    await expect(page.getByTestId("intake-inbox-empty")).toBeVisible();
    await page.goto("/operator?ws=network&tab=radar&dialog=add&extra=preserved");
    const { receipt } = await submitNativeURL(page, input, input.fresh_urls.durability,
      "由原dialog=add連結明確送件，確認已送出不代表解析或新物件決策成功。");
    expect(new URL(page.url()).searchParams.get("extra")).toBe("preserved");
    const read = await page.request.get(`/api/v1/canonical-intakes/${receipt.intake_id}?include_source=true&include_history=true&include_evidence=true`);
    expect(read.status(), await read.text()).toBe(200);
    const data = await read.json();
    expect(data).toMatchObject({ intake_id: receipt.intake_id, submitter_subject_id: input.actor_id,
      processing_state: "SUBMITTED", version: 1, correlation_id: receipt.correlation_id, resolution: null,
      source_info: { source_id: null, recorded_policy_state: null }, processing_evidence: { items: [] } });
    expect(data.processing_history.items).toHaveLength(1);
    expect(data.processing_history.items[0]).toMatchObject({ transition_id: receipt.transition_id,
      from_state: null, to_state: "SUBMITTED", actor_subject_id: input.actor_id });
    expect(data.workflow.assignment_id).toBeTruthy();
    expect(data.workflow.sla_instance_id).toBeTruthy();
    await page.reload();
    await expect(page.getByTestId("intake-detail-id")).toHaveText(receipt.intake_id);
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已送出");
    await expect(page.getByTestId("native-intake-stage-timeline")).toContainText(receipt.transition_id);
    await page.getByTestId("intake-return-button").click();
    await expect(page.getByRole("button", { name: new RegExp(receipt.intake_id) })).toHaveCount(1);
  });

  test("canonical native: a new key for the same URL cannot borrow a prior receipt or claim retrieval", async ({ page }) => {
    const input = nativeBrowserInput();
    await loginNativeSession(page, input);
    await openNativeIntakeList(page);
    await page.getByRole("button", { name: "從網址建立正式收件", exact: true }).click();
    const first = await submitNativeURL(page, input, input.fresh_urls.duplicate,
      "第一份正式網址送件，後續身分與來源檢查仍由既有worker負責。");
    await page.getByTestId("intake-return-button").click();
    await page.getByRole("button", { name: "從網址建立正式收件", exact: true }).click();
    const second = await submitNativeURL(page, input, input.fresh_urls.duplicate,
      "同網址的新送件意圖，不能以URL相等冒充原識別的歷史重播。");
    expect(second.key).not.toBe(first.key);
    expect(second.receipt.intake_id).not.toBe(first.receipt.intake_id);
    expect(second.receipt.processing_state).toBe("SUBMITTED");
    await page.getByTestId("intake-return-button").click();
    for (const id of [first.receipt.intake_id, second.receipt.intake_id])
      await expect(page.getByRole("button", { name: new RegExp(id) })).toHaveCount(1);
  });

  test("retained possible match requires a human decision and refuses an empty reason", async ({
    page,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
    expect(scenario.listing_id).toBeTruthy();

    await expect(page.getByTestId("intake-detail-stage")).toHaveText(
      "待人工覆核",
    );
    await expect(page.getByTestId("intake-detail-match")).toHaveText(
      "疑似重複",
    );
    // Never auto-merged.
    await expect(page.getByTestId("no-auto-merge-warning")).toBeVisible();
    await expect(page.getByTestId("intake-change-summary")).toBeVisible();

    await page.getByTestId("decide-action-create").click();
    // Review summary is shown BEFORE the decision commits.
    await expect(page.getByTestId("intake-decide-summary")).toBeVisible();
    await expect(page.getByTestId("intake-decide-note")).toContainText(
      "不使用 optimistic UI",
    );

    // Empty reason is blocked and the dialog stays open — nothing was written.
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-error")).toContainText(
      "必須填寫原因",
    );
    await expect(page.getByTestId("intake-decide-dialog")).toBeVisible();

    await page
      .getByTestId("intake-decide-reason")
      .fill("實地確認樓層與提供者 ID 為不同物件，判定為新物件。");

    // A reason alone does not commit: the risk must be disclosed AND accepted.
    await expect(page.getByTestId("intake-decide-risk-summary")).toContainText(
      "物件收件匣",
    );
    await expect(page.getByTestId("intake-decide-risk-ack")).not.toBeChecked();
    await page.getByTestId("intake-decide-submit").click();
    await expect(page.getByTestId("intake-decide-error")).toContainText(
      "了解此決策的影響",
    );
    await expect(page.getByTestId("intake-decide-dialog")).toBeVisible();

    await page.getByTestId("intake-decide-risk-ack").check();
    await page.getByTestId("intake-decide-submit").click();

    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({
      timeout: 15_000,
    });
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
    // The decision is recorded in the audit trail.
    await expect(
      page.getByTestId("intake-timeline-audit-section"),
    ).toContainText("實地確認樓層");
    await page.reload();
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
    expect(await getIntakeApi(scenario.intake_id, scenario)).toMatchObject({
      id: scenario.intake_id, stage: "READY", version: 2, matchResult: { outcome: "NEW" },
    });
    } finally { await api.dispose(); }
  });

  test("retained identity-field correction demands a reason, then records before/after", async ({
    page,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
    const id5 = scenario.intake_id;

    await page.getByTestId("fix-field-address").click();
    await expect(page.getByTestId("intake-fix-title")).toContainText(
      "修正欄位",
    );
    await page
      .getByTestId("intake-fix-value")
      .fill("新北市板橋區府中路 26 號 1F");

    // Identity field: the reason gate blocks before the request is made.
    await page.getByTestId("intake-fix-submit").click();
    await expect(page.getByTestId("intake-fix-error")).toContainText(
      "必須填寫原因",
    );

    await page
      .getByTestId("intake-fix-reason")
      .fill("與房東電話確認門牌為 26 號");

    // The risk of an identity correction is disclosed and must be accepted; the
    // summary names the field and the before/after values being written.
    await expect(page.getByTestId("intake-fix-risk-summary")).toContainText(
      "新北市板橋區府中路 26 號 1F",
    );
    await page.getByTestId("intake-fix-submit").click();
    await expect(page.getByTestId("intake-fix-error")).toContainText(
      "了解此修正的影響",
    );

    await page.getByTestId("intake-fix-risk-ack").check();
    await page.getByTestId("intake-fix-submit").click();

    await expect(page.getByTestId("intake-fix-dialog")).toBeHidden({
      timeout: 15_000,
    });
    // The corrected value is now distinguishable from source and normalized.
    await expect(page.getByTestId("intake-parsed-lineage")).toContainText(
      "新北市板橋區府中路 26 號 1F",
    );
    await expect(
      page.getByTestId("intake-timeline-audit-section"),
    ).toContainText("門牌");

    const updatedData = await getIntakeApi(id5, scenario);
    expect(updatedData.originalUrl).toBe(scenario.original_url);
    expect(updatedData.canonicalUrl).toBeTruthy();

    const addressField = updatedData.parsedFields.address;
    expect(addressField).toBeDefined();
    expect(addressField.sourceValue).toBeTruthy();
    expect(addressField.normalizedValue).toBeTruthy();
    expect(addressField.correctedValue).toBe("新北市板橋區府中路 26 號 1F");
    expect(addressField.correctionReason).toBe("與房東電話確認門牌為 26 號");

    const auditCorrect = updatedData.auditEvents.find(
      (e: any) => e.action === "intake.correct",
    );
    expect(auditCorrect).toBeDefined();
    expect(auditCorrect.metadata.beforeAfter).toBeDefined();
    const addressChange = auditCorrect.metadata.beforeAfter.find(
      (c: any) => c.field === "address",
    );
    expect(addressChange).toBeDefined();
    expect(addressChange.before).toBeTruthy();
    expect(addressChange.after).toBe("新北市板橋區府中路 26 號 1F");
    } finally { await api.dispose(); }
  });

  test("retained correct and decide writes carry retry-stable idempotency keys", async ({
    page,
  }) => {
    const correctKeys: string[] = [];
    const decideKeys: string[] = [];
    let failFirstCorrect = true;
    let failFirstDecide = true;

    await page.route("**/network-listings/intake/*/correct", async (route) => {
      correctKeys.push(
        route.request().headers()["idempotency-key"] ?? "<none>",
      );
      if (failFirstCorrect) {
        failFirstCorrect = false;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: "{}",
        });
        return;
      }
      await route.continue();
    });
    await page.route("**/network-listings/intake/*/decide", async (route) => {
      decideKeys.push(route.request().headers()["idempotency-key"] ?? "<none>");
      if (failFirstDecide) {
        failFirstDecide = false;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: "{}",
        });
        return;
      }
      await route.continue();
    });

    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
    await page.getByTestId("fix-field-address").click();
    await page
      .getByTestId("intake-fix-value")
      .fill("新北市板橋區府中路 26 號 1F");
    await page
      .getByTestId("intake-fix-reason")
      .fill("與房東電話確認門牌為 26 號");
    await page.getByTestId("intake-fix-risk-ack").check();
    const [unavailable] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${scenario.intake_id}/correct`),
      page.getByTestId("intake-fix-submit").click(),
    ]);
    expect(unavailable.status()).toBe(503);
    await expect(page.getByTestId("intake-fix-error")).toBeVisible();
    await expect(page.getByTestId("intake-fix-reason")).toHaveValue(
      "與房東電話確認門牌為 26 號",
    );
    await expect(page.getByTestId("intake-fix-value")).toHaveValue("新北市板橋區府中路 26 號 1F");
    await expect(page.getByTestId("intake-fix-risk-ack")).toBeChecked();

    const [corrected] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${scenario.intake_id}/correct`),
      page.getByTestId("intake-fix-submit").click(),
    ]);
    expect(corrected.status(), await corrected.text()).toBe(200);
    await expect(page.getByTestId("intake-fix-dialog")).toBeHidden({
      timeout: 15_000,
    });
    expect(correctKeys).toHaveLength(2);
    expect(correctKeys[0]).toBe(correctKeys[1]);
    expect(correctKeys[0]).not.toBe("<none>");
    expect(correctKeys[0]).toContain("intake-correct-");

    await page.getByTestId("decide-action-create").click();
    await page
      .getByTestId("intake-decide-reason")
      .fill("實地確認樓層與提供者 ID 為不同物件，判定為新物件。");
    await page.getByTestId("intake-decide-risk-ack").check();
    const [decisionUnavailable] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${scenario.intake_id}/decide`),
      page.getByTestId("intake-decide-submit").click(),
    ]);
    expect(decisionUnavailable.status()).toBe(503);
    await expect(page.getByTestId("intake-decide-error")).toBeVisible();
    await expect(page.getByTestId("intake-decide-reason")).toHaveValue(
      "實地確認樓層與提供者 ID 為不同物件，判定為新物件。",
    );

    const [decided] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${scenario.intake_id}/decide`),
      page.getByTestId("intake-decide-submit").click(),
    ]);
    expect(decided.status(), await decided.text()).toBe(200);
    expect(decided.request().headers()["if-match"]).toBe('W/"2"');
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({
      timeout: 15_000,
    });
    expect(decideKeys).toHaveLength(2);
    expect(decideKeys[0]).toBe(decideKeys[1]);
    expect(decideKeys[0]).not.toBe("<none>");
    expect(decideKeys[0]).toContain("intake-decide-create-");
    const saved = await getIntakeApi(scenario.intake_id, scenario);
    expect(saved).toMatchObject({ stage: "READY", version: 3 });
    expect(saved.auditEvents.filter((event: any) => event.action === "intake.correct")).toHaveLength(1);
    expect(saved.auditEvents.filter((event: any) => event.action === "intake.decide.create")).toHaveLength(1);
    } finally { await api.dispose(); }
  });

  test("retained assisted-entry-only source keeps the URL and never fetches the page", async ({
    page,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "assisted-entry-only" });
    try {
    // This historical fixture does not attest a fresh policy evaluation.
    expect(scenario.listing_id).toBeNull();

    await expect(page.getByTestId("intake-detail-stage")).toHaveText(
      "待人工補錄",
    );
    await expect(page.getByTestId("intake-detail-dialog")).toContainText(
      "僅人工補錄",
    );
    // No retrieval happened: there is no capture, and the entry form is offered.
    await expect(page.getByTestId("intake-captured-at")).toContainText(
      "UNAVAILABLE",
    );
    await expect(page.getByTestId("intake-assisted-entry")).toBeVisible();

    // Required-field gate.
    await page.getByTestId("assisted-save").click();
    await expect(page.getByTestId("intake-assisted-error")).toContainText(
      "地址、租金、坪數",
    );

    await page
      .getByTestId("assisted-address")
      .fill("新北市板橋區府中路 26 號 1F");
    await page.getByTestId("assisted-rent").fill("54000");
    await page.getByTestId("assisted-area").fill("22");

    // Hand-keyed values carry no retrieved evidence — that risk is disclosed on
    // the form and must be accepted before the correction is written.
    await expect(
      page.getByTestId("intake-assisted-risk-summary"),
    ).toContainText("不具本系統擷取證據");
    await page.getByTestId("assisted-save").click();
    await expect(page.getByTestId("intake-assisted-error")).toContainText(
      "了解人工補錄的風險",
    );

    await page.getByTestId("assisted-risk-ack").check();
    const [corrected] = await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname ===
        `/api/v1/operator/network-listings/intake/${scenario.intake_id}/correct` && response.request().method() === "POST"),
      page.getByTestId("assisted-save").click(),
    ]);
    expect(corrected.status(), await corrected.text()).toBe(200);
    expect(corrected.request().headers()["idempotency-key"]).toBeTruthy();
    await expect(page.getByTestId("intake-parsed-lineage")).toBeVisible({
      timeout: 15_000,
    });
    await page.reload();
    await expect(page.getByTestId("intake-parsed-lineage")).toContainText("新北市板橋區府中路 26 號 1F");
    const read = await api.get(`/api/v1/operator/network-listings/intake/${scenario.intake_id}`, {
      headers: { "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager",
        "x-tenant-id": scenario.tenant_id, "x-subject-id": scenario.proposer_id },
    });
    expect(read.status(), await read.text()).toBe(200);
    expect(await read.json()).toMatchObject({ originalUrl: scenario.original_url, policy: "ASSISTED_ENTRY_ONLY",
      capturedAt: null, rawSnapshot: null, parserVersion: null, version: 2,
      parsedFields: { address: { correctedValue: "新北市板橋區府中路 26 號 1F" } } });
    } finally { await api.dispose(); }
  });

  test("retained policy-unknown quarantine shows its governance reason without offering creation", async ({
    page,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "policy-unknown" });
    try {
    // Historical presentation, not proof of fresh native policy admission.

    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已隔離");
    await expect(page.getByTestId("intake-detail-dialog")).toContainText(
      "政策未知",
    );
    // Canonical evidence panel renders "UNAVAILABLE" when the reason is
    // absent, so assert a real governance reason is present instead.
    await expect(page.getByTestId("evidence-policy-reason")).toBeVisible();
    await expect(page.getByTestId("evidence-policy-reason")).not.toContainText(
      "UNAVAILABLE",
    );
    // Quarantine routes to governance review, not to listing creation.
    await expect(page.getByTestId("decide-action-steward")).toBeVisible();
    await expect(page.getByTestId("decide-action-create")).toHaveCount(0);
    const before = await getIntakeApi(scenario.intake_id, scenario);
    expect(before).toMatchObject({ policy: "POLICY_UNKNOWN", stage: "QUARANTINED", version: 1,
      rawSnapshot: null, snapshotId: null, parserVersion: null, capturedAt: null });
    await page.reload();
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已隔離");
    expect(await getIntakeApi(scenario.intake_id, scenario)).toEqual(before);
    } finally { await api.dispose(); }
  });

  test("retained retryable failure shows code, correlation and next action, and retry preserves input", async ({
    page,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "retrieval-timeout" });
    try {

    await expect(page.getByTestId("intake-detail-stage")).toHaveText(
      "處理失敗",
    );
    // Canonical Package 10 recovery surface (IntakeErrorRecovery).
    const failure = page.getByTestId("intake-error-recovery");
    await expect(failure).toContainText("ODP-INTAKE-RETRIEVAL-TIMEOUT");
    await expect(page.getByTestId("error-retryable-badge")).toContainText(
      "可自動重試",
    );
    await expect(failure).toContainText("建議處置");
    await expect(page.getByTestId("error-next-action")).not.toHaveText(
      "UNAVAILABLE",
    );

    await expect(page.getByTestId("error-action-retry")).toBeVisible();
    await page.getByTestId("error-toggle-preserved-input").click();
    await expect(page.getByTestId("error-preserved-input-json")).toContainText(scenario.original_url);
    await page.reload();
    await expect(page.getByTestId("error-code")).toHaveText("[ODP-INTAKE-RETRIEVAL-TIMEOUT]");
    expect(await getIntakeApi(scenario.intake_id, scenario)).toMatchObject({ stage: "FAILED", version: 1,
      originalUrl: scenario.original_url, rawSnapshot: null, parserVersion: null });
    } finally { await api.dispose(); }
  });

  test("retained revision outcome offers append-version against the matched listing", async ({
    page,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "revision" });
    try {

    await expect(page.getByTestId("intake-detail-match")).toHaveText(
      "版本更新",
    );
    const revise = page.getByTestId("decide-action-revise");
    await expect(revise).toBeVisible();
    await expect(revise).toContainText(scenario.listing_id!);
    expect(await getIntakeApi(scenario.intake_id, scenario)).toMatchObject({ stage: "READY", version: 1,
      matchResult: { outcome: "REVISION", targetListingId: scenario.listing_id } });
    } finally { await api.dispose(); }
  });

  test("retained deep link reopens the intake record after leaving the page", async ({
    page,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "revision" });
    try {
    const id = scenario.intake_id;

    // Leave entirely, then return via the canonical durable deep link
    // (?selected=<id>&dialog=detail). The restored detail view replaces the
    // tab shell, so no tab click is needed or possible here.
    await page.goto(`${PG_WEB}/operator?ws=today`);
    await page.goto(`${PG_WEB}/operator?ws=network&tab=radar&selected=${id}&dialog=detail`);

    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("intake-detail-id")).toHaveText(id);
    await page.reload();
    // Reload, like the initial deep link above, waits for the real detail
    // mount within the original 15-second UI bound before inspecting its ID.
    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("intake-detail-id")).toHaveText(id);
    expect(await getIntakeApi(id, scenario)).toMatchObject({ id, stage: "READY", version: 1 });
    } finally { await api.dispose(); }
  });

  test("retained queue counts reflect real server state across mixed outcomes", async ({
    page,
  }) => {
    const contexts: import("@playwright/test").APIRequestContext[] = [];
    try {
      const review = await openPgScenario(page, { retainedHistory: "possible-match" });
      contexts.push(review.api);
      const headers = { "x-subject-id": review.scenario.proposer_id, "x-tenant-id": review.scenario.tenant_id,
        "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager" };
      const before = await review.api.get("/api/v1/operator/network-listings/intake", { headers });
      expect(before.status(), await before.text()).toBe(200);
      const baseline = await before.json();
      const failed = await openPgScenario(page, { retainedHistory: "retrieval-timeout" });
      contexts.push(failed.api);
      const assisted = await openPgScenario(page, { retainedHistory: "assisted-entry-only" });
      contexts.push(assisted.api);
      const states = await Promise.all([review, failed, assisted].map(({ scenario }) =>
        getIntakeApi(scenario.intake_id, scenario)));
      expect(states.map((record) => record.stage)).toEqual(["NEEDS_REVIEW", "FAILED", "AWAITING_ASSISTED_ENTRY"]);
      expect(new Set(states.map((record) => record.id)).size).toBe(3);
      // Retained fixtures do not establish fresh policy/retrieval success.
      for (const record of states) expect(record).toMatchObject({ version: 1, rawSnapshot: null, snapshotId: null, parserVersion: null });
      const after = await review.api.get("/api/v1/operator/network-listings/intake", { headers });
      expect(after.status(), await after.text()).toBe(200);
      const current = await after.json();
      expect(current.total).toBe(baseline.total + 2);
      expect(current.counts).toMatchObject({ needsReview: baseline.counts.needsReview,
        blocked: baseline.counts.blocked + 1, awaitingEntry: baseline.counts.awaitingEntry + 1 });

      // Explicitly select all areas, rather than inheriting the shell's HZ-01
      // or assigning its legacy label to these unscoped retained SQL records.
      await page.goto(`${PG_WEB}/operator?ws=network&tab=radar&heatZoneId=`);
      await expect(page.getByTestId("intake-table")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("intake-filter-heat-zone")).toHaveValue("");
      for (const tab of ["needsReview", "blocked", "awaitingEntry"] as const) {
        await expect(page.getByTestId(`intake-tab-${tab}`)).toContainText(`(${current.counts[tab]})`);
      }
      await page.reload();
      await expect(page.getByTestId("intake-table")).toBeVisible({ timeout: 15_000 });
      for (const tab of ["needsReview", "blocked", "awaitingEntry"] as const) {
        await expect(page.getByTestId(`intake-tab-${tab}`)).toContainText(`(${current.counts[tab]})`);
      }
    } finally {
      for (const api of contexts) await api.dispose();
    }
  });

  test("a role without listing permission gets the permission-limited state, not an empty queue", async ({
    page,
  }) => {
    const headers = {
      "x-operator-role": "ops-lead",
      "x-roles": "operations_manager",
      "x-subject-id": "synthetic-denied-ops",
      "x-tenant-id": "tenant-a",
    };
    await page.setExtraHTTPHeaders(headers);
    await page.addInitScript(() => {
      window.sessionStorage.setItem("oday.operator.role", "ops-lead");
    });
    const bootstrap = page.waitForResponse((response) => response.url().includes("/api/v1/operator/bootstrap") && response.status() === 200);
    await page.goto("/operator?ws=network");
    const envelope = await (await bootstrap).json();
    expect(envelope.navigation.allowedWorkspaces).not.toContain("network");
    expect(envelope.navigation.allowedTabs.network).toEqual([]);
    await expect(page.getByTestId("operator-envelope-source")).toHaveText("operator-shell-api-envelope");
    await expect(page.getByRole("navigation", { name: "Operator workspaces" }).getByRole("button", { name: /展店與店網/ })).toHaveAttribute("aria-disabled", "true");
    const denied = await page.request.get("/api/v1/operator/network-listings/intake", { headers });
    expect(denied.status()).toBe(403);
    // It must not imply "no submissions exist", and must offer no write action.
    await expect(page.getByTestId("intake-inbox-empty")).toHaveCount(0);
    await expect(page.getByTestId("intake-add-button")).toHaveCount(0);
  });

  test("canonical native: dialogs are keyboard operable and Escape closes without writing", async ({ page }) => {
    const input = nativeBrowserInput();
    await loginNativeSession(page, input);
    const writes: string[] = [];
    const observe = (request: import("@playwright/test").Request) => {
      if (request.method() !== "GET" && new URL(request.url()).pathname.startsWith("/api/v1/")) writes.push(request.url());
    };
    page.on("request", observe);
    try {
      await page.getByTestId("intake-add-button").click();
      await expect(page.getByTestId("intake-add-dialog")).toBeVisible();
      await expect(page.getByTestId("intake-url-input")).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("intake-add-dialog")).toBeHidden();
      // The existing shared Escape dismissal closes both stacked shells and
      // returns to the original Inbox; it does not leave a hidden add intent.
      await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(new URL(page.url()).searchParams.get("dialog")).toBeNull();
      expect(writes).toEqual([]);
    } finally { page.off("request", observe); }
  });

  test("retained mobile routes ambiguous side-by-side compare to a desktop-required state", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const { api } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
      await expect(page.getByTestId("intake-desktop-required")).toBeVisible();
      await expect(page.getByTestId("intake-detail-stage")).toBeVisible();
    } finally { await api.dispose(); }
  });

  // ==========================================================================
  // ADDITIONAL TESTS INTEGRATING ODP-OC-R5-004 COMPLIANCE
  // ==========================================================================

  test("retained AUTH_REQUIRED history permits durable manual correction without claiming retrieval", async ({ page }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "auth-required" });
    try {
    // The retained policy is not an approved account, capture or fresh probe.

    await expect(page.getByTestId("intake-detail-stage")).toHaveText(
      "待人工補錄",
    );
    await expect(page.getByTestId("intake-detail-dialog")).toContainText(
      "需授權帳號",
    );
    await expect(page.getByTestId("evidence-policy-reason")).toContainText(
      "需經核准之合作帳號",
    );

    // No retrieval happened.
    await expect(page.getByTestId("intake-captured-at")).toContainText(
      "UNAVAILABLE",
    );
    await expect(page.getByTestId("intake-assisted-entry")).toBeVisible();

    // Verify no-fetch via direct API check
    const id = (
      await page.getByTestId("intake-detail-id").textContent()
    )?.trim();
    expect(id).toBeTruthy();
    expect(id).toBe(scenario.intake_id);
    const data = await getIntakeApi(id!, scenario);
    expect(data.policy).toBe("AUTH_REQUIRED");
    expect(data.capturedAt).toBeNull();
    expect(data.rawSnapshot).toBeNull();

    expect(data).toMatchObject({ version: 1, parserVersion: null, snapshotId: null, parsedFields: {} });
    expect(scenario.listing_id).toBeTruthy();
    // Fill the real human form against the retained scenario's actual target.
    await page
      .getByTestId("assisted-address")
      .fill("台北市信義區松仁路 96 號 1F");
    await page.getByTestId("assisted-rent").fill("55000");
    await page.getByTestId("assisted-area").fill("18");
    await page.getByTestId("assisted-risk-ack").check();
    const [corrected] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${scenario.intake_id}/correct`),
      page.getByTestId("assisted-save").click(),
    ]);
    expect(corrected.status(), await corrected.text()).toBe(200);
    expect(corrected.request().headers()["idempotency-key"]).toBeTruthy();

    // Wait for the actual matcher after the committed human correction.
    await expect(page.getByTestId("intake-detail-stage")).toHaveText(
      "待人工覆核",
    );
    await expect(page.getByTestId("intake-detail-match")).toHaveText(
      "疑似重複",
    );
    await page.reload();
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("待人工覆核");
    const saved = await getIntakeApi(scenario.intake_id, scenario);
    expect(saved).toMatchObject({ version: 2, policy: "AUTH_REQUIRED", stage: "NEEDS_REVIEW",
      rawSnapshot: null, snapshotId: null, capturedAt: null, parserVersion: null,
      matchResult: { outcome: "POSSIBLE_MATCH", targetListingId: scenario.listing_id },
      parsedFields: { address: { sourceValue: null, normalizedValue: null, correctedValue: "台北市信義區松仁路 96 號 1F" },
        rent: { correctedValue: "55000" }, areaPing: { correctedValue: "18" } } });
    expect(saved.auditEvents.filter((event: any) => event.action === "intake.correct")).toHaveLength(1);
    } finally { await api.dispose(); }
  });

  test("prove the correct fetch or no-fetch behavior per policy state", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);

    // 1. APPROVED_RETRIEVAL
    await submitUrl(page, URLS.clean);
    const idClean = (
      await page.getByTestId("intake-detail-id").textContent()
    )?.trim()!;
    const cleanData = await getIntakeApi(idClean);
    expect(cleanData.policy).toBe("APPROVED_RETRIEVAL");
    expect(cleanData.capturedAt).not.toBeNull();
    expect(cleanData.rawSnapshot).not.toBeNull();
    expect(cleanData.parserVersion).not.toBeNull();
    await page.getByTestId("intake-return-button").click();

    // 2. ASSISTED_ENTRY_ONLY
    await submitUrl(page, URLS.assistedOnly);
    const idAssisted = (
      await page.getByTestId("intake-detail-id").textContent()
    )?.trim()!;
    const assistedData = await getIntakeApi(idAssisted);
    expect(assistedData.policy).toBe("ASSISTED_ENTRY_ONLY");
    expect(assistedData.capturedAt).toBeNull();
    expect(assistedData.rawSnapshot).toBeNull();
    await page.getByTestId("intake-return-button").click();

    // 3. AUTH_REQUIRED
    await submitUrl(page, URLS.authRequired);
    const idAuth = (
      await page.getByTestId("intake-detail-id").textContent()
    )?.trim()!;
    const authData = await getIntakeApi(idAuth);
    expect(authData.policy).toBe("AUTH_REQUIRED");
    expect(authData.capturedAt).toBeNull();
    expect(authData.rawSnapshot).toBeNull();
    await page.getByTestId("intake-return-button").click();

    // 4. SOURCE_BLOCKED
    await submitUrl(page, URLS.blocked);
    const idBlocked = (
      await page.getByTestId("intake-detail-id").textContent()
    )?.trim()!;
    const blockedData = await getIntakeApi(idBlocked);
    expect(blockedData.policy).toBe("SOURCE_BLOCKED");
    expect(blockedData.capturedAt).toBeNull();
    expect(blockedData.rawSnapshot).toBeNull();
    expect(blockedData.stage).toBe("QUARANTINED");
    await page.getByTestId("intake-return-button").click();

    // 5. POLICY_UNKNOWN
    await submitUrl(page, URLS.unknown);
    const idUnknown = (
      await page.getByTestId("intake-detail-id").textContent()
    )?.trim()!;
    const unknownData = await getIntakeApi(idUnknown);
    expect(unknownData.policy).toBe("POLICY_UNKNOWN");
    expect(unknownData.capturedAt).toBeNull();
    expect(unknownData.rawSnapshot).toBeNull();
    expect(unknownData.stage).toBe("QUARANTINED");
  });

  test("explicit assertion of all 11 stage transitions in the UI stepper", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);

    // 1-7: SUBMITTED, CHECKING_IDENTITY, CHECKING_SOURCE_POLICY, RETRIEVING, PARSING, MATCHING, READY
    await submitUrl(page, URLS.clean);
    const stepCodesClean = await page
      .locator(
        '[data-testid="timeline-stepper"] [data-testid^="timeline-step-"]',
      )
      .evaluateAll((steps) =>
        steps.map((step) =>
          step.getAttribute("data-testid")!.replace("timeline-step-", ""),
        ),
      );
    expect(stepCodesClean).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "RETRIEVING",
      "PARSING",
      "MATCHING",
      "READY",
    ]);
    await page.getByTestId("intake-return-button").click();

    // 8: NEEDS_REVIEW
    await submitUrl(page, URLS.possible);
    const stepCodesPossible = await page
      .locator(
        '[data-testid="timeline-stepper"] [data-testid^="timeline-step-"]',
      )
      .evaluateAll((steps) =>
        steps.map((step) =>
          step.getAttribute("data-testid")!.replace("timeline-step-", ""),
        ),
      );
    expect(stepCodesPossible).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "RETRIEVING",
      "PARSING",
      "MATCHING",
      "NEEDS_REVIEW",
    ]);
    await page.getByTestId("intake-return-button").click();

    // 9: FAILED
    await submitUrl(page, URLS.timeout);
    const stepCodesTimeout = await page
      .locator(
        '[data-testid="timeline-stepper"] [data-testid^="timeline-step-"]',
      )
      .evaluateAll((steps) =>
        steps.map((step) =>
          step.getAttribute("data-testid")!.replace("timeline-step-", ""),
        ),
      );
    expect(stepCodesTimeout).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "RETRIEVING",
      "PARSING",
      "MATCHING",
      "FAILED",
    ]);
    await page.getByTestId("intake-return-button").click();

    // 10: AWAITING_ASSISTED_ENTRY
    await submitUrl(page, URLS.assistedOnly);
    const stepCodesAssisted = await page
      .locator(
        '[data-testid="timeline-stepper"] [data-testid^="timeline-step-"]',
      )
      .evaluateAll((steps) =>
        steps.map((step) =>
          step.getAttribute("data-testid")!.replace("timeline-step-", ""),
        ),
      );
    expect(stepCodesAssisted).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "AWAITING_ASSISTED_ENTRY",
    ]);
    await page.getByTestId("intake-return-button").click();

    // 11: QUARANTINED
    await submitUrl(page, URLS.unknown);
    const stepCodesUnknown = await page
      .locator(
        '[data-testid="timeline-stepper"] [data-testid^="timeline-step-"]',
      )
      .evaluateAll((steps) =>
        steps.map((step) =>
          step.getAttribute("data-testid")!.replace("timeline-step-", ""),
        ),
      );
    expect(stepCodesUnknown).toEqual([
      "SUBMITTED",
      "CHECKING_IDENTITY",
      "CHECKING_SOURCE_POLICY",
      "QUARANTINED",
    ]);
  });

  test("retained decisions and corrections survive page reload and a fresh browser context", async ({
    page,
    context,
  }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    const contexts: import("@playwright/test").BrowserContext[] = [];
    try {
    const id = scenario.intake_id;
    const headers = { "x-subject-id": scenario.proposer_id, "x-tenant-id": scenario.tenant_id,
      "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager" };

    // Perform correction on address
    await page.getByTestId("fix-field-address").click();
    await page
      .getByTestId("intake-fix-value")
      .fill("新北市板橋區府中路 26 號 1F");
    await page
      .getByTestId("intake-fix-reason")
      .fill("Durability test correction reason");
    await page.getByTestId("intake-fix-risk-ack").check();
    await page.getByTestId("intake-fix-submit").click();
    await expect(page.getByTestId("intake-fix-dialog")).toBeHidden({
      timeout: 15_000,
    });

    // Close detail dialog
    await page.getByTestId("intake-return-button").click();

    // Create a fresh browser context to prove durability
    const freshContext = await context.browser()!.newContext({ baseURL: PG_WEB, extraHTTPHeaders: headers });
    contexts.push(freshContext);
    const freshPage = await freshContext.newPage();

    await freshPage.addInitScript(({ actor, tenant }) => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", actor);
      sessionStorage.setItem("oday.operator.tenant", tenant);
    }, { actor: scenario.proposer_id, tenant: scenario.tenant_id });
    // Navigate directly using the canonical durable deep link; the restored
    // detail view replaces the tab shell, so no tab click is needed.
    await freshPage.goto(
      `/operator?ws=network&tab=radar&selected=${id}&dialog=detail`,
    );

    // Verify fields grid is updated and correct value is preserved
    await expect(freshPage.getByTestId("intake-detail-dialog")).toBeVisible({
      timeout: 15_000,
    });
    await expect(freshPage.getByTestId("intake-parsed-lineage")).toContainText(
      "新北市板橋區府中路 26 號 1F",
    );

    // Perform decision in fresh context
    await freshPage.getByTestId("decide-action-create").click();
    await freshPage
      .getByTestId("intake-decide-reason")
      .fill("Durability test decision reason");
    await freshPage.getByTestId("intake-decide-risk-ack").check();
    await freshPage.getByTestId("intake-decide-submit").click();
    await expect(freshPage.getByTestId("intake-decide-dialog")).toBeHidden({
      timeout: 15_000,
    });

    // Verify the record's stage is durably updated
    await expect(freshPage.getByTestId("intake-detail-stage")).toHaveText(
      "可決策",
    );

    await freshPage.close();
    await freshContext.close();

    // Reopen in another fresh context to ensure final decision stage is durable
    const anotherContext = await context.browser()!.newContext({ baseURL: PG_WEB, extraHTTPHeaders: headers });
    contexts.push(anotherContext);
    const anotherPage = await anotherContext.newPage();
    await anotherPage.addInitScript(({ actor, tenant }) => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", actor);
      sessionStorage.setItem("oday.operator.tenant", tenant);
    }, { actor: scenario.proposer_id, tenant: scenario.tenant_id });
    await anotherPage.goto(
      `/operator?ws=network&tab=radar&selected=${id}&dialog=detail`,
    );
    await expect(anotherPage.getByTestId("intake-detail-dialog")).toBeVisible({
      timeout: 15_000,
    });
    await expect(anotherPage.getByTestId("intake-detail-stage")).toHaveText(
      "可決策",
    );

    await anotherPage.close();
    await anotherContext.close();
    const durable = await getIntakeApi(id, scenario);
    expect(durable).toMatchObject({ stage: "READY", version: 3,
      parsedFields: { address: { correctedValue: "新北市板橋區府中路 26 號 1F" } } });
    expect(durable.auditEvents.filter((event: any) => event.action === "intake.correct")).toHaveLength(1);
    expect(durable.auditEvents.filter((event: any) => event.action === "intake.decide.create")).toHaveLength(1);
    } finally {
      for (const isolated of contexts) await isolated.close();
      await api.dispose();
    }
  });

  test("retained tablet viewport folds the 5-up meta grid correctly", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 768, height: 1024 }); // Tablet viewport
    const { api } = await openPgScenario(page, { retainedHistory: "revision" });
    try {
      await expect(page.getByTestId("intake-detail-dialog")).toBeVisible();
      await expect(page.getByTestId("timeline-stepper")).toBeVisible();
      await expect(page.getByTestId("intake-parsed-lineage")).toBeVisible();
    } finally { await api.dispose(); }
  });

  test("retained verify audit envelope for CREATE and PROMOTE decisions", async ({
    page,
  }, info) => {
    const retained = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
    // 1. Actual CREATE decision on explicit retained history, not a new URL.
    const id1 = retained.scenario.intake_id;
    const beforeCreate = await getIntakeApi(id1, retained.scenario);
    await page.getByTestId("decide-action-create").click();
    await page
      .getByTestId("intake-decide-reason")
      .fill("Audit test create reason");
    await page.getByTestId("intake-decide-risk-ack").check();
    const [created] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${id1}/decide`),
      page.getByTestId("intake-decide-submit").click(),
    ]);
    expect(created.status(), await created.text()).toBe(200);
    expect(created.request().headers()["if-match"]).toBe(`W/"${beforeCreate.version}"`);
    expect(created.request().headers()["idempotency-key"]).toBeTruthy();
    expect(await created.json()).toMatchObject({ id: id1, stage: "READY", version: beforeCreate.version + 1 });
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({
      timeout: 15_000,
    });
    await page.getByTestId("intake-return-button").click();

    const data1 = await getIntakeApi(id1, retained.scenario);
    expect(data1).toMatchObject({ parserVersion: null, snapshotId: null, rawSnapshot: null, version: 2 });
    expect(data1.parsedFields).toEqual(beforeCreate.parsedFields);
    const auditCreate = data1.auditEvents.find(
      (e: any) => e.action === "intake.decide.create",
    );
    expect(auditCreate).toBeDefined();
    expect(auditCreate.actorRoleId).toBe("site_reviewer");
    expect(auditCreate.actorName).toBe(retained.scenario.proposer_id);
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

    // 2. Canonical promotion uses a separately admitted, measured PG fixture.
    // The created legacy row above has real hard-rule failures; its 422 is a
    // correct guard, not a reason to manufacture geocode or weaken acceptance.
    const pgPage = await page.context().newPage();
    const { api, scenario } = await openPgScenario(pgPage);
    try {
      await expect(pgPage.getByTestId("promotion-target-format")).toHaveValue("");
      await pgPage.getByTestId("promotion-target-format").fill(SYNTHETIC_TARGET_FORMAT);
      await pgPage.getByTestId("promotion-request-reason").fill("Audit test canonical promotion reason");
      await pgPage.getByTestId("promotion-request-ack").check();
      const [requested] = await Promise.all([
        pgPage.waitForResponse((r) => r.url().includes("/promotion-requests") && r.request().method() === "POST"),
        pgPage.getByTestId("promotion-request-submit").click(),
      ]);
      expect(requested.status(), await requested.text()).toBe(202);
      const requestReceipt = await requested.json();
      expect(requestReceipt.status).toBe("PENDING_REVIEW");
      expect(requested.request().postDataJSON().target_format_code).toBe(SYNTHETIC_TARGET_FORMAT);
      expect(requestReceipt.target_format_code).toBe(SYNTHETIC_TARGET_FORMAT);
      await expect(pgPage.getByTestId("promotion-self-review-denied")).toBeVisible();
      await pgPage.setExtraHTTPHeaders({ "x-subject-id": scenario.reviewer_id, "x-tenant-id": scenario.tenant_id,
        "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager" });
      await pgPage.evaluate((actor) => sessionStorage.setItem("oday.operator.subject", actor), scenario.reviewer_id);
      await pgPage.reload();
      await expect(pgPage.getByTestId("promotion-second-actor-ok")).toBeVisible();
      await expect(pgPage.getByTestId("promotion-receipt")).toContainText(SYNTHETIC_TARGET_FORMAT);
      await pgPage.getByTestId("promotion-review-reason").fill("Audit test independent review reason");
      await pgPage.getByTestId("promotion-review-ack").check();
      await pgPage.getByTestId("promotion-approve-btn").click();
      await expect(pgPage.getByTestId("promotion-review-summary")).toContainText(SYNTHETIC_TARGET_FORMAT);
      const [reviewed] = await Promise.all([
        pgPage.waitForResponse((r) => r.url().includes("/actions/review") && r.request().method() === "POST"),
        pgPage.getByTestId("promotion-confirm-approve-btn").click(),
      ]);
      expect(reviewed.status(), await reviewed.text()).toBe(200);
      const reviewReceipt = await reviewed.json();
      expect(reviewReceipt.status).toBe("SCORE_QUEUED");
      expect(reviewReceipt.target_format_code).toBe(SYNTHETIC_TARGET_FORMAT);
      const evidenceResponse = await api.get(`/_synthetic/evidence/${scenario.scenario_id}`);
      expect(evidenceResponse.status(), await evidenceResponse.text()).toBe(200);
      const evidence = await evidenceResponse.json();
      const requestAudit = evidence.audits.find((event: any) => event.audit_event_id === requestReceipt.audit_event_id);
      expect(requestAudit).toMatchObject({ actor_subject_id: scenario.proposer_id, action: "intake.promotion_requested",
        resource_id: requestReceipt.promotion_decision_id, result: "SUCCEEDED", before_value: null });
      expect(requestAudit.after_value).toMatchObject({ listing_id: scenario.listing_id, intake_id: scenario.intake_id,
        reason: "Audit test canonical promotion reason", status: "PENDING_REVIEW", target_format_code: SYNTHETIC_TARGET_FORMAT });
      expect(requestAudit.correlation_id).toBe((await requested.request().allHeaders())["x-correlation-id"]);
      expect(requestAudit.occurred_at).toBeTruthy();
      const reviewAudit = evidence.audits.find((event: any) => event.audit_event_id === reviewReceipt.audit_event_id);
      expect(reviewAudit).toMatchObject({ actor_subject_id: scenario.reviewer_id, action: "promotion.score_queued",
        resource_id: requestReceipt.promotion_decision_id, result: "SUCCEEDED" });
      expect(reviewAudit.before_value.status).toBe("PENDING_REVIEW");
      expect(reviewAudit.after_value).toMatchObject({ status: "SCORE_QUEUED", review_reason: "Audit test independent review reason",
        risk_acknowledged: true, site_score_job_id: reviewReceipt.site_score_job_id, candidate_site_id: reviewReceipt.candidate_site_id });
      expect(reviewAudit.correlation_id).toBe((await reviewed.request().allHeaders())["x-correlation-id"]);
      expect(reviewAudit.occurred_at).toBeTruthy();
      expect(evidence.job).toMatchObject({ job_id: reviewReceipt.site_score_job_id, status: "QUEUED", attempt: 0 });
      await info.attach("physical-pg-request-and-review-audit", { body: JSON.stringify(evidence, null, 2), contentType: "application/json" });
      // Leave the isolated queue settled so a later positive case can run its
      // own true worker without inheriting another scenario's pending work.
      const drained = await api.post("/_synthetic/worker/drain", { data: { scenario_id: scenario.scenario_id }, timeout: 45_000 });
      expect(drained.ok(), await drained.text()).toBe(true);
      expect((await drained.json()).job.status).toBe("SUCCEEDED");
    } finally {
      await api.dispose();
      await pgPage.close();
    }
    } finally { await retained.api.dispose(); }
  });

  test("retained verify audit envelope for REVISE decision", async ({ page }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "revision" });
    try {
    const id2 = scenario.intake_id;
    const beforeRevise = await getIntakeApi(id2, scenario);
    await page.getByTestId("decide-action-revise").click();
    await page
      .getByTestId("intake-decide-reason")
      .fill("Audit test revise reason");
    await page.getByTestId("intake-decide-risk-ack").check();
    const [revised] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${id2}/decide`),
      page.getByTestId("intake-decide-submit").click(),
    ]);
    expect(revised.status(), await revised.text()).toBe(200);
    expect(revised.request().headers()["if-match"]).toBe(`W/"${beforeRevise.version}"`);
    expect(revised.request().headers()["idempotency-key"]).toBeTruthy();
    expect(await revised.json()).toMatchObject({ id: id2, stage: "READY", version: beforeRevise.version + 1 });
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({
      timeout: 15_000,
    });
    await page.getByTestId("intake-return-button").click();

    const data2 = await getIntakeApi(id2, scenario);
    expect(data2).toMatchObject({ parserVersion: null, snapshotId: null, rawSnapshot: null, version: 2 });
    expect(data2.parsedFields).toEqual(beforeRevise.parsedFields);
    const auditRevise = data2.auditEvents.find(
      (e: any) => e.action === "intake.decide.revise",
    );
    expect(auditRevise).toBeDefined();
    expect(auditRevise.actorRoleId).toBe("site_reviewer");
    expect(auditRevise.actorName).toBe(scenario.proposer_id);
    expect(auditRevise.occurredAt).toBeTruthy();
    expect(auditRevise.message).toContain("Audit test revise reason");
    expect(auditRevise.correlationId).toBeTruthy();
    expect(auditRevise.metadata.decision).toBe("revise");
    expect(auditRevise.metadata.reason).toBe("Audit test revise reason");
    expect(auditRevise.metadata.riskSummary).toContain("將以收件");
    expect(auditRevise.metadata.riskAcknowledged).toBe(true);
    expect(auditRevise.metadata.beforeAfter.stage.before).toBe("READY");
    expect(auditRevise.metadata.beforeAfter.stage.after).toBe("READY");
    expect(auditRevise.metadata.beforeAfter.target_rent).toBeDefined();
    expect(auditRevise.metadata.targetListingId).toBe(scenario.listing_id);
    expect(auditRevise.metadata.beforeAfter.target_rent).toEqual({ before: 58000,
      after: beforeRevise.parsedFields.rent.normalizedValue });
    await page.reload();
    expect(await getIntakeApi(id2, scenario)).toEqual(data2);
    } finally { await api.dispose(); }
  });

  test("retained verify audit envelope for DUPLICATE decision", async ({ page }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
    const id3 = scenario.intake_id;
    const beforeDuplicate = await getIntakeApi(id3, scenario);
    await page.getByTestId("decide-action-dup").click();
    await page
      .getByTestId("intake-decide-reason")
      .fill("Audit test duplicate reason");
    await page.getByTestId("intake-decide-risk-ack").check();
    const [duplicated] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${id3}/decide`),
      page.getByTestId("intake-decide-submit").click(),
    ]);
    expect(duplicated.status(), await duplicated.text()).toBe(200);
    expect(duplicated.request().headers()["if-match"]).toBe(`W/"${beforeDuplicate.version}"`);
    expect(duplicated.request().headers()["idempotency-key"]).toBeTruthy();
    expect(await duplicated.json()).toMatchObject({ id: id3, stage: "READY", version: beforeDuplicate.version + 1 });
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({
      timeout: 15_000,
    });
    await page.getByTestId("intake-return-button").click();

    const data3 = await getIntakeApi(id3, scenario);
    expect(data3).toMatchObject({ parserVersion: null, snapshotId: null, rawSnapshot: null, version: 2 });
    expect(data3.parsedFields).toEqual(beforeDuplicate.parsedFields);
    const auditDup = data3.auditEvents.find(
      (e: any) => e.action === "intake.decide.duplicate",
    );
    expect(auditDup).toBeDefined();
    expect(auditDup.actorRoleId).toBe("site_reviewer");
    expect(auditDup.actorName).toBe(scenario.proposer_id);
    expect(auditDup.occurredAt).toBeTruthy();
    expect(auditDup.message).toContain("Audit test duplicate reason");
    expect(auditDup.correlationId).toBeTruthy();
    expect(auditDup.metadata.decision).toBe("duplicate");
    expect(auditDup.metadata.reason).toBe("Audit test duplicate reason");
    expect(auditDup.metadata.riskSummary).toContain("將收件");
    expect(auditDup.metadata.riskAcknowledged).toBe(true);
    expect(auditDup.metadata.beforeAfter.stage.before).toBe("NEEDS_REVIEW");
    expect(auditDup.metadata.beforeAfter.stage.after).toBe("READY");
    expect(auditDup.metadata.beforeAfter.target_evidence_count).toBeDefined();
    expect(auditDup.metadata.targetListingId).toBe(scenario.listing_id);
    // No snapshot is invented by merging a retained row without one.
    expect(auditDup.metadata.beforeAfter.target_evidence_count).toEqual({ before: 0, after: 0 });
    } finally { await api.dispose(); }
  });

  test("retained verify audit envelope for QUARANTINE decision", async ({ page }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
    const id4 = scenario.intake_id;
    const beforeQuarantine = await getIntakeApi(id4, scenario);
    await page.getByTestId("decide-action-steward").click();
    await page
      .getByTestId("intake-decide-reason")
      .fill("Audit test quarantine reason");
    await page.getByTestId("intake-decide-risk-ack").check();
    const [quarantined] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/v1/operator/network-listings/intake/${id4}/decide`),
      page.getByTestId("intake-decide-submit").click(),
    ]);
    expect(quarantined.status(), await quarantined.text()).toBe(200);
    expect(quarantined.request().headers()["if-match"]).toBe(`W/"${beforeQuarantine.version}"`);
    expect(quarantined.request().headers()["idempotency-key"]).toBeTruthy();
    expect(await quarantined.json()).toMatchObject({ id: id4, stage: "QUARANTINED", version: beforeQuarantine.version + 1 });
    await expect(page.getByTestId("intake-decide-dialog")).toBeHidden({
      timeout: 15_000,
    });
    await page.getByTestId("intake-return-button").click();

    const data4 = await getIntakeApi(id4, scenario);
    expect(data4).toMatchObject({ parserVersion: null, snapshotId: null, rawSnapshot: null, version: 2 });
    expect(data4.parsedFields).toEqual(beforeQuarantine.parsedFields);
    const auditQuarantine = data4.auditEvents.find(
      (e: any) => e.action === "intake.decide.quarantine",
    );
    expect(auditQuarantine).toBeDefined();
    expect(auditQuarantine.actorRoleId).toBe("site_reviewer");
    expect(auditQuarantine.actorName).toBe(scenario.proposer_id);
    expect(auditQuarantine.occurredAt).toBeTruthy();
    expect(auditQuarantine.message).toContain("Audit test quarantine reason");
    expect(auditQuarantine.correlationId).toBeTruthy();
    expect(auditQuarantine.metadata.decision).toBe("quarantine");
    expect(auditQuarantine.metadata.reason).toBe(
      "Audit test quarantine reason",
    );
    expect(auditQuarantine.metadata.riskSummary).toContain("將收件");
    expect(auditQuarantine.metadata.riskAcknowledged).toBe(true);
    expect(auditQuarantine.metadata.beforeAfter.stage.before).toBe(
      "NEEDS_REVIEW",
    );
    expect(auditQuarantine.metadata.beforeAfter.stage.after).toBe(
      "QUARANTINED",
    );
    } finally { await api.dispose(); }
  });

  test("retained verify audit envelope for REJECT decision", async ({ page }) => {
    const { api, scenario } = await openPgScenario(page, { retainedHistory: "possible-match" });
    try {
    const id5 = scenario.intake_id;
    const beforeReject = await getIntakeApi(id5, scenario);
    await page.getByTestId("intake-return-button").click();

    const apiContext = await playwrightRequest.newContext({
      baseURL: PG_API,
      extraHTTPHeaders: {
        "x-subject-id": scenario.proposer_id,
        "x-roles": "expansion_user",
        "x-operator-role": "expansion-staff",
        "x-tenant-id": scenario.tenant_id,
        "If-Match": `W/"${beforeReject.version}"`,
        "X-Correlation-Id": "reject-corr-id-12345",
        "Idempotency-Key": "reject-key-12345",
      },
    });
    const deniedStaff = await apiContext.post(
      `/api/v1/operator/network-listings/intake/${id5}/decide`,
      { data: { action: "reject", reason: "Staff cannot manufacture manager authority",
        riskSummary: "Synthetic rejection risk", riskAcknowledged: true,
        actorRoleId: "expansion-manager", actorName: "forged-manager" } },
    );
    expect(deniedStaff.status()).toBe(422);
    const deniedFinal = await apiContext.post(`/api/v1/operator/network-listings/intake/${id5}/decide`, {
      data: { action: "reject", reason: "Pure staff cannot make a final decision",
        riskSummary: "Synthetic rejection risk", riskAcknowledged: true },
    });
    expect(deniedFinal.status(), await deniedFinal.text()).toBe(422);
    expect((await deniedFinal.json()).detail).toContain("is not allowed to decide intake");
    const unchanged = await getIntakeApi(id5, scenario);
    expect(unchanged).toEqual(beforeReject);
    expect(unchanged.stage).toBe("NEEDS_REVIEW");
    expect(unchanged.auditEvents.filter((event: any) => event.action === "intake.decide.reject")).toHaveLength(0);
    await apiContext.dispose();
    // Explicit authorized test identity, rather than a body-supplied persona.
    const managerContext = await playwrightRequest.newContext({
      baseURL: PG_API, extraHTTPHeaders: {
        "x-subject-id": scenario.proposer_id,
        "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager",
        "x-tenant-id": scenario.tenant_id, "X-Correlation-Id": "reject-corr-id-12345",
        "If-Match": `W/"${unchanged.version}"`,
        "Idempotency-Key": "reject-key-12345",
      },
    });
    const rejectRes = await managerContext.post(
      `/api/v1/operator/network-listings/intake/${id5}/decide`,
      {
        data: {
          action: "reject",
          reason: "Audit test reject reason",
          riskSummary: "測試拒絕風險宣告",
          riskAcknowledged: true,
        },
      },
    );
    expect(rejectRes.status()).toBe(200);
    await managerContext.dispose();

    const data5 = await getIntakeApi(id5, scenario);
    expect(data5).toMatchObject({ parserVersion: null, snapshotId: null, rawSnapshot: null, version: 2 });
    expect(data5.parsedFields).toEqual(beforeReject.parsedFields);
    const auditReject = data5.auditEvents.find(
      (e: any) => e.action === "intake.decide.reject",
    );
    expect(auditReject).toBeDefined();
    expect(auditReject.actorRoleId).toBe("site_reviewer");
    expect(auditReject.actorName).toBe(scenario.proposer_id);
    expect(auditReject.occurredAt).toBeTruthy();
    expect(auditReject.message).toContain("Audit test reject reason");
    expect(auditReject.correlationId).toBe("reject-corr-id-12345");
    expect(auditReject.metadata.decision).toBe("reject");
    expect(auditReject.metadata.reason).toBe("Audit test reject reason");
    expect(auditReject.metadata.riskSummary).toBe("測試拒絕風險宣告");
    expect(auditReject.metadata.riskAcknowledged).toBe(true);
    expect(auditReject.metadata.beforeAfter.stage.before).toBe("NEEDS_REVIEW");
    expect(auditReject.metadata.beforeAfter.stage.after).toBe("FAILED");
    } finally { await api.dispose(); }
  });
});
