import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace as GrantedReadNetworkWorkspace } from "../../NetworkFindAreasWorkspace";

// Explicit synthetic bootstrap tabs for these component controls. Backend
// scope/role denials and read revalidation assertions remain authoritative.
function NetworkFindAreasWorkspace(props: Parameters<typeof GrantedReadNetworkWorkspace>[0]) {
  return <GrantedReadNetworkWorkspace allowedTabIds={["areas", "radar", "candidates", "score", "compare", "review"]} {...props} />;
}

const nav = vi.hoisted(() => ({ pathname: "/operator", search: "ws=network&tab=candidates&candidate=second", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname, useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }),
}));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({
  ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div />,
}));

// Synthetic contract regressions, not live acceptance evidence.
const ids = ["first", "second"];
const candidates = ids.map((id) => ({ id, listingId: `listing-${id}`, heatZoneId: "zone", title: id,
  address: "Recorded location", status: "scored", modelVersion: null, datasetSnapshotId: null,
  score: null, recommendation: null, missingData: [] }));
const network = { source: "api", candidates, heatZones: [], listingSources: [], siteReviews: [], assistedIntakes: [],
  listings: ids.map((id) => ({ id: `listing-${id}`, address: "Recorded location", sourceId: "recorded-source", status: "new" })),
  expansionSteps: [
    { id: "sitescore", label: "SiteScore", tabIndex: 3, entityId: "first", state: "next", summary: "Requires a production model binding." },
  ] };
const scoringCandidates = candidates.map((candidate) => ({ ...candidate, zoneLabel: "Area", modelVersion: "", datasetSnapshotId: "",
  stage: "evaluated", scored: true, inCompare: false,
  gate: { state: "passed", passed: true, missing: [], otherMissing: [], checks: [], okCount: 0, totalCount: 0, blockNote: "" } }));
const scorecards = ids.map((id) => ({ id, title: id, zoneLabel: "Area", heatZoneId: "zone", score: null, recommendation: "GO",
  reportId: `report-${id}`, reportVersion: 1, sitescoreRunId: `run-${id}`, modelVersion: "recorded-model",
  datasetSnapshotId: "recorded-snapshot", generatedAt: "2026-10-13T00:00:00Z", confidence: null, payback: null,
  revenuePath: { m1: null, m3: null, m6: null, m12: null }, band: { p10: "—", p50: "—", p90: "—" },
  subScores: {}, capex: null, rentAssumption: null, drivers: [], reasons: [], risks: [], conditions: [], conditionTitle: "" }));
const scoring = { source: "canonical", candidates: scoringCandidates, scorecards, compareSet: [],
  compare: { columns: [], metrics: [], recommendation: null, empty: true } };
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function reads(scoringStatus = 200, reviewRead?: () => Response | Promise<Response>) {
  const mock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).includes("network-listings?")
    ? json(network) : String(input).includes("network-scoring") ? json(scoring, scoringStatus)
      : String(input).includes("network-reviews") && reviewRead ? reviewRead() : json({ detail: "denied" }, 403));
  vi.stubGlobal("fetch", mock);
  return mock;
}

