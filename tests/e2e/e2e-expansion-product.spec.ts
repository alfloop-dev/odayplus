import { expect, request as playwrightRequest, test } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const CORRELATION_ID = "corr-pv005-expansion-product";
const SNAPSHOT_TIME = "2026-06-28T01:00:00Z";
const PREDICTION_TIME = "2026-06-28T02:00:00Z";

const headers = {
  "x-correlation-id": CORRELATION_ID,
  "x-subject-id": "product-e2e-test",
  "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive,model_owner,release_owner,pricing_manager,marketing_manager",
};

test.setTimeout(90_000);

test("E2E-PV-005 Expansion product flow writes API state and verifies map/list/evidence/final decision", async ({ page }, testInfo) => {
  const api = await playwrightRequest.newContext({ extraHTTPHeaders: headers });

  const freshness = await api.get(`${API_BASE_URL}/external-data/freshness`);
  expect(freshness.status()).toBe(200);
  const freshnessPayload = await freshness.json();
  expect(freshnessPayload.freshness[0]).toMatchObject({
    data_status: "FRESH",
    source_snapshot_id: "snap-expansion-20260628-0100",
    correlation_id: CORRELATION_ID,
  });

  const heatzone = await api.post(`${API_BASE_URL}/heatzones/score-jobs`, {
    data: {
      idempotency_key: "pv005-heatzone-score",
      prediction_origin_time: PREDICTION_TIME,
      features: [
        {
          h3_index: "8928308280fffff",
          h3_resolution: 9,
          poi_count: 188,
          competitor_count: 2,
          active_listing_count: 8,
          median_listing_rent: 128000,
          competitor_capacity: 0.24,
          average_confidence: 0.93,
          source_snapshot_ids: ["poi_snapshot.valid", "listing_raw_snapshot.valid"],
          existing_store_count: 1,
          admin_city: "Taipei",
          admin_district: "Da-an",
        },
      ],
    },
  });
  expect(heatzone.status()).toBe(202);
  const heatzonePayload = await heatzone.json();
  expect(heatzonePayload.audit_event_id).toBeTruthy();
  expect(heatzonePayload.scores[0].state).toBe("STILL_EXPANDABLE");

  const listingImport = await api.post(`${API_BASE_URL}/listings/import-jobs`, {
    data: {
      source_id: "pv005-product-e2e-listing-source",
      records: [
        listingRecord("PV005-LST-001", "台北市大安區復興南路二段100號1樓", 25.026, 121.543, 45000, 25.5),
        listingRecord("PV005-LST-001", "台北市大安區復興南路二段100號1樓", 25.026, 121.543, 45000, 25.5),
        listingRecord("PV005-LST-002", "台北市大安區復興南路二段200號地下1樓", 25.028, 121.545, 35000, 30, "B1"),
      ],
    },
  });
  expect(listingImport.status()).toBe(202);
  const listingPayload = await listingImport.json();
  expect(listingPayload.accepted_count).toBe(1);
  expect(listingPayload.duplicate_count).toBe(1);
  expect(listingPayload.rejected_count).toBe(1);
  expect(listingPayload.records.map((record: { status: string }) => record.status)).toEqual([
    "CANDIDATE",
    "DUPLICATE",
    "FAILED_HARD_RULE",
  ]);
  const candidateId = listingPayload.candidates[0].candidateSiteId as string;
  expect(candidateId).toBeTruthy();

  const candidateInbox = await api.get(`${API_BASE_URL}/listings/candidates`);
  expect(candidateInbox.status()).toBe(200);
  expect((await candidateInbox.json()).candidates.map((candidate: { candidateSiteId: string }) => candidate.candidateSiteId)).toContain(candidateId);

  const firstScore = await scoreCandidate(api, candidateId, "pv005-sitescore-return", {
    comparable_store_count: 0,
    average_confidence: 0.46,
    source_snapshot_ids: ["pv005-listing-import-v1"],
  });
  const firstReport = firstScore.reports[0];
  const returnedDecision = await openDecision(api, firstReport.report_id, "pv005-analyst-return");
  const returned = await api.post(`${API_BASE_URL}/sitescore/decisions/${returnedDecision.decision_id}/decision`, {
    data: {
      action: "REQUEST_REVISION",
      actor: "pv005-reviewer",
      reason: "Need comparable evidence before approval",
    },
  });
  expect(returned.status()).toBe(200);
  expect((await returned.json()).decision_status).toBe("DRAFT");

  const secondScore = await scoreCandidate(api, candidateId, "pv005-sitescore-approval", {
    comparable_store_count: 6,
    comparable_monthly_revenue_p50: 520000,
    average_confidence: 0.92,
    source_snapshot_ids: ["pv005-listing-import-v2", "pv005-comparables-v1"],
  });
  const secondReport = secondScore.reports[0];
  expect(secondReport.report_version).toBe(2);
  expect(secondScore.summaries[0].recommendation).toBe("GO");

  const reportHistory = await api.get(`${API_BASE_URL}/sitescore/reports/${candidateId}`);
  expect(reportHistory.status()).toBe(200);
  expect((await reportHistory.json()).version_count).toBe(2);

  const approvedDecision = await openDecision(api, secondReport.report_id, "pv005-analyst-approval");
  const missingReason = await api.post(`${API_BASE_URL}/sitescore/decisions/${approvedDecision.decision_id}/decision`, {
    data: {
      action: "APPROVE",
      actor: "pv005-director",
    },
  });
  expect(missingReason.status()).toBe(422);

  const approval = await api.post(`${API_BASE_URL}/sitescore/decisions/${approvedDecision.decision_id}/decision`, {
    data: {
      action: "APPROVE",
      actor: "pv005-director",
      reason: "HeatZone demand, rent, and comparables satisfy the expansion policy.",
    },
  });
  expect(approval.status()).toBe(200);
  const approvalPayload = await approval.json();
  expect(approvalPayload.decision_status).toBe("APPROVED");
  expect(approvalPayload.realization_events[0].candidate_site_id).toBe(candidateId);
  expect(approvalPayload.realization_events[0].policy_version).toBe("sitescore-decision-policy-v1");

  const realized = await api.get(`${API_BASE_URL}/sitescore/realized`);
  expect(realized.status()).toBe(200);
  expect((await realized.json()).items.map((site: { decision_id: string }) => site.decision_id)).toContain(approvedDecision.decision_id);

  const audit = await api.get(`${API_BASE_URL}/audit/events?correlation_id=${CORRELATION_ID}`);
  expect(audit.status()).toBe(200);
  const auditEvents = (await audit.json()).events as Array<{ action: string; event_type: string; metadata: Record<string, unknown> }>;
  expect(auditEvents.map((event) => event.event_type)).toEqual(expect.arrayContaining([
    "heatzone.scored.v1",
    "sitescore.scored.v1",
    "sitescore.decision.v1",
  ]));
  expect(auditEvents.map((event) => event.action)).toEqual(expect.arrayContaining([
    "run_model",
    "return",
    "approve",
  ]));

  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");
  await expect(page.getByTestId("heat-zone-map")).toHaveAttribute("data-selected-zone", "hz-1049");
  await page.getByTestId("heatzone-row-hz-0773").click();
  await expect(page).toHaveURL(/selected=hz-0773/);
  await expect(page.getByTestId("heatzone-drawer")).toContainText("低信心 guard");
  await testInfo.attach("pv005-heatzone-map-and-list-sync", {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });

  await page.goto("/w/expansion/listings?selected=lst-9001&drawer=listing");
  await expect(page.getByTestId("listing-drawer")).toContainText("CandidateSiteCard preview");
  await expect(page.getByTestId("listing-drawer")).toContainText("correlation_id");

  await page.goto("/w/expansion/candidates?selected=cs-4107&drawer=candidate");
  await expect(page.getByTestId("candidate-site-card")).toContainText("Nearby evidence");
  await expect(page.getByTestId("candidate-site-card")).toContainText("執行 SiteScore");

  await page.goto("/w/expansion/sitescore/ssr-7001");
  await expect(page.getByTestId("evidence-panel")).toContainText("Positive factors");
  await expect(page.getByTestId("approval-panel")).toContainText("never optimistic");
  await expect(page.getByText("decision_id dec-20260628-7001")).toBeVisible();
  await expect(page.getByText("correlation id")).toBeVisible();
  await testInfo.attach("pv005-sitescore-evidence-and-decision", {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });

  await api.dispose();
});

