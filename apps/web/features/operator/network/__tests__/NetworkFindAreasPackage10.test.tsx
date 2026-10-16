import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiBinding } from "../../../../src/lib/api/binding";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import type { Candidate, OperatorHeatZone } from "../../types";
import * as fixtures from "../../fixtures";
import { ExpansionStepper } from "../ExpansionStepper";
import type { ReviewItem } from "../networkReviewTypes";
import { FindAreasPanel } from "../FindAreasPanel";
import { buildNetworkFindAreasViewModel } from "../../networkFindAreasViewModel";

// Package 10 Find Areas content parity (ODP-UI-NETWORK-FIND-AREAS-PARITY-001).
// The map needs WebGL, so it is stubbed; its props are captured instead.
const mapProps = vi.hoisted(() => ({ last: null as null | Record<string, unknown> }));
vi.mock("../HeatZoneMap", () => ({
  HeatZoneMap: (props: Record<string, unknown>) => {
    mapProps.last = props;
    return <div data-testid="heat-zone-map-stub" />;
  },
}));

const navigation = vi.hoisted(() => ({
  pathname: "/operator",
  push: vi.fn(),
  search: "ws=network&tab=find",
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

function unavailableBinding<T>(): ApiBinding<T> {
  return {
    error: "snapshot unavailable",
    fetchedAt: "2026-07-25T00:00:00.000Z",
    items: [],
    source: "unavailable",
    state: "error",
  };
}

function findAreasElement(
  activeRoleId: "expansion-manager" | "ops-lead" = "expansion-manager",
  zones?: { heatZones?: OperatorHeatZone[]; selectedHeatZoneId?: string },
) {
  return (
    <NetworkFindAreasWorkspace
      activeRoleId={activeRoleId}
      heatZones={zones?.heatZones}
      liveCandidates={unavailableBinding<Candidate>()}
      liveHeatZones={unavailableBinding<OperatorHeatZone>()}
      selectedHeatZoneId={zones?.selectedHeatZoneId}
    />
  );
}

function renderFindAreas(
  activeRoleId: "expansion-manager" | "ops-lead" = "expansion-manager",
  zones?: { heatZones?: OperatorHeatZone[]; selectedHeatZoneId?: string },
) {
  return render(findAreasElement(activeRoleId, zones));
}

function lastPushedParams() {
  return new URL(String(navigation.push.mock.calls.at(-1)?.[0]), "http://local").searchParams;
}

const LENS_LABELS = [
  "需求熱度",
  "Oday G2 適配",
  "競店壓力",
  "自家稀釋",
  "租金可行性",
  "住宅／學區／商圈",
  "交通／人流",
  "未滿足需求",
  "資料信心",
];

describe("Network Find Areas — Package 10 content", () => {
  beforeEach(() => {
    navigation.search = "ws=network&tab=find";
    navigation.push.mockReset();
    mapProps.last = null;
    window.history.replaceState(null, "", "/operator?ws=network&tab=find");
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<Response>(() => undefined)));
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows the one-row header with the four Chinese KPI chips", () => {
    renderFindAreas();
    const header = screen.getByTestId("network-header");
    expect(within(header).getByRole("heading", { name: "展店與店網" })).toBeInTheDocument();
    const chips = within(screen.getByTestId("network-header-stats")).getAllByRole("listitem");
    expect(chips.map((chip) => chip.textContent?.replace(/^\d+\s*/, ""))).toEqual([
      "今日新物件",
      "進行中候選",
      "待審 Review",
      "重配候選",
    ]);
    expect(header).not.toHaveTextContent(/HeatZones|avg confidence|candidates/);
  });

  it("puts a count badge on tabs with open work", () => {
    renderFindAreas();
    // Fixtures: one pending review, one rebalance store, no new listings.
    expect(screen.getByTestId("network-tab-count-5")).toHaveTextContent("1");
    expect(screen.getByTestId("network-tab-count-6")).toHaveTextContent("1");
    expect(screen.queryByTestId("network-tab-count-1")).toBeNull();
    expect(screen.queryByTestId("network-tab-count-0")).toBeNull();
  });

  it("renders nine single-line Chinese lenses and re-sorts by the chosen one", () => {
    renderFindAreas();
    const buttons = within(screen.getByTestId("find-areas-lens-list")).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual(LENS_LABELS);
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
    for (const button of buttons) expect(button.children).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "資料信心" }));
    expect(screen.getByRole("button", { name: "資料信心" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("依「資料信心」排序")).toBeInTheDocument();
    expect(mapProps.last?.caption).toBe("HeatZone Lens：資料信心");
  });

  it("passes lens scores to the map and keeps the confidence/risk overlays off", () => {
    renderFindAreas();
    const scores = mapProps.last?.lensScores as Array<{ id: string; points: number; label: string }>;
    expect(scores.map((entry) => entry.id)).toContain("HZ-01");
    expect(scores.every((entry) => Number.isInteger(entry.points))).toBe(true);
    expect(mapProps.last?.layerQuery).toBe("h3,listings,candidates,freshness");
  });

  it("renders the zone detail: big score, fact table, why/risks, next step, 1 + 5 actions", () => {
    renderFindAreas();
    const detail = screen.getByTestId("find-areas-zone-detail");
    expect(screen.getByTestId("find-areas-zone-score")).toHaveTextContent(/^\d{1,3}$/);
    const facts = within(screen.getByTestId("find-areas-zone-facts")).getAllByRole("term");
    expect(facts.map((term) => term.textContent)).toEqual([
      "需求缺口",
      "競店壓力",
      "自家稀釋",
      "租金帶",
      "租金可行性",
      "可用物件",
      "資料信心",
    ]);
    expect(within(detail).getByRole("heading", { name: "為什麼是這一區" })).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "主要風險" })).toBeInTheDocument();
    expect(within(detail).getByText("下一步")).toBeInTheDocument();

    const actions = within(screen.getByTestId("find-areas-zone-actions")).getAllByRole("button");
    // Exact Package 10 primary + URL intake + four secondary actions. Missing
    // search/quarter-plan owners are unavailable, never substituted workflows.
    expect(actions).toHaveLength(6);
    const survey = within(screen.getByTestId("find-areas-zone-actions")).getByRole("button", { name: "指派找點任務" });
    expect(survey).toBeDisabled();
    expect(survey).toHaveAttribute("title", "熱區任務服務未接通");
    expect(actions.map((action) => action.textContent)).toEqual([
      expect.stringMatching(/^查看本區物件（\d+）$/),
      "＋ 從網址新增物件（帶入本區）",
      "✓ 已追蹤（點擊移除）",
      "指派找點任務",
      "建立物件搜尋條件",
      "加入季度展店計畫",
    ]);
    for (const label of ["建立物件搜尋條件", "加入季度展店計畫"]) {
      expect(within(screen.getByTestId("find-areas-zone-actions")).getByRole("button", { name: label })).toBeDisabled();
    }
    expect(actions[0]).toHaveTextContent(/^查看本區物件（\d+）$/);
    expect(detail).not.toHaveTextContent(/Source Listings|Score Candidate|Submit Review|Pipeline/);
  });

  it("toggles tracking with the design copy", () => {
    renderFindAreas();
    const toggle = screen.getByRole("button", { name: "✓ 已追蹤（點擊移除）" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "加入追蹤" })).toHaveAttribute("aria-pressed", "false");
  });

  it("opens the canonical add-from-URL dialog on Listing Radar for roles that may submit", () => {
    renderFindAreas("expansion-manager");
    fireEvent.click(screen.getByRole("button", { name: "＋ 從網址新增物件（帶入本區）" }));
    const href = String(navigation.push.mock.calls.at(-1)?.[0]);
    const params = new URL(href, "http://local").searchParams;
    expect(params.get("tab")).toBe("radar");
    expect(params.get("dialog")).toBe("add");
  });

  it("disables add-from-URL for a role without the intake submit grant", () => {
    renderFindAreas("ops-lead");
    expect(screen.getByRole("button", { name: "＋ 從網址新增物件（帶入本區）" })).toBeDisabled();
  });

  it.each([
    [/^查看本區物件/, "radar"],
    [/^＋ 從網址新增物件/, "radar"],
  ])("routes %s to the %s tab", (name, tab) => {
    renderFindAreas();
    fireEvent.click(screen.getByRole("button", { name }));
    expect(new URL(String(navigation.push.mock.calls.at(-1)?.[0]), "http://local").searchParams.get("tab")).toBe(tab);
  });

  // Fixtures: CS-1002 sits in HZ-01, CS-1003 in HZ-02.
  it.each([
    ["HZ-01", "CS-1002", "CS-1003"],
    ["HZ-02", "CS-1003", "CS-1002"],
  ])("scopes the canonical Candidates deep link for %s to that zone's candidates", (zoneId, inZone, otherZone) => {
    navigation.search = `ws=network&tab=candidates&zone=${zoneId}`;
    renderFindAreas("expansion-manager", { selectedHeatZoneId: zoneId });
    expect(screen.getByTestId("candidate-zone-scope")).toHaveTextContent("（1）");
    const board = screen.getByTestId("network-candidate-table");
    expect(within(board).getByTestId(`candidate-row-${inZone}`)).toHaveAttribute("data-active", "true");
    expect(within(board).queryByTestId(`candidate-row-${otherZone}`)).toBeNull();
  });

  it("shows an empty scoped board for a zone without candidates", () => {
    const { HEAT_ZONE_FIXTURES } = fixtures;
    const emptyZone: OperatorHeatZone = { ...HEAT_ZONE_FIXTURES[1], id: "HZ-03", label: "中山南京東路", rank: 3 };
    navigation.search = "ws=network&tab=candidates&zone=HZ-03";
    renderFindAreas("expansion-manager", {
      heatZones: [...HEAT_ZONE_FIXTURES, emptyZone],
      selectedHeatZoneId: "HZ-03",
    });
    expect(screen.getByTestId("candidate-zone-scope")).toHaveTextContent("中山南京東路（0）");
    expect(screen.queryByTestId("network-candidate-table")).toBeNull();
    expect(screen.queryByTestId(/^candidate-row-/)).toBeNull();
    expect(screen.getByTestId("candidate-empty")).toHaveTextContent("中山南京東路 尚無候選點。");
    expect(screen.getByTestId("candidate-score-all")).toBeDisabled();
  });

  it.each([
    ["HZ-02", "CS-1003", "CS-1002"],
    ["HZ-01", "CS-1002", "CS-1003"],
  ])("restores same-tab %s on Back and unscoped rows on Forward after clear acknowledgment", (zone, own, other) => {
    const scoped = `ws=network&tab=candidates&zone=${zone}`;
    navigation.search = scoped;
    const view = renderFindAreas();
    const assertScoped = () => {
      expect(screen.getByTestId(`candidate-row-${own}`)).toBeInTheDocument();
      expect(screen.queryByTestId(`candidate-row-${other}`)).toBeNull();
    };
    assertScoped();
    fireEvent.click(screen.getByTestId("candidate-zone-scope-clear"));
    // Click-time hydration guard applies before Next acknowledges the URL.
    expect(screen.getByTestId(`candidate-row-${other}`)).toBeInTheDocument();
    const unscoped = lastPushedParams().toString();
    expect(lastPushedParams().has("zone")).toBe(false);
    navigation.search = unscoped;
    view.rerender(findAreasElement()); // URL ACK, not a workspace remount
    navigation.search = scoped;
    view.rerender(findAreasElement()); // Back, tab remains candidates
    assertScoped();
    navigation.search = unscoped;
    view.rerender(findAreasElement()); // Forward, tab remains candidates
    expect(screen.getByTestId(`candidate-row-${own}`)).toBeInTheDocument();
    expect(screen.getByTestId(`candidate-row-${other}`)).toBeInTheDocument();
    expect(screen.queryByTestId("candidate-zone-scope")).toBeNull();
  });

  it("restores a same-tab empty zone after clear acknowledgment without leaking other rows", () => {
    const emptyZone: OperatorHeatZone = { ...fixtures.HEAT_ZONE_FIXTURES[1], id: "HZ-03", label: "空區", rank: 3 };
    const zones = { heatZones: [...fixtures.HEAT_ZONE_FIXTURES, emptyZone] };
    const scoped = "ws=network&tab=candidates&zone=HZ-03";
    navigation.search = scoped;
    const view = renderFindAreas("expansion-manager", zones);
    expect(screen.getByTestId("candidate-empty")).toHaveTextContent("空區 尚無候選點。");
    fireEvent.click(screen.getByTestId("candidate-zone-scope-clear"));
    navigation.search = lastPushedParams().toString();
    view.rerender(findAreasElement("expansion-manager", zones));
    expect(screen.getByTestId("candidate-row-CS-1002")).toBeInTheDocument();
    navigation.search = scoped;
    view.rerender(findAreasElement("expansion-manager", zones));
    expect(screen.queryByTestId(/^candidate-row-/)).toBeNull();
    expect(screen.getByTestId("candidate-empty")).toHaveTextContent("空區 尚無候選點。");
    expect(screen.getByTestId("candidate-score-all")).toBeDisabled();
  });

  it("honors another zone URL on the same tab after the clear URL was acknowledged", () => {
    navigation.search = "ws=network&tab=candidates&zone=HZ-02";
    const view = renderFindAreas();
    fireEvent.click(screen.getByTestId("candidate-zone-scope-clear"));
    navigation.search = lastPushedParams().toString();
    view.rerender(findAreasElement());
    navigation.search = "ws=network&tab=candidates&zone=HZ-01";
    view.rerender(findAreasElement());
    expect(screen.getByTestId("candidate-row-CS-1002")).toBeInTheDocument();
    expect(screen.queryByTestId("candidate-row-CS-1003")).toBeNull();
  });

  it("restores the zone scope from the URL and clears it on request or tab change", () => {
    navigation.search = "ws=network&tab=candidates&zone=HZ-02";
    renderFindAreas();
    const board = screen.getByTestId("network-candidate-table");
    expect(within(board).getByTestId("candidate-row-CS-1003")).toBeInTheDocument();
    expect(within(board).queryByTestId("candidate-row-CS-1002")).toBeNull();
    expect(screen.getByTestId("candidate-zone-scope")).toHaveTextContent("本區：大安復興南路（1）");

    fireEvent.click(screen.getByTestId("candidate-zone-scope-clear"));
    expect(lastPushedParams().get("tab")).toBe("candidates");
    expect(lastPushedParams().has("zone")).toBe(false);
    expect(screen.queryByTestId("candidate-zone-scope")).toBeNull();
    expect(screen.getByTestId("candidate-row-CS-1002")).toBeInTheDocument();
    expect(screen.getByTestId("candidate-row-CS-1003")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("network-tab-3"));
    expect(lastPushedParams().get("tab")).toBe("score");
    expect(lastPushedParams().has("zone")).toBe(false);
  });

  // Fixture mode with the real ReviewPanel: the header chip, the tab badge and
  // the listed queue must come from one effective source. Only the dedicated
  // reviews read answers; every other read stays pending.
  function stubReviewsRead(body: unknown) {
    const fetchMock = vi.fn((input: RequestInfo | URL) =>
      String(input).includes("/api/v1/operator/network-reviews")
        ? Promise.resolve({
            headers: new Headers({ "Content-Type": "application/json" }),
            json: async () => body,
            ok: true,
            status: 200,
          } as unknown as Response)
        : new Promise<Response>(() => undefined),
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  function apiReview(id: string): ReviewItem {
    return {
      brokerContact: "—", cannibalization: "—", candidateId: `CS-${id}`, candidateTitle: `API ${id}`,
      compareText: "—", datasetSnapshotId: "—", decision: null, dueAt: "", eventChips: [], fieldVisit: "—",
      history: [], id, m12P50: "—", modelVersion: "SiteScore v2.3", notes: "—", payback: "—",
      recommendation: "GO", rentReasonableness: "—", requestedBy: "展店經理", reviewerRole: "審核角色",
      risk: "", score: 80, sourceListingId: "—", status: "pending", statusLabel: "待審核",
      submittedAt: "2026-10-11", zoneLabel: "信義區",
    };
  }

  function reviewHeaderChip() {
    return within(screen.getByTestId("network-header-stats")).getAllByRole("listitem")[2];
  }

  it("keeps the fixture queue, badge and header count aligned after an empty reviews response", async () => {
    const fetchMock = stubReviewsRead({ source: "api", reviews: [] });
    renderFindAreas();
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/network-reviews"))).toBe(true),
    );
    // Let the read resolve and the empty snapshot be applied.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.getByTestId("network-tab-count-5")).toHaveTextContent("1");
    expect(reviewHeaderChip()).toHaveTextContent(/^1\s*待審 Review$/);

    fireEvent.click(screen.getByTestId("network-tab-5"));
    const panel = screen.getByTestId("network-panel-review");
    const cards = within(screen.getByTestId("review-queue")).getAllByTestId(/^review-card-/);
    expect(cards.map((card) => card.getAttribute("data-testid"))).toContain("review-card-RV-701");
    expect(panel).toHaveTextContent("1 待審核");
    expect(screen.getByTestId("network-tab-count-5")).toHaveTextContent("1");
  });

  it("switches count, badge and queue together to a nonempty reviews response", async () => {
    stubReviewsRead({ source: "api", reviews: [apiReview("RV-API-1"), apiReview("RV-API-2")] });
    renderFindAreas();
    await waitFor(() => expect(screen.getByTestId("network-tab-count-5")).toHaveTextContent("2"));
    expect(reviewHeaderChip()).toHaveTextContent(/^2\s*待審 Review$/);

    fireEvent.click(screen.getByTestId("network-tab-5"));
    const queue = screen.getByTestId("review-queue");
    expect(within(queue).getAllByTestId(/^review-card-/)).toHaveLength(2);
    expect(within(queue).queryByTestId("review-card-RV-701")).toBeNull();
    expect(screen.getByTestId("network-panel-review")).toHaveTextContent("2 待審核");
  });
});

