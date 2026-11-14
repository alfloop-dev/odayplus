import { expect, request as playwrightRequest, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { CanonicalIntakeReceipt, NativeIntakeScope } from "@oday-plus/openapi-client";

export type NativeBrowserInput = {
  username: string; password: string; actor_id: string; tenant_id: string; intake_id: string;
  match_case_id: string | null; api_url: string; manager_region_id: string | null; outside_intake_id: string;
  context_property_ids: string[]; append_property_id: string | null; append_receipt_probe: boolean;
  independent_reviewer: { username: string; password: string; actor_id: string } | null;
  url_manual: { original_url: string; fields: { address: string; rent: number; areaPing: number } } | null;
  fresh_urls: { presentation: string; durability: string; duplicate: string; replay: string };
  fresh_scope: NativeIntakeScope;
  policy_sources: Record<"SOURCE_BLOCKED" | "POLICY_UNKNOWN", { source_id: string; original_url: string }>;
};

export function nativeBrowserInput(): NativeBrowserInput {
  const path = process.env.ODP_NATIVE_BROWSER_SCENARIO_FILE;
  expect(path, "Original owned native SQL/password-session composition is required").toBeTruthy();
  return JSON.parse(readFileSync(path!, "utf8")) as NativeBrowserInput;
}

export async function loginNativeSession(page: Page, account: { username: string; password: string; actor_id: string }) {
  await page.goto("/login?returnTo=" + encodeURIComponent("/operator?ws=network&tab=radar"));
  await page.getByLabel("使用者帳號").fill(account.username);
  await page.getByLabel("密碼", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).toHaveURL(/\/operator\?/);
  const session = await page.request.get("/auth/session");
  expect(session.status()).toBe(200);
  expect((await session.json()).subject).toBe(account.actor_id);
}

export async function openNativeIntakeList(page: Page) {
  const listing = () => page.waitForResponse((candidate) => candidate.request().method() === "GET"
    && new URL(candidate.url()).pathname === "/api/v1/canonical-intakes");
  const pending = listing();
  await page.getByRole("button", { name: "選取收件／比對案件", exact: true }).click();
  let response = await pending;
  if (response.status() === 409) {
    // Original visible refresh, not a command retry or suppressed failure.
    await expect(page.getByTestId("canonical-intake-case-picker").getByRole("alert")).toBeVisible();
    const reloaded = listing();
    await page.getByRole("button", { name: "重新讀取", exact: true }).click();
    response = await reloaded;
  }
  expect(response.status(), await response.text()).toBe(200);
}

/** Both original specs submit the actual form, never the retired HTTP producer. */
export async function submitNativeURL(page: Page, input: NativeBrowserInput, url: string, reason: string) {
  await expect(page.getByTestId("intake-add-dialog")).toBeVisible();
  await expect(page.getByTestId("intake-submit-button")).toBeDisabled();
  await expect(page.getByRole("combobox", { name: /^正式收件範圍/ })).toHaveValue("");
  await expect(page.getByLabel("送件原因", { exact: true })).toHaveValue("");
  await page.getByTestId("intake-url-input").fill(url);
  await page.getByRole("combobox", { name: /^正式收件範圍/ })
    .selectOption({ label: `沿用收件 ${input.intake_id} 的範圍` });
  await page.getByLabel("送件原因", { exact: true }).fill(reason);
  const posts: string[] = [];
  const observe = (request: import("@playwright/test").Request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname.startsWith("/api/v1/"))
      posts.push(new URL(request.url()).pathname);
  };
  page.on("request", observe);
  try {
    const [response] = await Promise.all([
      page.waitForResponse((candidate) => candidate.request().method() === "POST"
        && new URL(candidate.url()).pathname === "/api/v1/canonical-intakes"),
      page.getByTestId("intake-submit-button").click(),
    ]);
    expect(response.status(), await response.text()).toBe(201);
    const request = response.request(), headers = await request.allHeaders();
    const body = request.postDataJSON();
    expect(body).toEqual({ intake_method: "URL", original_url: url, scope: input.fresh_scope, reason });
    expect(headers["if-actor-match"]).toBe(input.actor_id);
    expect(headers["if-tenant-match"]).toBe(input.tenant_id);
    expect(headers["if-match"]).toBeUndefined();
    expect(headers["idempotency-key"]).toBeTruthy();
    const receipt = await response.json() as CanonicalIntakeReceipt;
    expect(receipt).toMatchObject({ intake_method: "URL", processing_state: "SUBMITTED", version: 1 });
    await expect(page.getByTestId("intake-detail-id")).toHaveText(receipt.intake_id);
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("已送出");
    const selected = new URL(page.url());
    expect(selected.pathname).toBe(`/intake/${receipt.intake_id}`);
    expect(selected.searchParams.get("intakeKind")).toBe("canonical");
    expect(posts).toEqual(["/api/v1/canonical-intakes"]);
    const command = { receipt, body, key: headers["idempotency-key"] };
    // The owning Python harness independently verifies these actual receipts
    // and one complete SQL graph per accepted key, including replay absence.
    writeFileSync(join(dirname(process.env.ODP_NATIVE_BROWSER_SCENARIO_FILE!),
      `native-url-command-${receipt.intake_id}.json`), JSON.stringify(command), { mode: 0o600 });
    return command;
  } finally { page.off("request", observe); }
}

