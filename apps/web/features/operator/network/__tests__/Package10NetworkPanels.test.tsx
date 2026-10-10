import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CandidatePanel } from "../CandidatePanel";
import { ComparePanel } from "../ComparePanel";
import { ExpansionStepper } from "../ExpansionStepper";
import { NetworkShell } from "../NetworkShell";
import { ListingRadarPanel } from "../ListingRadarPanel";
import type { ListingRadarRow } from "../../networkFindAreasViewModel";
import type { Listing } from "../../types";

vi.mock("../intake/AssistedIntakeSection", () => ({ AssistedIntakeSection: () => null }));
import { SiteScorePanel } from "../SiteScorePanel";
import type {
  NetworkScoringCompare,
  ScoreCard,
  ScoringCandidate,
} from "../networkScoringTypes";

afterEach(cleanup);

const radarListing: Listing & { frontageMeters: number } = {
  id: "L-2024", sourceId: "591", heatZoneId: "HZ-01", address: "台北市信義區松仁路 96 號",
  status: "new", rentPerMonth: 58000, areaPing: 18, geocodeConfidence: 0.94,
  hardRuleFailures: [], frontageMeters: 0,
};
const radarRow: ListingRadarRow = {
  ...radarListing, sourceName: "591", sourceStatus: "connected", complianceNote: "licensed",
  zoneLabel: "信義", statusLabel: "新進", rentLabel: "NT$58,000", geocodeConfidenceLabel: "94%",
  isDuplicate: false, tone: "good",
};
const radarSources = [
  { id: "591", name: "591", status: "connected" as const, complianceNote: "licensed" },
  { id: "broker", name: "仲介", status: "manualOnly" as const, complianceNote: "manual" },
];

