import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { OdpApiClient } from "@oday-plus/openapi-client";
import { afterEach, describe, expect, it } from "vitest";
import { buildNetworkFindAreasViewModel } from "../../networkFindAreasViewModel";
import { CandidatePanel } from "../CandidatePanel";
import { ComparePanel } from "../ComparePanel";
import { operatorCandidateToMapSite } from "../heatZoneMapAdapters";
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
