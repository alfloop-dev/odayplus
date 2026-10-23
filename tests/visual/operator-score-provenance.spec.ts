import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { primeOperatorPersona } from "../e2e/_operatorPersona";

// Controlled responses verify the compiled UI boundary. This is explicit local
// synthetic evidence, not production model admission or a live delivery proof.
test("compiled Network keeps missing candidate locations and report confidence honest", async ({ page }, info) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await primeOperatorPersona(page, "expansion-manager");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const pending = {
    id: "synthetic-pending-location", listingId: "synthetic-listing", heatZoneId: "HZ-01",
    title: "尚未定位候選點", address: "Synthetic pending address", status: "new",
    score: null, recommendation: null, modelVersion: null, datasetSnapshotId: "synthetic-source",
    missingData: [], latitude: null, longitude: null, geocodeConfidence: null,
  };
  const located = { ...pending, id: "synthetic-recorded-location", title: "已有座標候選點",
    latitude: 25.033, longitude: 121.5668, geocodeConfidence: 0.91 };
  const interval = { p10: 270000, p50: 315000, p90: 360000 };
  const report = {
    id: located.id, title: located.title, zoneLabel: "Area", heatZoneId: "HZ-01",
    score: null, recommendation: "GO", modelVersion: "synthetic-model-v1",
    datasetSnapshotId: "synthetic-source", generatedAt: "2026-10-13T00:00:00Z",
    confidence: 0.9, confidenceStatus: "measured", payback: 24,
    revenuePath: { m1: interval, m3: interval, m6: interval, m12: interval }, band: interval,
    subScores: {}, capex: null, rentAssumption: null, drivers: [], reasons: [], risks: [],
    conditions: [], conditionTitle: "",
  };
  await page.route(/\/api\/v1\/operator\/network-listings(?:\?.*)?$/, async (route) => {
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    await route.fulfill({ response, json: { ...await response.json(), candidates: [pending, located] } });
  });
  await page.route("**/api/v1/operator/network-scoring", async (route) => {
    const gate = { state: "ready", passed: true, missing: [], otherMissing: [], blockNote: "", checks: [], okCount: 6, totalCount: 6 };
    await route.fulfill({ json: { source: "canonical", modelVersion: report.modelVersion,
      candidates: [pending, located].map((candidate) => ({ ...candidate, zoneLabel: "Area", gate,
        scored: candidate.id === located.id, recommendation: candidate.id === located.id ? "GO" : null,
        stage: candidate.id === located.id ? "scored" : "new", inCompare: false })),
      scorecards: [report], batchResults: [], compare: { columns: [], metrics: [], recommendation: null, empty: true },
      compareSet: [], auditEvents: [] } });
  });
  await page.goto("/operator?ws=network&tab=find");
  await expect(page.getByTestId("heat-zone-map")).toBeVisible({ timeout: 45_000 });
  await expect(page.getByTestId("map-unlocated-candidates")).toContainText("1 個候選點");
  const noticeBox = await page.getByTestId("map-unlocated-candidates").boundingBox();
  const mapBox = await page.getByTestId("heat-zone-map-canvas").boundingBox();
  expect(noticeBox!.height).toBeLessThan(60);
  expect(mapBox!.height).toBeGreaterThan(300);
  const evidence = process.env.INTAKE_URL_EVIDENCE_DIR ?? info.outputDir;
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: path.join(evidence, "network-candidate-location-1440.png"), fullPage: true, animations: "disabled" });
  await page.getByTestId("network-tab-2").click();
  await expect(page.getByTestId(`candidate-row-${pending.id}`)).toBeVisible();
  await expect(page.getByTestId(`candidate-score-value-${pending.id}`)).not.toContainText("WAIT 0");
  await expect(page.getByTestId(`candidate-row-${located.id}`)).not.toContainText("GO null");
  await page.getByTestId("network-tab-3").click();
  const card = page.getByTestId(`sitescore-card-${located.id}`);
  await expect(card).toBeVisible();
  await expect(card).toContainText("信心 0.9");
  await expect(card).toContainText("NT$315,000");
  await expect(card.getByLabel("M1 P10 NT$270,000 · P50 NT$315,000 · P90 NT$360,000"))
    .toHaveAttribute("title", "M1 P10 NT$270,000 · P50 NT$315,000 · P90 NT$360,000");
  await expect(card).toContainText("P10 保守NT$270,000");
  await expect(card).toContainText("P90 樂觀NT$360,000");
  await expect(card).not.toContainText("/ 100");
  const intervalBox = await card.getByLabel("M1 P10 NT$270,000 · P50 NT$315,000 · P90 NT$360,000").boundingBox();
  const bandBox = await card.getByText("P10 保守", { exact: true }).boundingBox();
  expect(intervalBox!.y + intervalBox!.height).toBeLessThanOrEqual(bandBox!.y);
  await page.screenshot({ path: path.join(evidence, "network-report-provenance-1440.png"), fullPage: true, animations: "disabled" });
  expect(errors).toEqual([]);
  await writeFile(path.join(evidence, "network-score-provenance.json"), JSON.stringify({
    evidence_kind: "explicit_synthetic_compiled_ui_boundary", pending, located, report,
    assertions: { unlocated_candidates: 1, no_confidence_derived_business_score: true, report_intervals_rendered: true,
      map_canvas_height: mapBox!.height, notice_height: noticeBox!.height, report_intervals_do_not_overlap_band: true },
    page_errors: errors,
  }, null, 2));
});
