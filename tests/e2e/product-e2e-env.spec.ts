import { expect, request as playwrightRequest, test } from "@playwright/test";
import { createHash } from "node:crypto";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const SEED_CORRELATION_ID = "corr-product-e2e-seed-001";
const SEED_TENANT_ID = "tenant-a";
const headers = {
  "x-correlation-id": "corr-product-e2e-env-test",
  "x-subject-id": "product-e2e-test",
  "x-tenant-id": "tenant-a",
  "x-roles":
    "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive,model_owner,release_owner,pricing_manager,marketing_manager",
};

test("Product E2E environment exposes durable API, seeded evidence, and source stub state", async () => {
  const api = await playwrightRequest.newContext({ extraHTTPHeaders: headers });

  const health = await api.get(`${API_BASE_URL}/platform/health`);
  expect(health.status()).toBe(200);
  expect((await health.json()).service).toBe("oday-api");

  const cases = await api.get(`${API_BASE_URL}/avm/cases`);
  expect(cases.status()).toBe(200);
  const casePayload = await cases.json();
  expect(
    casePayload.items.map((item: { store_id: string }) => item.store_id),
  ).toContain("e2e-store-taipei-001");

  const heatzones = await api.get(`${API_BASE_URL}/heatzones`);
  expect(heatzones.status()).toBe(200);
  expect((await heatzones.json()).count).toBeGreaterThan(0);

  // Tenant-scoped audit read: only the seed's tenant-a rows are returned. The
  // legacy evidence-export writer records no tenant, so its event stays out of
  // the public scoped list; the export is proven through its retained record.
  const audit = await api.get(
    `${API_BASE_URL}/audit/events?correlation_id=${SEED_CORRELATION_ID}`,
  );
  expect(audit.status()).toBe(200);
  const auditEvents: Array<{
    event_id: string;
    event_type: string;
    correlation_id: string;
    metadata: Record<string, unknown>;
  }> = (await audit.json()).events;
  const scopedEventTypes = auditEvents.map((event) => event.event_type);
  expect(scopedEventTypes).toContain("heatzone.scored.v1");
  expect(scopedEventTypes).toContain("job.enqueue");
  expect(scopedEventTypes).not.toContain("audit.evidence_export.v1");
  for (const event of auditEvents) {
    expect(event.correlation_id).toBe(SEED_CORRELATION_ID);
    expect(event.metadata.tenant_id).toBe(SEED_TENANT_ID);
  }
  const scopedEventIds = new Set(auditEvents.map((event) => event.event_id));

  const exports = await api.get(
    `${API_BASE_URL}/audit/evidence/exports?program_id=product-e2e-subsidy`,
    { headers: { "x-subject-id": "product-e2e-seed" } },
  );
  expect(exports.status()).toBe(200);
  const exportRows = (await exports.json()).exports;
  expect(exportRows.length).toBeGreaterThan(0);
  const seededExport = exportRows.find(
    (row: { correlation_id: string }) => row.correlation_id === SEED_CORRELATION_ID,
  );
  expect(seededExport).toBeDefined();
  expect(seededExport.program_id).toBe("product-e2e-subsidy");
  expect(seededExport.requested_by).toBe("product-e2e-seed");
  expect(seededExport.purpose).toBe("product-grade-e2e-validation");
  expect(seededExport.export_scope).toBe("internal-product-e2e");
  expect(seededExport.audit_event_id).toMatch(/\S/);
  expect(seededExport.bundle_checksum).toMatch(/\S/);
  expect(seededExport.integrity.record_hash).toMatch(/\S/);
  // The retained export's own audit event is not leaked by tenant-scoped reads.
  expect(scopedEventIds.has(seededExport.audit_event_id)).toBe(false);

  const retained = await api.get(
    `${API_BASE_URL}/audit/evidence/exports/${seededExport.export_id}`,
    { headers: { "x-subject-id": "product-e2e-seed" } },
  );
  expect(retained.status()).toBe(200);
  expect(retained.headers()["cache-control"]).toBe("private, no-store");
  const retainedRecord = await retained.json();
  expect(retainedRecord.export_id).toBe(seededExport.export_id);
  expect(retainedRecord.audit_event_id).toBe(seededExport.audit_event_id);
  expect(retainedRecord.bundle_checksum).toBe(seededExport.bundle_checksum);
  expect(retainedRecord.integrity).toEqual(seededExport.integrity);
  const bundle = retainedRecord.bundle;
  expect(bundle.export_id).toBe(seededExport.export_id);
  expect(bundle.audit_event_id).toBe(seededExport.audit_event_id);
  expect(bundle.bundle_checksum).toBe(seededExport.bundle_checksum);
  expect(bundle.program_id).toBe("product-e2e-subsidy");
  expect(bundle.requested_by).toBe("product-e2e-seed");
  const seedCase = casePayload.items.find(
    (item: { store_id: string }) => item.store_id === "e2e-store-taipei-001",
  );
  expect(bundle.decision_cards).toHaveLength(1);
  const decisionCard = bundle.decision_cards[0];
  expect(decisionCard.decision_id).toBe("decision-product-e2e-001");
  expect(decisionCard.decision_type).toBe("site-approval");
  expect(decisionCard.module).toBe("expansion");
  expect(decisionCard.outcome).toBe("approved_for_e2e");
  expect(decisionCard.owner).toBe("product-e2e-seed");
  expect(decisionCard.subject_ref).toBe(seedCase.case_id);
  expect(decisionCard.evidence_refs).toContain(seedCase.case_id);
  expect(decisionCard.lifecycle_refs.approval).toBe("approval-product-e2e-001");
  // The bundle captured the seed's actual correlated events at export time;
  // its tenant-a business events are the same rows the scoped read returns.
  const bundleEvents: Array<{
    event_id: string;
    event_type: string;
    correlation_id: string;
    job_id: string | null;
  }> = bundle.audit_events;
  expect(bundle.audit_chain.event_count).toBe(bundleEvents.length);
  for (const event of bundleEvents) {
    expect(event.correlation_id).toBe(SEED_CORRELATION_ID);
  }
  for (const eventType of ["heatzone.scored.v1", "job.enqueue"]) {
    const bundled = bundleEvents.filter((event) => event.event_type === eventType);
    expect(bundled.length).toBeGreaterThan(0);
    for (const event of bundled) {
      expect(scopedEventIds.has(event.event_id)).toBe(true);
    }
  }
  // The decision card's execution ref is the seeded scheduler job whose
  // enqueue event the bundle retained.
  expect(
    bundleEvents
      .filter((event) => event.event_type === "job.enqueue")
      .map((event) => event.job_id),
  ).toContain(decisionCard.lifecycle_refs.execution);

  const download = await api.get(
    `${API_BASE_URL}/audit/evidence/exports/${seededExport.export_id}/download`,
    { headers: { "x-subject-id": "product-e2e-seed" } },
  );
  expect(download.status()).toBe(200);
  expect(download.headers()["content-type"]).toBe("application/zip");
  expect(download.headers()["cache-control"]).toBe("private, no-store");
  const archive = await download.body();
  expect(archive.subarray(0, 4).toString("hex")).toBe("504b0304");
  expect(createHash("sha256").update(archive).digest("hex")).toBe(download.headers()["x-artifact-sha256"]);
  const otherActorExports = await api.get(
    `${API_BASE_URL}/audit/evidence/exports?program_id=product-e2e-subsidy`,
  );
  expect(otherActorExports.status()).toBe(200);
  expect((await otherActorExports.json()).exports).toEqual([]);
  expect((await api.get(
    `${API_BASE_URL}/audit/evidence/exports/${seededExport.export_id}/download`,
  )).status()).toBe(404);

  await api.dispose();
});