function listingRecord(
  sourceListingId: string,
  address: string,
  latitude: number,
  longitude: number,
  rentAmount: number,
  areaPing: number,
  floor = "1F",
) {
  return {
    source_listing_id: sourceListingId,
    address_raw: address,
    latitude,
    longitude,
    city: "台北市",
    district: "大安區",
    rent_amount: rentAmount,
    currency: "TWD",
    area_ping: areaPing,
    floor,
    listing_status: "active",
    confidence: 0.92,
    snapshot_id: "pv005-listing-import-v1",
  };
}

async function scoreCandidate(
  api: Awaited<ReturnType<typeof playwrightRequest.newContext>>,
  candidateId: string,
  idempotencyKey: string,
  overrides: Record<string, unknown>,
) {
  const response = await api.post(`${API_BASE_URL}/sitescore/score-jobs`, {
    headers: { "Idempotency-Key": idempotencyKey },
    data: {
      prediction_origin_time: PREDICTION_TIME,
      features: [
        {
          candidate_site_id: candidateId,
          feature_snapshot_time: SNAPSHOT_TIME,
          heat_zone_id: "hz-1049",
          heat_zone_score: 91,
          monthly_rent: 45000,
          area_ping: 25.5,
          frontage_m: 8,
          competitor_count: 2,
          own_store_count_nearby: 1,
          comparable_monthly_revenue_p50: 480000,
          buildout_capex: 2500000,
          gross_margin_ratio: 0.6,
          data_quality_score: 0.95,
          ...overrides,
        },
      ],
    },
  });
  expect(response.status()).toBe(202);
  return response.json();
}

async function openDecision(
  api: Awaited<ReturnType<typeof playwrightRequest.newContext>>,
  reportId: string,
  createdBy: string,
) {
  const response = await api.post(`${API_BASE_URL}/sitescore/decisions`, {
    data: {
      report_id: reportId,
      created_by: createdBy,
    },
  });
  expect(response.status()).toBe(201);
  const payload = await response.json();
  expect(payload.decision_status).toBe("PENDING_REVIEW");
  return payload;
}
