import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { OdpApiClient } from "@oday-plus/openapi-client";
import { afterEach, describe, expect, it } from "vitest";
import { buildNetworkFindAreasViewModel } from "../../networkFindAreasViewModel";
import { CandidatePanel } from "../CandidatePanel";
import { ComparePanel } from "../ComparePanel";
import { operatorCandidateToMapSite, operatorRecordedHeatZoneToMapZone, operatorRecordedMapFreshness } from "../heatZoneMapAdapters";
import { loadNetworkFindAreasBindings } from "../networkFindAreasLoader";
import { SiteScorePanel } from "../SiteScorePanel";
import type { ScoreCard } from "../networkScoringTypes";

afterEach(cleanup);

// These synthetic API responses exercise frontend provenance; they do not
// assert production model admission or actual score delivery.
const pending = {
  id: "synthetic-pending-candidate", heatZoneId: "synthetic-zone",
  title: "等待評分候選點", address: "Synthetic address", status: "new",
  score: null, recommendation: null, modelVersion: null,
  datasetSnapshotId: "synthetic-recorded-snapshot", missingData: [],
};

function clientFor(candidate: Record<string, unknown>, reports: Record<string, unknown>[] = []): OdpApiClient {
  return {
    baseUrl: "https://example.invalid",
    listHeatzones: async () => ({ items: [] }),
    listCandidates: async () => ({ candidates: [candidate] }),
    listSiteScoreReports: async () => ({ items: reports }),
  } as unknown as OdpApiClient;
}