export const PG_API = process.env.ODP_SCORE_BROWSER_API_URL ?? "http://127.0.0.1:38299";
export const PG_WEB = process.env.ODP_SCORE_BROWSER_WEB_URL ?? "http://127.0.0.1:38300";
export const PG_CONTROL = process.env.ODP_SCORE_BROWSER_CONTROL_KEY ?? "synthetic-browser-control-20261013-local-only";
const MANAGER_HEADERS = { "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager" };

export type PgScenario = {
  scenario_id: string; intake_id: string; listing_id: string | null; original_url: string; tenant_id: string;
  proposer_id: string; reviewer_id: string;
};

export async function openPgScenario(page: Page, options: {
  entry?: "intake" | "radar";
  retainedHistory?: "assisted-entry-only" | "possible-match" | "retrieval-timeout" | "revision" | "auth-required" | "policy-unknown";
} = {}) {
  const api = await playwrightRequest.newContext({ baseURL: PG_API, extraHTTPHeaders: { "x-synthetic-control": PG_CONTROL } });
  const response = await api.post("/_synthetic/scenarios", options.retainedHistory
    ? { data: { retained_history: options.retainedHistory } } : undefined);
  expect(response.status(), await response.text()).toBe(200);
  const scenario = await response.json() as PgScenario;
  await page.setExtraHTTPHeaders({ ...MANAGER_HEADERS, "x-tenant-id": scenario.tenant_id, "x-subject-id": scenario.proposer_id });
  await page.addInitScript(({ tenant, proposer }) => {
    // Initialize this fresh fixture context, but do not overwrite an explicit
    // in-test role switch on reload (governance/read-only and denied roles).
    if (!sessionStorage.getItem("oday.operator.role")) sessionStorage.setItem("oday.operator.role", "expansion-manager");
    if (!localStorage.getItem("oday.operator.role")) localStorage.setItem("oday.operator.role", "expansion-manager");
    if (!sessionStorage.getItem("oday.operator.subject")) sessionStorage.setItem("oday.operator.subject", proposer);
    sessionStorage.setItem("oday.operator.tenant", tenant);
  }, { tenant: scenario.tenant_id, proposer: scenario.proposer_id });
  if (options.entry === "radar") {
    await openPgRadar(page, scenario);
    const action = page.getByTestId(`listing-navigate-${scenario.listing_id}`);
    await expect(action).toBeEnabled({ timeout: 30_000 });
    await expect(action).toHaveText("查看收件與審查");
    const writes: string[] = [];
    const observe = (request: import("@playwright/test").Request) => {
      if (request.method() !== "GET" && request.url().includes("/api/v1/")) writes.push(request.url());
    };
    page.on("request", observe);
    try {
      await action.click();
      await expect(page.getByTestId("intake-detail-id")).toHaveText(scenario.intake_id, { timeout: 30_000 });
      expect(new URL(page.url()).pathname).toBe(`/intake/${scenario.intake_id}`);
      expect(writes).toEqual([]);
    } finally {
      page.off("request", observe);
    }
  } else {
    await page.goto(`${PG_WEB}/intake/${scenario.intake_id}?ws=network&tab=radar`);
  }
  await expect(page.getByTestId("intake-detail-id")).toHaveText(scenario.intake_id, { timeout: 30_000 });
  const retainedStage = { "assisted-entry-only": "待人工補錄", "possible-match": "待人工覆核", "retrieval-timeout": "處理失敗", "revision": "可決策",
    "auth-required": "待人工補錄", "policy-unknown": "已隔離" };
  await expect(page.getByTestId("intake-detail-stage")).toHaveText(options.retainedHistory
    ? retainedStage[options.retainedHistory] : "可決策");
  return { api, scenario };
}

