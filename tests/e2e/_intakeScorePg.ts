import { expect, request as playwrightRequest, type Page } from "@playwright/test";

export const PG_API = process.env.ODP_SCORE_BROWSER_API_URL ?? "http://127.0.0.1:38299";
export const PG_WEB = process.env.ODP_SCORE_BROWSER_WEB_URL ?? "http://127.0.0.1:38300";
export const PG_CONTROL = process.env.ODP_SCORE_BROWSER_CONTROL_KEY ?? "synthetic-browser-control-20261013-local-only";
const MANAGER_HEADERS = { "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager" };

export type PgScenario = {
  scenario_id: string; intake_id: string; listing_id: string; tenant_id: string;
  proposer_id: string; reviewer_id: string;
};

export async function openPgScenario(page: Page) {
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
  await page.goto(`${PG_WEB}/intake/${scenario.intake_id}?ws=network&tab=radar`);
  await expect(page.getByTestId("intake-detail-id")).toHaveText(scenario.intake_id, { timeout: 30_000 });
  await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
  return { api, scenario };
}