describe("Pending candidate score delivery", () => {
  it("preserves absent results through API adaptation, view model and panels", async () => {
    const binding = await loadNetworkFindAreasBindings(clientFor({
      ...pending, composite_score: 99, final_recommendation: "GO", model_version: "stale-model",
      latitude: 0, longitude: 0, geocodeConfidence: 0.85, siteStatus: "new", status: "CANDIDATE",
    }));
    expect(binding.candidates.state).toBe("ready");
    const candidate = binding.candidates.items[0];
    expect(candidate).toMatchObject({ score: null, recommendation: null, modelVersion: null, status: "new" });
    expect(operatorCandidateToMapSite(candidate).coordinates).toEqual([0, 0]);
    const view = buildNetworkFindAreasViewModel({ heatZones: [], listings: [], candidates: [candidate] });
    expect(view.candidatePipeline[0]).toMatchObject({
      score: null, scoreMeter: null, recommendation: null, statusLabel: "等待評分", isBestInZone: false,
    });
    expect(view.siteScoreLab[0]).toMatchObject({ band: "等待評分", decisionReady: false, gateLabel: "等待評分結果" });
    expect(operatorCandidateToMapSite(candidate).siteScore).toBe("等待評分");
    render(<CandidatePanel candidates={[]} fallbackRows={view.candidatePipeline} />);
    const card = screen.getByTestId("candidate-row-synthetic-pending-candidate");
    expect(card).not.toHaveTextContent("WAIT 0");
    expect(within(card).queryByText(/已評分/)).toBeNull();
    expect(screen.getByRole("button", { name: /^已評分/ })).toHaveTextContent("0");
    cleanup();
    render(<SiteScorePanel candidates={[]} fallbackRows={view.siteScoreLab} scorecards={[]} />);
    expect(screen.queryByText("Synthetic address")).toBeNull();
    expect(screen.queryByText(/REJECT < 60/)).toBeNull();
    expect(screen.getByTestId("sitescore-model-meta")).not.toHaveTextContent("SiteScore v2.3");
  });

  it("keeps report confidence distinct from a business score and preserves snapshot identity", async () => {
    const binding = await loadNetworkFindAreasBindings(clientFor(pending, [{
      candidateSiteId: pending.id, confidence: 0.91, confidenceStatus: "measured",
      recommendation: "GO", modelVersion: "synthetic-model-v1", featureSnapshotTime: "2026-10-09T00:00:00Z",
    }]));
    expect(binding.candidates.items[0]).toMatchObject({
      score: null, confidence: 0.91, recommendation: "GO", modelVersion: "synthetic-model-v1", status: "scored",
      datasetSnapshotId: "synthetic-recorded-snapshot",
    });
    const view = buildNetworkFindAreasViewModel({ heatZones: [], listings: [], candidates: binding.candidates.items });
    render(<CandidatePanel candidates={[]} fallbackRows={view.candidatePipeline} />);
    expect(screen.getByRole("button", { name: /^已評分\s+1$/ })).toHaveTextContent("1");
    expect(screen.getByTestId("candidate-row-synthetic-pending-candidate")).toHaveTextContent("SiteScore GO");
    expect(screen.getByTestId("candidate-row-synthetic-pending-candidate")).not.toHaveTextContent("91");
    expect(screen.getByTestId("candidate-row-synthetic-pending-candidate")).not.toHaveTextContent("null");
  });

  it("keeps an abstaining report distinct from a measured zero score", async () => {
    const binding = await loadNetworkFindAreasBindings(clientFor(pending, [{
      candidateSiteId: pending.id, confidence: 0, confidenceStatus: "unmeasured",
      recommendation: "INVESTIGATE", modelVersion: "synthetic-model-v1",
    }]));
    expect(binding.candidates.items[0]).toMatchObject({ score: null, recommendation: "INVESTIGATE" });
    const view = buildNetworkFindAreasViewModel({ heatZones: [], listings: [], candidates: binding.candidates.items });
    expect(view.siteScoreLab[0]).toMatchObject({ band: "待補調查", decisionReady: false, scoreMeter: null });
  });

  it("renders actual report intervals without inventing a confidence-derived score", () => {
    const interval = { p10: 270000, p50: 315000, p90: 360000 };
    const report: ScoreCard = {
      id: "synthetic-completed", title: "已完成報告", zoneLabel: "Area", heatZoneId: "HZ-01",
      score: null, recommendation: "GO", modelVersion: "synthetic-model-v1",
      datasetSnapshotId: "synthetic-recorded-snapshot", generatedAt: "", confidence: 0.9,
      confidenceStatus: "measured", payback: 24,
      revenuePath: { m1: interval, m3: interval, m6: interval, m12: interval },
      band: interval, subScores: {}, capex: "", rentAssumption: "", drivers: [], reasons: [],
      risks: [], conditions: [], conditionTitle: "",
    };
    render(<SiteScorePanel candidates={[]} fallbackRows={[]} scorecards={[report]} />);
    const card = screen.getByTestId("sitescore-card-synthetic-completed");
    expect(card).toHaveTextContent("已評分");
    expect(card).toHaveTextContent("信心 0.9");
    expect(card).not.toHaveTextContent("/ 100");
    expect(within(card).getByLabelText("M1 P10 NT$270,000 · P50 NT$315,000 · P90 NT$360,000"))
      .toHaveAttribute("title", "M1 P10 NT$270,000 · P50 NT$315,000 · P90 NT$360,000");
    expect(card).toHaveTextContent("P10 保守NT$270,000");
    expect(card).toHaveTextContent("P90 樂觀NT$360,000");
    expect(card).toHaveTextContent("NT$315,000");
    expect(card).toHaveTextContent("24 個月");
  });

  it("keeps completed reports comparable without inventing a rank or system recommendation", () => {
    const view = buildNetworkFindAreasViewModel({ heatZones: [], listings: [], candidates: [] });
    render(<ComparePanel fallback={view.compare} compare={{
      columns: [{ id: "synthetic-completed", title: "已完成報告", priority: null,
        recommendation: "GO", score: null, isBest: false }],
      metrics: [{ key: "score", label: "SiteScore", values: [{ id: "synthetic-completed", text: "— GO", isBest: false }] }],
      recommendation: null, empty: false,
    }} />);
    expect(screen.getByTestId("network-compare-table")).toHaveTextContent("尚無排名");
    expect(screen.getByTestId("network-compare-table")).not.toHaveTextContent("#1");
    expect(screen.queryByTestId("compare-recommendation")).toBeNull();
  });
});