describe("Radar source-backed detail and reachable controls", () => {
  it("retires off-filter detail and writes when the source selection is empty", () => {
    render(<ListingRadarPanel activeRoleId="expansion-manager" listings={[radarListing]} rows={[radarRow]} sources={radarSources} selectedHeatZoneId="HZ-01" />);
    expect(within(screen.getByLabelText("Listing detail")).getByText("L-2024")).toBeInTheDocument();
    fireEvent.click(within(screen.getByLabelText("來源篩選")).getByRole("button", { name: "仲介 0" }));
    expect(screen.getByLabelText("Listing detail")).toHaveTextContent("此篩選下沒有物件");
    expect(screen.queryByTestId("listing-detail-primary")).toBeNull();
    expect(screen.queryByTestId("network-listing-table")).toBeNull();
  });

  it("has focusable selected-state detail controls and does not fabricate missing facts", () => {
    render(<ListingRadarPanel activeRoleId="expansion-manager" listings={[radarListing]} rows={[radarRow]} sources={radarSources} />);
    const pick = screen.getByRole("button", { name: "查看 L-2024 物件詳情" });
    pick.focus();
    expect(pick).toHaveFocus();
    fireEvent.click(pick);
    expect(pick).toHaveAttribute("aria-pressed", "true");
    const detail = screen.getByLabelText("Listing detail");
    expect(detail).toHaveTextContent("未提供檢查結果");
    expect(detail).toHaveTextContent("0m");
    expect(detail).not.toHaveTextContent("3/3 通過");
    expect(screen.getByRole("button", { name: "地圖" })).toBeDisabled();
    expect(screen.getByTestId("listing-detail-primary")).toBeDisabled();
    for (const label of ["加入 Watchlist", "聯絡仲介", "直接送 SiteScore（資料足夠）", "標記不適合／封存"]) {
      expect(screen.getByRole("button", { name: label })).toBeDisabled();
    }
  });

  it("uses identical hard-rule and pending-write gates in the row and detail", () => {
    const onConvert = vi.fn();
    const view = render(<ListingRadarPanel activeRoleId="expansion-manager" listings={[radarListing]} rows={[{ ...radarRow, hardRuleFailures: ["floor_not_ground_level"] }]} sources={radarSources} onConvert={onConvert} />);
    expect(screen.getByTestId("listing-detail-primary")).toBeDisabled();
    expect(screen.queryByTestId("convert-L-2024")).toBeNull();
    fireEvent.click(screen.getByTestId("listing-detail-primary"));
    expect(onConvert).not.toHaveBeenCalled();
    view.rerender(<ListingRadarPanel activeRoleId="expansion-manager" listings={[radarListing]} rows={[radarRow]} sources={radarSources} onConvert={onConvert} busyListingId="L-2024" />);
    expect(screen.getByTestId("listing-detail-primary")).toBeDisabled();
    expect(screen.getByTestId("convert-L-2024")).toBeDisabled();
    view.rerender(<ListingRadarPanel activeRoleId="expansion-manager" listings={[radarListing]} rows={[radarRow]} sources={radarSources} onConvert={onConvert} />);
    fireEvent.click(screen.getByTestId("listing-detail-primary"));
    expect(onConvert).toHaveBeenCalledExactlyOnceWith("L-2024");
  });

  it("retires candidate no-op navigation and terminal merge without granting new actions", () => {
    const onMerge = vi.fn();
    const view = render(<ListingRadarPanel activeRoleId="expansion-manager" listings={[{ ...radarListing, status: "candidate", candidateId: "CS-1001" }]} rows={[{ ...radarRow, status: "candidate", candidateId: "CS-1001" }]} sources={radarSources} />);
    expect(screen.getByTestId("listing-detail-primary")).toBeDisabled();
    expect(screen.getByLabelText("Listing detail")).toHaveTextContent("候選點請至候選點分頁查看");
    view.rerender(<ListingRadarPanel activeRoleId="expansion-manager" listings={[{ ...radarListing, id: "L-2029", status: "duplicate", duplicateOfId: "L-2025", mergedIntoId: "L-2025" }]} rows={[{ ...radarRow, id: "L-2029", status: "duplicate", duplicateOfId: "L-2025", isDuplicate: true }]} sources={radarSources} onMerge={onMerge} />);
    expect(screen.getByTestId("listing-detail-primary")).toBeDisabled();
    expect(screen.getByTestId("listing-detail-primary")).toHaveTextContent("已標記重複至 L-2025");
    expect(screen.queryByTestId("merge-L-2029")).toBeNull();
    expect(onMerge).not.toHaveBeenCalled();
  });
});

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
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByLabelText("Network Golden Flow")).toHaveTextContent("此流程下一步受阻：需補地址");
    fireEvent.click(screen.getByTestId("network-step-find"));
    expect(onTabChange).toHaveBeenCalledWith(0);
  });

  it("scopes the compact blocker to the active journey without enabling unavailable steps", () => {
    const steps = [
      { id: "find", label: "找區域", state: "current" as const, tabIndex: 0, summary: "區域已選定" },
      { id: "candidate", label: "候選點", state: "next" as const, tabIndex: 2, summary: "建立候選點" },
      { id: "sitescore", label: "SiteScore", state: "blocked" as const, tabIndex: 3, summary: "Blocked until candidate exists." },
    ];
    const view = render(<ExpansionStepper activeTab={3} onStepSelect={vi.fn()} steps={steps} />);
    expect(screen.getByRole("status")).toHaveTextContent("此流程受阻：須先建立此流程候選點");
    expect(screen.getByTestId("network-step-sitescore")).toBeDisabled();
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("title", "須先建立此流程候選點");
    view.rerender(<ExpansionStepper activeTab={0} onStepSelect={vi.fn()} steps={steps} />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByLabelText("Network Golden Flow")).not.toHaveTextContent("Blocked until");
    expect(screen.getByTestId("network-step-sitescore")).toBeDisabled();
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
    fireEvent.click(screen.getByTestId("sitescore-pick-CS-1003"));
    expect(screen.getByTestId("sitescore-blocked-CS-1003")).toBeVisible();
    expect(screen.getByTestId("sitescore-card-CS-1001")).not.toBeVisible();
    expect(within(screen.getByTestId("sitescore-blocked-CS-1003")).getByRole("button")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "批次評分" }));
    expect(screen.getByTestId("sitescore-batch-table")).toHaveTextContent("82");
    expect(screen.getByTestId("sitescore-batch-table")).toHaveTextContent("76");
    const batchCandidate = screen.getByRole("button", { name: /信義松仁候選點.*NT\$58,000/ });
    expect(batchCandidate).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /中壢中原候選點.*—/ })).toBeInTheDocument();
  });

  it("keeps comparison evidence and recommendation priority in one dense workspace", () => {
    render(<ComparePanel compare={compare} fallback={{ columns: [], metrics: [] }} />);

    expect(screen.getByTestId("network-compare-table")).toHaveTextContent("82 GO");
    expect(screen.getByTestId("compare-primary")).toHaveTextContent("回本期最短");
    expect(screen.getByLabelText("Candidate priority")).toHaveTextContent("#1信義松仁82");
  });

  it("binds Candidate detail to Gate notes, preserves failed rules and never invents missing facts", () => {
    const candidate: ScoringCandidate = {
      ...candidates[1],
      listingId: null,
      gate: {
        ...candidates[1].gate,
        okCount: 0,
        checks: [
          { key: "area", label: "坪數", state: "fail", note: "18坪" },
          { key: "hardRule", label: "硬規則", state: "fail", note: "用途不符" },
        ],
      },
    };
    const view = render(<CandidatePanel candidates={[candidate]} fallbackRows={[]} />);
    const detail = screen.getByLabelText("候選點詳情");
    expect(detail).toHaveTextContent("0/2");
    const values = within(detail).getByLabelText("候選點鍵值資訊");
    expect(values).toHaveTextContent("18坪（未通過）");
    expect(values).toHaveTextContent("用途不符（未通過）");
    expect(detail).toHaveTextContent("未提供候選點 audit 記錄");
    for (const fabricated of ["28 坪", "NT$58,000", "3/3 通過", "0.94", "王仲介", "吳孟哲", "650m", "L-2024"]) {
      expect(detail).not.toHaveTextContent(fabricated);
    }
    view.rerender(<CandidatePanel candidates={[{ ...candidate, gate: { ...candidate.gate, checks: [] } }]} fallbackRows={[]} />);
    expect(detail).toHaveTextContent("未提供逐項檢查記錄");
    expect(detail).not.toHaveTextContent("3/3 通過");
  });

  it("disables unavailable Candidate mutations and restores eligible API batch scoring", () => {
    const onScoreAll = vi.fn();
    render(<CandidatePanel candidates={candidates} fallbackRows={[]} onScoreAll={onScoreAll} />);
    const detail = screen.getByLabelText("候選點詳情");
    for (const name of ["移出比較", "編輯候選點", "封存候選點"]) {
      expect(within(detail).getByRole("button", { name })).toBeDisabled();
    }
    fireEvent.click(screen.getByTestId("candidate-score-all"));
    expect(onScoreAll).toHaveBeenCalledExactlyOnceWith(["CS-1001"]);
  });

  it("batch selection submits only checked eligible candidates and disables blocked/busy execution", () => {
    const onScoreAll = vi.fn();
    const ready = { ...candidates[0], id: "CS-ready", title: "可評分候選", scored: false };
    const view = render(<SiteScorePanel candidates={[...candidates, ready]} fallbackRows={[]} scorecards={scorecards} onScoreAll={onScoreAll} />);
    fireEvent.click(screen.getByRole("button", { name: "批次評分" }));
    const blocked = screen.getByRole("button", { name: /中壢中原候選點.*—/ });
    expect(blocked).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /可評分候選/ }));
    fireEvent.click(screen.getByRole("button", { name: /信義松仁候選點.*NT\$58,000/ }));
    fireEvent.click(screen.getByTestId("sitescore-batch-run"));
    expect(onScoreAll).toHaveBeenCalledExactlyOnceWith(["CS-ready"]);
    expect(screen.queryByText(/批次執行完成/)).toBeNull();
    view.rerender(<SiteScorePanel busyCandidateId="batch" candidates={[...candidates, ready]} fallbackRows={[]} scorecards={scorecards} onScoreAll={onScoreAll} />);
    expect(screen.getByTestId("sitescore-batch-run")).toBeDisabled();
    expect(screen.getByRole("button", { name: /可評分候選/ })).toBeDisabled();
  });

  it("preserves zero revenue and unavailable API risk metadata without design mock defaults", () => {
    render(<SiteScorePanel candidates={[]} fallbackRows={[]} scorecards={[{
      ...scorecards[0], revenuePath: { m1: 0, m3: 0, m6: 0, m12: 0 },
      generatedAt: "", confidence: "", subScores: {}, capex: "", rentAssumption: "",
    }]} />);
    const report = screen.getByTestId("sitescore-card-CS-1001");
    expect(within(report).getAllByText("NT$0K")).toHaveLength(4);
    expect(within(report).getByLabelText("Risk breakdown")).toHaveTextContent("未提供");
    for (const bar of within(report).getByLabelText("月營收路徑（P50）").querySelectorAll("b")) {
      expect(bar).toHaveStyle({ height: "0%" });
    }
    expect(report).not.toHaveTextContent("NT$920K");
    expect(report).not.toHaveTextContent("280m 2家");
    expect(report).not.toHaveTextContent("NT$3,800K");
  });

  it("uses only explicit source risk tones and does not render missing/unknown risks as passing", () => {
    render(<SiteScorePanel candidates={[]} fallbackRows={[]} scorecards={[{
      ...scorecards[0], subScoreTones: { rentReasonableness: "watch", cannibalization: "risk", competition: "good" },
    }]} />);
    const risk = within(screen.getByTestId("sitescore-card-CS-1001")).getByLabelText("Risk breakdown");
    const values = risk.querySelectorAll("dd");
    expect(values[0]).toHaveAttribute("data-tone", "watch");
    expect(values[1]).toHaveAttribute("data-tone", "risk");
    for (const value of [...values].slice(2)) expect(value).toHaveAttribute("data-tone", "unknown");
    expect(values[2]).toHaveTextContent("未提供");
  });

  it("does not announce durable writes for unavailable or rejected SiteScore/Compare actions", async () => {
    const onToggleCompare = vi.fn().mockResolvedValue(false);
    const view = render(<SiteScorePanel candidates={candidates} fallbackRows={[]} scorecards={scorecards} onToggleCompare={onToggleCompare} />);
    const report = screen.getByTestId("sitescore-card-CS-1001");
    for (const name of ["產生報告 preview", "送審（SiteScore Review）", "要求補資料", "標記不適合"]) {
      expect(within(report).getByRole("button", { name })).toBeDisabled();
    }
    fireEvent.click(within(report).getByRole("button", { name: "加入／移出比較" }));
    expect(onToggleCompare).toHaveBeenCalledExactlyOnceWith("CS-1001");
    await Promise.resolve();
    expect(screen.queryByRole("status")).toBeNull();
    view.unmount();
    render(<ComparePanel compare={compare} fallback={{ columns: [], metrics: [] }} />);
    expect(screen.getByRole("button", { name: /送審首選/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /保留.*為備選/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /產生比較報告/ })).toBeDisabled();
    expect(screen.queryByRole("status")).toBeNull();
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
    expect(css).toContain("grid-template-columns: minmax(0, 1fr) 300px");
    // A CSS module silently returns undefined for missing keys. The inherited
    // detail and secondary-action markup had no corresponding style rules.
    for (const panel of ["CandidatePanel", "SiteScorePanel", "ComparePanel", "ListingRadarPanel"]) {
      const source = readFileSync(resolve(process.cwd(), `features/operator/network/${panel}.tsx`), "utf8");
      for (const [, name] of source.matchAll(/styles\.([A-Za-z_][A-Za-z_0-9]*)/g)) {
        expect(css, `${panel}: missing .${name}`).toMatch(new RegExp(`\\.${name}\\b`));
      }
    }
  });
});