function canonicalReviews(historicalSecond = false) {
  const rows = ids.map((id) => ({
    id: `review-${id}`, candidateId: id, candidateTitle: `Recorded ${id}`, zoneLabel: null,
    recommendation: "GO" as const, score: null, confidence: null, confidenceStatus: "unmeasured" as const,
    reportAvailability: "available" as const, reportId: historicalSecond && id === "second" ? "older-report-second" : `report-${id}`,
    reportVersion: 1, canonicalDecisionStatus: "PENDING_REVIEW" as const, risk: "", status: "pending", statusLabel: "待審核",
    requestedBy: "synthetic-author", reviewerRole: "Site Reviewer", submittedAt: "2026-10-13T00:00:00Z",
    dueAt: null, payback: null, m12P50: null, rentReasonableness: null, cannibalization: null,
    sourceListingId: null, fieldVisit: null, brokerContact: null, notes: null, modelVersion: "recorded-model",
    datasetSnapshotId: "recorded-snapshot", compareText: null, eventChips: [], history: [], decision: null,
    pending: true, decided: false,
  }));
  return { source: "canonical", reviews: rows,
    candidates: rows.map((row) => ({ id: row.candidateId, reviewId: row.id, score: null })),
    approvals: rows.map((row) => ({ id: row.id, candidateId: row.candidateId, reviewId: row.id })),
    decisions: rows.map((row) => ({ decision_id: row.id, candidate_site_id: row.candidateId, report_id: row.reportId,
      report_version: row.reportVersion, decision_status: row.canonicalDecisionStatus, recommendation: row.recommendation,
      model_version: row.modelVersion, created_by: row.requestedBy, created_at: row.submittedAt, history: [] })),
    counts: { reviews: rows.length, pending: rows.length, decided: 0 },
  };
}
beforeEach(() => {
  nav.pathname = "/operator"; nav.search = "ws=network&tab=candidates&candidate=second"; nav.push.mockReset();
  window.history.replaceState(null, "", `/operator?${nav.search}`); window.sessionStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("exact report-aware workflow context", () => {
  it("keeps an exact review link readable without treating legacy approval envelopes as a verified human workflow", async () => {
    nav.search = "ws=network&tab=review&candidate=second";
    const fetchMock = reads(200, () => json(canonicalReviews()));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("review-card-review-second");
    await waitFor(() => expect(screen.getByTestId("network-step-review")).toHaveAttribute("data-state", "blocked"));
    expect(screen.getByTestId("network-step-review")).toHaveTextContent("人工流程未核驗");
    expect(screen.getByTestId("network-step-review")).toHaveAttribute("title", expect.stringContaining("review-second"));
    expect(screen.getByTestId("review-btn-go-review-second")).toBeEnabled();
    expect(screen.queryByTestId("review-btn-go-review-first")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("preserves an older report review for explicit reading without applying it to the current report", async () => {
    nav.search = "ws=network&tab=review&candidate=second";
    reads(200, () => json(canonicalReviews(true)));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("review-card-review-second");
    expect(screen.getByTestId("network-step-review")).toHaveAttribute("data-state", "blocked");
    expect(screen.queryByTestId("review-btn-go-review-second")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("review-card-review-second"));
    expect(screen.getByText("目前報告的人審關聯尚未唯一核對；可查閱歷史項目，未套用為目前報告的決策。")).toBeInTheDocument();
    expect(screen.queryByTestId("review-btn-go-review-second")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
  });

  it("does not advance the canonical review workflow with an abandoned role's late allowed response", async () => {
    nav.search = "ws=network&tab=review&candidate=second";
    let calls = 0, finish!: (response: Response) => void;
    reads(200, () => ++calls === 1 ? new Promise<Response>((resolve) => { finish = resolve; }) : json({ detail: "new role review denied" }, 403));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(calls).toBe(1));
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    await waitFor(() => expect(calls).toBe(2));
    await act(async () => finish(json(canonicalReviews())));
    expect(screen.getByTestId("network-step-review")).not.toHaveAttribute("data-state", "current");
    expect(screen.queryByTestId("review-card-review-second")).not.toBeInTheDocument();
    expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error");
  });

  it("uses the selected second candidate's committed report even when its scalar is null", async () => {
    const fetchMock = reads(); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "completed"));
    expect(screen.getByTestId("network-step-sitescore")).toHaveTextContent("second");
    expect(screen.queryByText("下一步：執行 SiteScore")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("preserves exact candidate across the report tab and reload, then clears it when leaving the chain", async () => {
    reads(); const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-second");
    fireEvent.click(screen.getByTestId("network-tab-3"));
    const reportHref = new URL(nav.push.mock.calls[0][0], "https://example.test");
    expect(reportHref.searchParams.get("candidate")).toBe("second");
    nav.search = reportHref.searchParams.toString();
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("sitescore-pick-second")).toHaveAttribute("aria-current", "true"));
    expect(screen.getByTestId("sitescore-pick-first")).not.toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByTestId("network-tab-1"));
    expect(new URL(nav.push.mock.calls.at(-1)![0], "https://example.test").searchParams.has("candidate")).toBe(false);
  });

  it("shows neutral context with no exact selection instead of using the first report", async () => {
    nav.search = "ws=network&tab=candidates"; reads(); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    expect(await screen.findByTestId("network-workflow-context-notice")).toHaveTextContent("請選取候選點");
    expect(screen.getByTestId("network-step-sitescore")).not.toHaveAttribute("data-state", "completed");
    expect(screen.queryByText("下一步：執行 SiteScore")).not.toBeInTheDocument();
    expect(screen.getByTestId("network-tab-0")).toBeEnabled();
    expect(screen.getByTestId("network-tab-1")).toBeEnabled();
  });

  it("cold-reloads the report tab with the exact second ID and restores first ID on history navigation", async () => {
    reads(); const initial = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-second"); initial.unmount();
    nav.search = "ws=network&tab=score&candidate=second";
    const reloaded = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("sitescore-card-second")).toBeVisible());
    expect(screen.getByTestId("sitescore-rescore-second")).toBeDisabled();
    expect(screen.getByTestId("sitescore-rescore-qualification-second")).toHaveTextContent("再評分資格尚未確認");
    expect(screen.getByTestId("sitescore-card-first")).not.toBeVisible();
    nav.search = "ws=network&tab=score&candidate=first";
    reloaded.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    expect(screen.getByTestId("sitescore-card-first")).toBeVisible();
    expect(screen.getByTestId("sitescore-card-second")).not.toBeVisible();
    expect(screen.getByTestId("network-step-sitescore")).toHaveTextContent("first");
  });

  it.each([403, 500])("keeps a %s scoring read unknown, never turning a cached/global next into execution permission", async (status) => {
    reads(status); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("network-workflow-context-notice")).toHaveTextContent("評分資料尚未成功讀取"));
    expect(screen.getByTestId("network-step-sitescore")).not.toHaveAttribute("data-state", "completed");
    expect(screen.queryByText("下一步：執行 SiteScore")).not.toBeInTheDocument();
    expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error");
  });

  it("keeps conflicting report-tab URL selection unknown and does not display the first report as selected", async () => {
    nav.search = "ws=network&tab=score&candidate=first&candidate=second";
    reads(); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("sitescore-card-first");
    expect(screen.getByTestId("sitescore-card-first")).not.toBeVisible();
    expect(screen.getByTestId("sitescore-card-second")).not.toBeVisible();
    expect(screen.getByTestId("network-workflow-context-notice")).toHaveTextContent("不一致或空白");
  });

  it("keeps Find/Radar entry tabs readable without a selected candidate workflow", async () => {
    nav.search = "ws=network&tab=radar"; reads(); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("network-panel-listings");
    expect(screen.getByTestId("network-workflow-context-notice")).toHaveTextContent("請選取候選點");
    expect(screen.getByTestId("network-step-find")).toBeEnabled();
    expect(screen.getByTestId("network-step-radar")).toBeEnabled();
    expect(screen.getByTestId("network-step-sitescore")).not.toHaveAttribute("data-state", "completed");
  });

  it("lets an exact candidate URL own selection even if a stale zone filter remains on cold open", async () => {
    nav.search += "&zone=unrelated-old-zone";
    reads(); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-second");
    expect(screen.queryByTestId("candidate-zone-scope")).not.toBeInTheDocument();
    expect(screen.getByTestId("network-step-sitescore")).toHaveTextContent("second");
  });
});


describe("Find Areas keeps candidate-only workflow notices in their action context", () => {
  it("does not add a candidate-selection notice to Find Areas while keeping unknown step states", async () => {
    nav.search = "ws=network&tab=areas";
    const fetchMock = reads();
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("network-listings?"))).toBe(true));
    expect(screen.getByTestId("network-tab-0")).toHaveAttribute("aria-current", "page");
    expect(screen.queryByTestId("network-workflow-context-notice")).not.toBeInTheDocument();
    expect(screen.getByTestId("network-step-sitescore")).not.toHaveAttribute("data-state", "completed");
    expect(screen.getByTestId("network-step-candidate")).toHaveTextContent("未確認");
    expect(screen.getByTestId("network-tab-1")).toBeEnabled();
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
});