/** A document reload is not completion of the original async Intake read. */
export async function reloadPgIntake(page: Page, scenario: PgScenario) {
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => candidate.request().method() === "GET"
      && new URL(candidate.url()).pathname === `/api/v1/operator/network-listings/intake/${scenario.intake_id}`),
    page.reload(),
  ]);
  expect(response.status(), await response.text()).toBe(200);
  const record = await response.json();
  expect(record.id).toBe(scenario.intake_id);
  return record;
}

/** Enter the actual Radar with a measured, durable listing; no UI route stub. */
export async function openPgRadar(page: Page, scenario: PgScenario) {
  expect(scenario.listing_id, "Radar navigation requires an actually resolved listing").toBeTruthy();
  await page.goto(`${PG_WEB}/operator?ws=network&tab=radar`);
  await expect(page.getByTestId("network-panel-listings")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("listing-filter-all").click();
  await expect(page.getByTestId(`listing-row-${scenario.listing_id}`)).toBeVisible({ timeout: 30_000 });
}

/** A second durable candidate proves selection is not a first-card fallback. */
export async function createPgNavigationControl(api: import("@playwright/test").APIRequestContext) {
  const seeded = await api.post("/_synthetic/scenarios");
  expect(seeded.status(), await seeded.text()).toBe(200);
  const scenario = await seeded.json() as PgScenario;
  const proposer = { ...MANAGER_HEADERS, "x-tenant-id": scenario.tenant_id, "x-subject-id": scenario.proposer_id };
  const reviewer = { ...MANAGER_HEADERS, "x-tenant-id": scenario.tenant_id, "x-subject-id": scenario.reviewer_id };
  const correlationId = randomUUID();
  const record = await api.get(`/api/v1/intakes/${scenario.intake_id}`, { headers: proposer });
  expect(record.status(), await record.text()).toBe(200);
  const requested = await api.post(`/api/v1/intakes/${scenario.intake_id}/promotion-requests`, {
    headers: { ...proposer, "If-Match": `W/"${(await record.json()).version}"`,
      "Idempotency-Key": `synthetic-nav-request-${scenario.scenario_id}`, "X-Correlation-Id": correlationId },
    data: { target_format_code: "synthetic-navigation-control-format", reason: "Explicit synthetic navigation control; independently measured listing.",
      gate_snapshot_sha256: "a".repeat(64), risk_acknowledged: true },
  });
  expect(requested.status(), await requested.text()).toBe(202);
  const decision = await requested.json();
  const reviewed = await api.post(`/api/v1/promotion-decisions/${decision.promotion_decision_id}/actions/review`, {
    headers: { ...reviewer, "If-Match": `W/"${decision.version}"`,
      "Idempotency-Key": `synthetic-nav-review-${scenario.scenario_id}`, "X-Correlation-Id": correlationId },
    data: { decision: "APPROVE", reason: "Independent synthetic reviewer approves the second navigation control.", risk_acknowledged: true },
  });
  expect(reviewed.status(), await reviewed.text()).toBe(200);
  const receipt = await reviewed.json();
  expect(receipt.status).toBe("SCORE_QUEUED");
  const drained = await api.post("/_synthetic/worker/drain", { data: { scenario_id: scenario.scenario_id }, timeout: 45_000 });
  expect(drained.status(), await drained.text()).toBe(200);
  const evidence = await drained.json();
  expect(evidence.job.status).toBe("SUCCEEDED");
  expect(evidence.promotion.candidate_site_id).toBe(receipt.candidate_site_id);
  expect(evidence.read_model_runtime).toEqual({ scope: "SYNTHETIC_READ_ONLY_NO_PRODUCTION_MODEL", infer_calls: 0 });
  expect(evidence.inferences).toHaveLength(1);
  return { scenario, candidateId: receipt.candidate_site_id as string, evidence };
}