describe("Expansion stepper copy", () => {
  afterEach(() => cleanup());

  it("names the current step in Chinese and only warns when the next step is blocked", () => {
    render(
      <ExpansionStepper
        activeTab={0}
        onStepSelect={vi.fn()}
        steps={[
          { id: "find", label: "Find Area", state: "completed", tabIndex: 0, summary: "HZ-01 selected." },
          { id: "radar", label: "Listing Radar", state: "current", tabIndex: 1, summary: "Review listings." },
          { id: "candidate", label: "Candidate", state: "next", tabIndex: 2, summary: "Convert listing." },
          { id: "sitescore", label: "SiteScore", state: "blocked", tabIndex: 3, summary: "Blocked until candidate exists." },
        ]}
      />,
    );
    const stepper = screen.getByTestId("network-expansion-stepper");
    expect(stepper).toHaveTextContent("目前步驟：找區域 Find Areas");
    expect(stepper).toHaveTextContent("下一步：查看本區物件並轉為候選點");
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("Find Areas does not promote prototype map facts into production", () => {
  beforeEach(() => { mapProps.last = null; });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  function renderPanel(fixturesAllowed: boolean, heatZones = fixtures.HEAT_ZONE_FIXTURES) {
    // Owned engineering rows exercise the DTO boundary, not real model/data readiness.
    const viewModel = buildNetworkFindAreasViewModel({ fixturesAllowed, heatZones, listings: [], candidates: [] });
    render(<FindAreasPanel activeRoleId="expansion-manager" fixturesAllowed={fixturesAllowed}
      viewModel={viewModel} selectedZone={viewModel.zones[0]} effectiveLens="demand"
      isSelectedTracked={false} heatZones={heatZones} listings={[]} candidates={[]} canAddListing={false}
      onSelectZone={vi.fn()} onChangeLens={vi.fn()} onToggleTracked={vi.fn()} onSourceListings={vi.fn()}
      onAddListingFromUrl={vi.fn()} />);
    return viewModel;
  }

  it("keeps ranked production rows available without inventing geometry, model, snapshot or FRESH state", async () => {
    const viewModel = renderPanel(false);
    await waitFor(() => expect(mapProps.last?.productionMode).toBe(true));
    expect(mapProps.last?.dataSource).toBe("api");
    expect(mapProps.last?.zones).toEqual([]);
    expect(mapProps.last?.freshness).toEqual({
      status: "MISSING", updatedAt: "", modelVersion: "", featureSnapshotTime: "", sourceSnapshotId: "",
    });
    expect(screen.getByTestId("find-areas-map-geometry-unavailable")).toHaveTextContent("地圖 geometry 尚未可用；列表仍可用於審查。");
    for (const zone of viewModel.rankedZones) {
      expect(within(screen.getByTestId("find-areas-tray")).getByRole("button", {
        name: `${zone.label} ${zone.lensPoints} 分`,
      })).toBeInTheDocument();
    }
  });

  it("preserves the explicit POC map illustration and labels", async () => {
    renderPanel(true);
    await waitFor(() => expect(mapProps.last?.productionMode).toBe(false));
    expect(mapProps.last?.dataSource).toBe("fixture");
    expect((mapProps.last?.zones as Array<{ h3: string }>)[0].h3).toBe(`h3-${fixtures.HEAT_ZONE_FIXTURES[0].id}`);
    expect(mapProps.last?.freshness).toMatchObject({ status: "FRESH", modelVersion: "network-ops-local" });
    expect(screen.queryByTestId("find-areas-map-geometry-unavailable")).toBeNull();
  });

  it("passes recorded native metadata and explicit null geometry without promoting the timestamp to FRESH", async () => {
    const row: OperatorHeatZone = { ...fixtures.HEAT_ZONE_FIXTURES[0], mapEvidence: {
      h3Index: "8928308280fffff", h3Resolution: 9, geometry: null, score: 0, priorityRank: 1,
      state: "UNTOUCHED", confidence: 0, unmetDemandScore: 0, formatFitScore: 0,
      cannibalizationRisk: 0, rentFeasibility: 0, listingAvailability: 0,
      modelVersion: "recorded-owner-model", featureVersion: "recorded-owner-features",
      featureSnapshotTime: "2026-10-15T00:00:00Z", predictionOriginTime: "2026-10-15T00:00:00Z",
      lastScoredAt: "2026-10-15T00:01:00Z", sourceSnapshotIds: ["recorded-owner-source"],
    } };
    renderPanel(false, [row]);
    await waitFor(() => expect(mapProps.last?.zones).toHaveLength(1));
    expect(mapProps.last?.zones).toEqual([expect.objectContaining({
      id: row.id, h3: row.mapEvidence!.h3Index, geometry: null, score: 0,
      modelVersion: "recorded-owner-model", sourceSnapshotIds: ["recorded-owner-source"], poiCount: null,
    })]);
    expect(mapProps.last?.freshness).toMatchObject({ status: "MISSING", modelVersion: "recorded-owner-model",
      featureSnapshotTime: row.mapEvidence!.featureSnapshotTime, sourceSnapshotId: "recorded-owner-source" });
  });

  it("shows an unavailable recorded lens as plain text without a favorable score tier", async () => {
    const row: OperatorHeatZone = { ...fixtures.HEAT_ZONE_FIXTURES[0], label: "Missing row", rank: null,
      demandGap: null, competitionIndex: null, cannibalizationRisk: null, confidence: null, centroid: null };
    renderPanel(false, [row]);
    await waitFor(() => expect(mapProps.last?.productionMode).toBe(true));
    const card = screen.getByRole("button", { name: "Missing row 未評估" });
    expect(card.querySelector("[data-tier]")).toBeNull();
    expect(screen.getByTestId("find-areas-zone-score")).toHaveTextContent("未評估");
    expect(screen.getByTestId("find-areas-zone-score")).not.toHaveAttribute("data-tier");
    expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent("HeatZone 綜合 未評估");
    expect(mapProps.last?.lensScores).toEqual([]);
  });

});


it("hands the exact selected HeatZone to the assignment entry and preserves unavailable geometry instead of making a fake task", () => {
  const heatZones = fixtures.HEAT_ZONE_FIXTURES;
  const viewModel = buildNetworkFindAreasViewModel({ fixturesAllowed: false, heatZones, listings: [], candidates: [] });
  const open = vi.fn(), selected = viewModel.zones[0];
  render(<FindAreasPanel activeRoleId="expansion-manager" fixturesAllowed={false}
    viewModel={viewModel} selectedZone={selected} effectiveLens="demand" isSelectedTracked={false}
    heatZones={heatZones} listings={[]} candidates={[]} canAddListing={false}
    onSelectZone={vi.fn()} onChangeLens={vi.fn()} onToggleTracked={vi.fn()} onSourceListings={vi.fn()}
    onAddListingFromUrl={vi.fn()}
    onOpenSurvey={open} />);
  fireEvent.click(screen.getByRole("button", { name: "指派找點任務" }));
  expect(open).toHaveBeenCalledOnce(); expect(open).toHaveBeenCalledWith(selected.id);
  expect(screen.getByTestId("find-areas-map-geometry-unavailable")).toBeVisible();
});