// This canonical-shaped owner double supplies recorded fields; it is not an
// admission of a production model, provider, license or real business outcome.
const recordedHeatZone = {
  heat_zone_id: "heatzone:8928308280fffff", h3_index: "8928308280fffff", h3_resolution: 9,
  score: 0, priority_rank: 3, unmet_demand_score: 0, format_fit_score: 0,
  cannibalization_risk_score: 0, rent_feasibility_score: 0, listing_availability_score: 0,
  confidence: 0, state: "UNTOUCHED", model_version: "owner-model-v1", feature_version: "owner-features-v1",
  feature_snapshot_time: "2026-10-15T00:00:00Z", prediction_origin_time: "2026-10-15T00:00:00Z",
  last_scored_at: "2026-10-15T00:01:00Z", source_snapshot_ids: ["owner-source-1"],
  reasons: ["Recorded reason"], warnings: ["Recorded warning"], geometry: null,
};

function heatZoneClient(row: Record<string, unknown>): OdpApiClient {
  return { ...clientFor(pending), listHeatzones: async () => ({ items: [row] }) } as unknown as OdpApiClient;
}

describe("Canonical HeatZone map evidence delivery", () => {
  it("preserves exact native identity, zero measurements, model and source references without overriding explicit null geometry", async () => {
    const { heatZones } = await loadNetworkFindAreasBindings(heatZoneClient(recordedHeatZone));
    expect(heatZones.state).toBe("ready");
    const row = heatZones.items[0];
    expect(row).toMatchObject({ id: recordedHeatZone.heat_zone_id, rank: 3, demandGap: null, competitionIndex: null, cannibalizationRisk: null, confidence: 0, risks: ["Recorded warning"] });
    expect(row.mapEvidence).toMatchObject({ geometry: null, h3Index: recordedHeatZone.h3_index,
      h3Resolution: 9, score: 0, priorityRank: 3, unmetDemandScore: 0, formatFitScore: 0,
      modelVersion: "owner-model-v1", featureVersion: "owner-features-v1",
      lastScoredAt: recordedHeatZone.last_scored_at, sourceSnapshotIds: ["owner-source-1"] });
    expect(row.centroid).not.toEqual([0, 0]);
    const map = operatorRecordedHeatZoneToMapZone(row)!;
    expect(map).toMatchObject({ h3: recordedHeatZone.h3_index, geometry: null, score: 0,
      state: "UNTOUCHED", modelVersion: "owner-model-v1", sourceSnapshotIds: ["owner-source-1"],
      listings: null, poiCount: null, competitorCount: null, competitorCapacity: null,
      medianListingRent: null, existingStoreCount: null, dataQualityScore: null });
    expect(operatorRecordedMapFreshness([map])).toEqual({ status: "MISSING",
      updatedAt: recordedHeatZone.last_scored_at, modelVersion: recordedHeatZone.model_version,
      featureSnapshotTime: recordedHeatZone.feature_snapshot_time, sourceSnapshotId: "owner-source-1" });
  });

  it("keeps an absent native geometry unavailable rather than creating it from a valid H3", async () => {
    const raw: Record<string, unknown> = { ...recordedHeatZone };
    delete raw.geometry;
    const { heatZones } = await loadNetworkFindAreasBindings(heatZoneClient(raw));
    expect(heatZones.items[0].mapEvidence?.geometry).toBeNull();
  });

  it("preserves an actual polygon and never changes the recorded model/source/time", async () => {
    const geometry = { type: "Polygon", coordinates: [[[121.5, 25], [121.51, 25], [121.5, 25.01], [121.5, 25]]] };
    const { heatZones } = await loadNetworkFindAreasBindings(heatZoneClient({ ...recordedHeatZone, geometry }));
    expect(heatZones.items[0].mapEvidence?.geometry).toEqual(geometry);
    expect(heatZones.items[0].mapEvidence?.sourceSnapshotIds).toEqual(recordedHeatZone.source_snapshot_ids);
    expect(heatZones.items[0].mapEvidence?.modelVersion).toBe(recordedHeatZone.model_version);
    expect(heatZones.items[0].mapEvidence?.featureSnapshotTime).toBe(recordedHeatZone.feature_snapshot_time);
  });

  it.each([
    { h3_index: "not-a-real-cell" }, { h3_resolution: 8 }, { source_snapshot_ids: "owner-source-1" },
    { source_snapshot_ids: [] }, { model_version: "" }, { score: Number.NaN }, { confidence: "0.8" },
  ])("keeps malformed native evidence from becoming map authority (%j)", async (invalid) => {
    const { heatZones } = await loadNetworkFindAreasBindings(heatZoneClient({ ...recordedHeatZone, ...invalid }));
    expect(heatZones.state).toBe("ready");
    expect(heatZones.items[0].mapEvidence).toBeUndefined();
  });

  it.each([
    { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1]]] },
    { type: "Polygon", coordinates: [[[0, 0], [181, 0], [0, 1], [0, 0]]] },
    { type: "Polygon", coordinates: [[[0, 0], [1, 0], [0, 1], [1, 1]]] },
  ])("keeps invalid polygon geometry null without discarding genuine source metadata (%j)", async (geometry) => {
    const { heatZones } = await loadNetworkFindAreasBindings(heatZoneClient({ ...recordedHeatZone, geometry }));
    expect(heatZones.items[0].mapEvidence?.geometry).toBeNull();
    expect(heatZones.items[0].mapEvidence?.sourceSnapshotIds).toEqual(["owner-source-1"]);
  });
});

