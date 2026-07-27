import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CandidatePanel } from "../CandidatePanel";
import { ComparePanel } from "../ComparePanel";
import { NetworkShell } from "../NetworkShell";
import { SiteScorePanel } from "../SiteScorePanel";
import type {
  NetworkScoringCompare,
  ScoreCard,
  ScoringCandidate,
} from "../networkScoringTypes";

afterEach(cleanup);

const gateChecks = [
  { key: "address", label: "地址", state: "ok" as const, note: "已正規化" },
  { key: "geocode", label: "Geocode", state: "ok" as const, note: "0.94" },
];

const candidates: ScoringCandidate[] = [
  {
    id: "CS-1001",
    listingId: "L-2024",
    heatZoneId: "HZ-01",
    title: "信義松仁候選點",
    zoneLabel: "信義松仁 86",
    address: "信義區松仁路 9X 號 1F",
    modelVersion: "SiteScore v2.3",
    datasetSnapshotId: "FS-20260704-0600",
    stage: "scored",
    gate: {
      state: "ready",
      passed: true,
      missing: [],
      otherMissing: [],
      blockNote: "",
      checks: gateChecks,
      okCount: 2,
      totalCount: 2,
    },
    scored: true,
    score: 82,
    recommendation: "GO",
    inCompare: true,
  },
  {
    id: "CS-1003",
    listingId: "L-2026",
    heatZoneId: "HZ-05",
    title: "中壢中原候選點",
    zoneLabel: "中壢中原 69",
    address: "中壢區中北路 XX 號 1F",
    modelVersion: "SiteScore v2.3",
    datasetSnapshotId: "FS-20260704-0600",
    stage: "needdata",
    gate: {
      state: "blocked",
      passed: false,
      missing: ["地址人工確認"],
      otherMissing: [],
      blockNote: "地址信心不足",
      checks: [
        gateChecks[0],
        { key: "geocode", label: "Geocode", state: "fail", note: "0.61" },
      ],
      okCount: 1,
      totalCount: 2,
    },
    scored: false,
    score: null,
    recommendation: null,
    inCompare: false,
  },
];

const scorecards: ScoreCard[] = [
  {
    id: "CS-1001",
    title: "信義松仁候選點",
    zoneLabel: "信義松仁 86",
    heatZoneId: "HZ-01",
    score: 82,
    recommendation: "GO",
    modelVersion: "SiteScore v2.3",
    datasetSnapshotId: "FS-20260704-0600",
    generatedAt: "今日 06:10",
    confidence: "中高",
    payback: "22 個月",
    revenuePath: { m1: 182, m3: 268, m6: 342, m12: 428 },
    band: { p10: "NT$356K", p50: "NT$428K", p90: "NT$512K" },
    subScores: { rentReasonableness: "合理", cannibalization: "低" },
    capex: "NT$1.6M",
    rentAssumption: "NT$58,000",
    drivers: ["夜間人流"],
    reasons: ["住宅與商辦混合"],
    risks: ["週末停車不易"],
    conditions: [],
    conditionTitle: "",
  },
  {
    id: "CS-1002",
    title: "板橋府中候選點",
    zoneLabel: "板橋府中 78",
    heatZoneId: "HZ-02",
    score: 76,
    recommendation: "WAIT",
    modelVersion: "SiteScore v2.3",
    datasetSnapshotId: "FS-20260703-0600",
    generatedAt: "昨日 16:42",
    confidence: "中",
    payback: "27 個月",
    revenuePath: { m1: 142, m3: 221, m6: 289, m12: 372 },
    band: { p10: "NT$308K", p50: "NT$372K", p90: "NT$431K" },
    subScores: { rentReasonableness: "偏高", cannibalization: "中" },
    capex: "NT$1.8M",
    rentAssumption: "NT$52,000",
    drivers: ["捷運通勤人流"],
    reasons: ["距捷運出口 80m"],
    risks: ["站前施工"],
    conditions: ["站前施工影響需於 Q4 前複評"],
    conditionTitle: "WAIT 通過條件",
  },
];

const compare: NetworkScoringCompare = {
  columns: [
    { id: "CS-1001", title: "信義松仁", priority: "#1", recommendation: "GO", score: 82, isBest: true },
    { id: "CS-1002", title: "板橋府中", priority: "#2", recommendation: "WAIT", score: 76, isBest: false },
  ],
  metrics: [
    {
      key: "score",
      label: "SiteScore",
      values: [
        { id: "CS-1001", text: "82 GO", isBest: true },
        { id: "CS-1002", text: "76 WAIT", isBest: false },
      ],
    },
  ],
  recommendation: {
    primary: {
      id: "CS-1001",
      title: "信義松仁",
      recommendation: "GO",
      score: 82,
      text: "優先送審",
      why: ["回本期最短"],
    },
    alternate: {
      id: "CS-1002",
      title: "板橋府中",
      recommendation: "WAIT",
      score: 76,
      text: "條件式備選",
    },
    avoid: null,
    priorityList: [
      { priority: "#1", id: "CS-1001", title: "信義松仁", score: 82, recommendation: "GO" },
      { priority: "#2", id: "CS-1002", title: "板橋府中", score: 76, recommendation: "WAIT" },
    ],
  },
  empty: false,
};

