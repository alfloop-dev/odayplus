import { expect, request as playwrightRequest, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

export const PG_API = process.env.ODP_SCORE_BROWSER_API_URL ?? "http://127.0.0.1:38299";
export const PG_WEB = process.env.ODP_SCORE_BROWSER_WEB_URL ?? "http://127.0.0.1:38300";
export const PG_CONTROL = process.env.ODP_SCORE_BROWSER_CONTROL_KEY ?? "synthetic-browser-control-20261013-local-only";
const MANAGER_HEADERS = { "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager" };

export type PgScenario = {
  scenario_id: string; intake_id: string; listing_id: string; tenant_id: string;
  proposer_id: string; reviewer_id: string;
};

export async function openPgScenario(page: Page, options: { entry?: "intake" | "radar" } = {}) {
  const api = await playwrightRequest.newContext({ baseURL: PG_API, extraHTTPHeaders: { "x-synthetic-control": PG_CONTROL } });
  const response = await api.post("/_synthetic/scenarios");
  expect(response.status(), await response.text()).toBe(200);
  const scenario = await response.json() as PgScenario;
  await page.setExtraHTTPHeaders({ ...MANAGER_HEADERS, "x-tenant-id": scenario.tenant_id, "x-subject-id": scenario.proposer_id });
  await page.addInitScript(({ tenant, proposer }) => {
    sessionStorage.setItem("oday.operator.role", "expansion-manager");
    localStorage.setItem("oday.operator.role", "expansion-manager");
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
  await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
  return { api, scenario };
}

/** Enter the actual Radar with a measured, durable listing; no UI route stub. */
export async function openPgRadar(page: Page, scenario: PgScenario) {
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