describe("Recorded HeatZone lenses keep unknown observations unavailable", () => {
  it("does not turn missing competition/risk/traffic/life/demand-heat into favorable native lenses", async () => {
    const { heatZones } = await loadNetworkFindAreasBindings(heatZoneClient(recordedHeatZone));
    for (const activeLens of ["competition", "traffic", "life", "demand"] as const) {
      const view = buildNetworkFindAreasViewModel({ fixturesAllowed: false, activeLens,
        heatZones: heatZones.items, listings: [], candidates: [] });
      expect(view.selectedZone).toMatchObject({ competitionIndex: null, competitionLabel: "未知",
        cannibalizationRisk: null, cannibalizationLabel: "未評估", trafficScore: null, lifeScore: null,
        demandGap: null, lensScore: null, lensPoints: null, lensTier: null, overallPoints: 0,
        mapX: null, mapY: null, mapSize: null, mapTone: null });
      expect(view.mapPoints).toEqual([]);
      const competition = view.compare.metrics.find((metric) => metric.key === "competition")!;
      expect(competition.values).toEqual([{ zoneId: recordedHeatZone.heat_zone_id, value: null,
        label: "未評估", isLeader: false }]);
    }
  });

  it("keeps true measured zero distinct from unknown without borrowing prototype scores", async () => {
    const { heatZones } = await loadNetworkFindAreasBindings(heatZoneClient(recordedHeatZone));
    for (const activeLens of ["fit", "rent", "unmet", "confidence"] as const) {
      const view = buildNetworkFindAreasViewModel({ fixturesAllowed: false, activeLens,
        heatZones: heatZones.items, listings: [], candidates: [] });
      expect(view.selectedZone).toMatchObject({ lensPoints: 0, lensScore: 0, lensLabel: "0%", confidence: 0, overallPoints: 0 });
    }
    const measured = await loadNetworkFindAreasBindings(heatZoneClient({ ...recordedHeatZone,
      competition_index: 0, demand_gap: 0 }));
    const view = buildNetworkFindAreasViewModel({ fixturesAllowed: false, activeLens: "competition",
      heatZones: measured.heatZones.items, listings: [], candidates: [] });
    expect(view.selectedZone).toMatchObject({ competitionIndex: 0, competitionLabel: "0%", lensPoints: 100, demandGap: 0 });
  });

  it("keeps an unmeasured legacy API row readable without a default rank, coordinate or low-risk assertion", async () => {
    const { heatZones } = await loadNetworkFindAreasBindings(heatZoneClient({ id: "owner-unmeasured", label: "Owner row" }));
    expect(heatZones.items[0]).toMatchObject({ rank: null, centroid: null, demandGap: null,
      competitionIndex: null, cannibalizationRisk: null, confidence: null });
    const view = buildNetworkFindAreasViewModel({ fixturesAllowed: false, heatZones: heatZones.items, listings: [], candidates: [] });
    expect(view.selectedZone).toMatchObject({ rank: null, centroidLabel: "未定位", lensPoints: null, overallPoints: null,
      rentScore: null, trafficScore: null, lifeScore: null, confidence: null });
    expect(view.selectedZone?.reasons).not.toContain("Fixture fallback generated from demand and traffic signals.");
  });
});