describe("Package 10 Network non-intake panels", () => {
  it("renders the dense bilingual shell and preserves machine-readable step states", () => {
    const onTabChange = vi.fn();
    render(
      <NetworkShell
        activeTab={1}
        onTabChange={onTabChange}
        steps={[
          { id: "find", label: "找區域", state: "completed", tabIndex: 0, summary: "區域已選定" },
          { id: "radar", label: "物件雷達", state: "current", tabIndex: 1, entityId: "L-2024", summary: "確認物件" },
          { id: "candidate", label: "候選點", state: "blocked", tabIndex: 2, summary: "需補地址" },
        ]}
        tabs={["找區域 / Find Areas", "物件雷達 / Listing Radar", "候選點 / Candidates"]}
      >
        <div>active panel</div>
      </NetworkShell>,
    );

    expect(screen.getByTestId("network-tab-1")).toHaveTextContent("物件雷達Listing Radar");
    expect(screen.getByTestId("network-step-find")).toHaveTextContent("completed");
    expect(screen.getByTestId("network-step-candidate")).toHaveTextContent("blocked");
    expect(screen.getByRole("status")).toHaveTextContent("需補地址");
    fireEvent.click(screen.getByTestId("network-step-find"));
    expect(onTabChange).toHaveBeenCalledWith(0);
  });

  it("renders the Candidate pipeline, data gate and existing score callbacks", () => {
    const onToggleCompare = vi.fn();
    render(
      <CandidatePanel
        candidates={candidates}
        fallbackRows={[]}
        onScore={vi.fn()}
        onScoreAll={vi.fn()}
        onToggleCompare={onToggleCompare}
      />,
    );

    const board = screen.getByTestId("network-candidate-table");
    expect(within(board).getByTestId("candidate-row-CS-1001")).toHaveTextContent("SiteScore GO 82");
    expect(within(board).getByTestId("candidate-gate-block-CS-1003")).toHaveTextContent("缺資料");
    fireEvent.click(screen.getByTestId("candidate-compare-CS-1001"));
    expect(onToggleCompare).toHaveBeenCalledWith("CS-1001");
  });

  it("renders the single SiteScore report and a dense batch table from the same API model", () => {
    render(
      <SiteScorePanel
        candidates={candidates}
        fallbackRows={[]}
        modelVersion="SiteScore v2.3"
        onRescore={vi.fn()}
        scorecards={scorecards}
      />,
    );

    expect(screen.getByTestId("sitescore-card-CS-1001")).toHaveTextContent("FS-20260704-0600");
    expect(screen.getByTestId("sitescore-conditions-CS-1002")).toHaveTextContent("站前施工");
    fireEvent.click(screen.getByRole("button", { name: "批次評分" }));
    expect(screen.getByTestId("sitescore-batch-table")).toHaveTextContent("82");
    expect(screen.getByTestId("sitescore-batch-table")).toHaveTextContent("76");
  });

  it("keeps comparison evidence and recommendation priority in one dense workspace", () => {
    render(<ComparePanel compare={compare} fallback={{ columns: [], metrics: [] }} />);

    expect(screen.getByTestId("network-compare-table")).toHaveTextContent("82 GO");
    expect(screen.getByTestId("compare-primary")).toHaveTextContent("回本期最短");
    expect(screen.getByLabelText("Candidate priority")).toHaveTextContent("#1信義松仁82");
  });

  it("locks the canonical desktop, tablet and mobile layout breakpoints", () => {
    const css = readFileSync(
      resolve(process.cwd(), "features/operator/networkFindAreas.module.css"),
      "utf8",
    );

    expect(css).toContain("@media (min-width: 1160px)");
    expect(css).toContain("@media (min-width: 760px) and (max-width: 1159px)");
    expect(css).toContain("@media (max-width: 759px)");
    expect(css).toContain("grid-template-columns: 180px minmax(0, 1fr) 348px");
    expect(css).toContain("grid-template-columns: 250px minmax(0, 1fr)");
    expect(css).toContain("grid-template-columns: minmax(0, 1fr) 348px");
  });
});
