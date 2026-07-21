import { expect, request as playwrightRequest, test } from "@playwright/test";

/**
 * ODP-PV-010 — Web fixture-to-API data binding.
 *
 * These tests prove that the bound product workspaces render from live backend
 * responses, and that a backend state change appears in the UI WITHOUT editing
 * any fixture (`data.ts`). The API base URL mirrors the default chosen in
 * playwright.config.ts.
 */
const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

const SEED_CASE = {
  gm_ttm: 3_200_000,
  forecast_gm_next_12m: 3_400_000,
  asset_book_value: 5_000_000,
  equipment_fair_value: 1_800_000,
  lease_liability: 600_000,
  working_capital: 400_000,
  comparable_multiples: [3.1, 3.5, 4.0],
  created_by: "e2e-pv-010",
} as const;

const headers = {
  "x-correlation-id": "corr-pv010-api-bound-ui",
  "x-subject-id": "product-e2e-test",
  "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive,model_owner,release_owner,pricing_manager,marketing_manager",
};

test("API backend is reachable and healthy for the bound web app", async () => {
  const api = await playwrightRequest.newContext({ extraHTTPHeaders: headers });
  const res = await api.get(`${API_BASE_URL}/platform/health`);
  expect(res.status()).toBe(200);
  expect((await res.json()).service).toBe("oday-api");
  await api.dispose();
});

test("E2E-PV-010 AVM cases workspace reflects a backend state change without editing data.ts", async ({ page }) => {
  const storeId = `e2e-store-${Date.now()}`;
  const api = await playwrightRequest.newContext({ extraHTTPHeaders: headers });
  const created = await api.post(`${API_BASE_URL}/avm/cases`, {
    data: { ...SEED_CASE, store_id: storeId },
  });
  expect(created.status()).toBe(201);
  const caseId = (await created.json()).case_id as string;
  await api.dispose();

  await page.goto("/w/dealroom/cases");

  const live = page.getByTestId("avm-live-cases");
  await expect(live).toBeVisible();
  await expect(page.getByTestId("avm-data-source")).toHaveAttribute("data-source", "api");
  // The store/case the test just created via the API shows up in the UI.
  await expect(live).toContainText(storeId);
  await expect(live).toContainText(caseId);

  // The documented non-product fixture table still coexists as fallback if not in production mode.
  const fallbackTable = page.getByText("估值案件列表（reserve / asking 為敏感欄位，依權限遮罩）");
  if (await fallbackTable.count() > 0) {
    await expect(fallbackTable).toBeVisible();
  }
});

test("E2E-PV-010 admin audit workspace surfaces live backend audit events", async ({ page }) => {
  // Any backend write records an audit event; create one, then read it back.
  const api = await playwrightRequest.newContext({ extraHTTPHeaders: headers });
  const created = await api.post(`${API_BASE_URL}/avm/cases`, {
    data: { ...SEED_CASE, store_id: `audit-${Date.now()}` },
  });
  expect(created.status()).toBe(201);
  await api.dispose();

  await page.goto("/admin/audit");

  const liveEvents = page.getByTestId("audit-live-events");
  await expect(liveEvents).toBeVisible();
  await expect(page.getByTestId("audit-data-source")).toHaveAttribute("data-source", "api");
  await expect(liveEvents).toContainText("avm.case_created.v1");
});
