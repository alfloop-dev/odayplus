import { expect, request as playwrightRequest, test } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const headers = {
  "x-correlation-id": "corr-product-e2e-env-test",
  "x-subject-id": "product-e2e-test",
  "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive,model_owner,release_owner,pricing_manager,marketing_manager",
};

test("Product E2E environment exposes durable API, seeded evidence, and source stub state", async () => {
  const api = await playwrightRequest.newContext({ extraHTTPHeaders: headers });

  const health = await api.get(`${API_BASE_URL}/platform/health`);
  expect(health.status()).toBe(200);
  expect((await health.json()).service).toBe("oday-api");

  const cases = await api.get(`${API_BASE_URL}/avm/cases`);
  expect(cases.status()).toBe(200);
  const casePayload = await cases.json();
  expect(casePayload.items.map((item: { store_id: string }) => item.store_id)).toContain("e2e-store-taipei-001");

  const heatzones = await api.get(`${API_BASE_URL}/heatzones`);
  expect(heatzones.status()).toBe(200);
  expect((await heatzones.json()).count).toBeGreaterThan(0);

  const audit = await api.get(`${API_BASE_URL}/audit/events?correlation_id=corr-product-e2e-seed-001`);
  expect(audit.status()).toBe(200);
  const auditPayload = await audit.json();
  expect(auditPayload.events.map((event: { event_type: string }) => event.event_type)).toContain("audit.evidence_export.v1");

  const exports = await api.get(`${API_BASE_URL}/audit/evidence/exports?program_id=product-e2e-subsidy`);
  expect(exports.status()).toBe(200);
  expect((await exports.json()).exports.length).toBeGreaterThan(0);

  await api.dispose();
});
