import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import type { ApiBinding } from "../../../../src/lib/api/binding";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import type { Candidate, OperatorHeatZone } from "../../types";

const navigation = vi.hoisted(() => ({
  pathname: "/operator",
  push: vi.fn(),
  search: "ws=network&tab=radar",
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({
    push: navigation.push,
  }),
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

const radarProps = vi.hoisted(() => ({ calls: [] as Array<Record<string, unknown>> }));

vi.mock("../ListingRadarPanel", () => ({
  ListingRadarPanel: (props: Record<string, unknown>) => {
    radarProps.calls.push(props);
    return <div data-testid="listing-radar-panel">Listing Radar</div>;
  },
}));

vi.mock("../ReviewPanel", async (importOriginal) => ({
  // The workspace counts through the real queue selector; only the panel is stubbed.
  resolveEffectiveReviews: (await importOriginal<typeof import("../ReviewPanel")>()).resolveEffectiveReviews,
  ReviewPanel: ({ canDecide, reviews, onDecide }: ComponentProps<typeof import("../ReviewPanel").ReviewPanel>) => (
    <div data-testid="review-authority" data-can-decide={String(canDecide)}>
      {canDecide && reviews[0] ? (
        <button onClick={() => onDecide?.(reviews[0].id, "GO", {
          reason: "Reviewed", conditions: "", requiredData: "", overrideAck: false,
        })} type="button">Submit test review decision</button>
      ) : null}
    </div>
  ),
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

const unavailableCandidates = unavailableBinding<Candidate>();
const unavailableHeatZones = unavailableBinding<OperatorHeatZone>();

// Actual legacy Review producer row; preserve the local mock IDs/status/count assertions.
const legacyReviewPrerequisites = {
  "id": "RV-702",
  "candidateId": "CS-1001",
  "candidateTitle": "信義松仁候選點",
  "zoneLabel": "信義松仁 86",
  "recommendation": "GO",
  "score": 82,
  "risk": "回本 22 個月 · 週末停車不易",
  "requestedBy": "王若寧（拓展）",
  "reviewerRole": "選址審核 / Site Reviewer",
  "submittedAt": "2026-07-13 09:20",
  "dueAt": "2026-07-16 18:00",
  "payback": "22 個月",
  "m12P50": "NT$428K",
  "rentReasonableness": "合理（區間 P45）",
  "cannibalization": "4%（低）",
  "sourceListingId": "L-2024",
  "fieldVisit": "已完成（現勘照片 6 張）",
  "brokerContact": "已聯絡（王仲介）",
  "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
  "modelVersion": "SiteScore v2.3",
  "datasetSnapshotId": "FS-20260704-0600",
  "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
  "candidateStatus": "pendingreview",
  "eventChips": [
    "SiteScore v2.3",
    "Dataset FS-20260704-0600",
    "現勘完成",
    "比較 P1"
  ],
  "history": [
    {
      "t": "2026-07-04 06:10",
      "v": "SiteScore 完成 GO 82"
    },
    {
      "t": "2026-07-13 09:20",
      "v": "王若寧送審（拓展）"
    }
  ],
  "status": "pending",
  "statusLabel": "待審核",
  "decision": null,
  "candidateStatusLabel": "待審核",
  "candidateMissingData": [],
  "pending": true,
  "decided": false,
  "overrideAvailable": true
};

// The canonical envelope (shared/api/errors.py) a guarded read answers with.
function forbiddenResponse(correlationId: string, resource: string) {
  const message = `role does not permit view on ${resource}`;
  return new Response(
    JSON.stringify({
      detail: message,
      error: {
        code: "forbidden",
        message,
        next_action: "Request access for this role or tenant; do not retry as-is.",
        occurred_at: "2026-10-10T12:17:00.000Z",
        details: [],
        correlation_id: correlationId,
      },
    }),
    { status: 403, headers: { "Content-Type": "application/json", "X-Correlation-Id": correlationId } },
  );
}

/** Every Network read refused with 403, as the deployed dev answered on 2026-10-10. */
function stubDeniedNetworkReads() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/network-listings")) return forbiddenResponse("corr-listings-403", "operator_network");
    if (path.includes("/network-scoring")) return forbiddenResponse("corr-scoring-403", "sitescore");
    if (path.includes("/network-reviews")) return forbiddenResponse("corr-reviews-403", "sitescore");
    if (path.includes("/merge-split/proposals")) return forbiddenResponse("corr-proposals-403", "heatzone");
    if (path.includes("/network-rebalance")) {
      return new Response(JSON.stringify({ source: "api", stores: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("NetworkFindAreasWorkspace route and gate behavior", () => {
  beforeEach(() => {
    navigation.pathname = "/operator";
    navigation.search = "ws=network&tab=radar";
    navigation.push.mockReset();
    radarProps.calls.length = 0;
    window.history.replaceState(null, "", "/operator?ws=network&tab=radar");
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise<Response>(() => undefined)),
    );
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("uses the active verified persona for every Network read without false fixture labels", async () => {
    const view = render(
      <NetworkFindAreasWorkspace activeRoleId="platform-admin" selectedHeatZoneId="live-zone"
        liveCandidates={unavailableCandidates} liveHeatZones={unavailableHeatZones} />,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    const calls = vi.mocked(fetch).mock.calls;
    expect(calls.map(([url]) => String(url))).toEqual(expect.arrayContaining([
      expect.stringContaining("network-listings?"), "/api/v1/operator/network-scoring",
      "/api/v1/operator/network-rebalance", "/api/v1/operator/network-reviews",
    ]));
    for (const [, init] of calls) {
      expect(init?.headers).toMatchObject({ "X-Operator-Role": "platform-admin" });
      expect(init?.headers).not.toHaveProperty("X-Roles");
    }
    expect(screen.queryByLabelText("Data source: fixtures")).toBeNull();
    expect(screen.queryByText("fixture data")).toBeNull();
    vi.mocked(fetch).mockClear();
    view.rerender(
      <NetworkFindAreasWorkspace activeRoleId="pm-audit" selectedHeatZoneId="live-zone"
        liveCandidates={unavailableCandidates} liveHeatZones={unavailableHeatZones} />,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    for (const [, init] of vi.mocked(fetch).mock.calls) {
      expect(init?.headers).toMatchObject({ "X-Operator-Role": "pm-audit" });
    }
  });

  it.each([
    ["expansion-manager", "true"],
    ["ops-lead", "false"],
    ["pm-audit", "false"],
    ["platform-admin", "false"],
  ] as const)("presents review authority for %s without impersonation", (roleId, canDecide) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    navigation.search = "ws=network&tab=review";
    render(<NetworkFindAreasWorkspace activeRoleId={roleId} initialTabId="review" />);
    expect(screen.getByTestId("review-authority")).toHaveAttribute("data-can-decide", canDecide);
  });

  it.each([
    ["overview", "network-panel-find-areas", "corr-listings-403"],
    ["candidates", "network-panel-candidates", "corr-scoring-403"],
    ["sitescore", "network-panel-sitescore", "corr-scoring-403"],
    ["review", "review-authority", "corr-reviews-403"],
    ["merge-split", "heatzone-merge-split-panel", "corr-proposals-403"],
  ])("presents a received 403 on the %s tab as an authenticated denial", async (tab, panelTestId, correlationId) => {
    stubDeniedNetworkReads();
    navigation.search = `ws=network&tab=${tab}`;
    render(
      <NetworkFindAreasWorkspace activeRoleId="platform-admin"
        liveCandidates={unavailableCandidates} liveHeatZones={unavailableHeatZones} />,
    );

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-failure-kind", "forbidden"));
    expect(gate).toHaveAttribute("data-status", "error");
    expect(gate).toHaveTextContent("沒有權限");
    expect(gate).not.toHaveTextContent("連線失敗");
    expect(gate).not.toHaveTextContent("無法連到營運資料服務");
    expect(screen.getByTestId("operator-data-unavailable-correlation")).toHaveTextContent(correlationId);
    expect(gate).toHaveTextContent("403");
    // The refused collection is never rendered as an empty authorized panel.
    expect(screen.queryByTestId(panelTestId)).toBeNull();
  });

  it("hands a refused listings read to Listing Radar instead of an empty listing collection", async () => {
    stubDeniedNetworkReads();
    render(
      <NetworkFindAreasWorkspace activeRoleId="platform-admin"
        liveCandidates={unavailableCandidates} liveHeatZones={unavailableHeatZones} />,
    );

    await waitFor(() =>
      expect(radarProps.calls.at(-1)?.listingsReadFailure).toMatchObject({
        correlationId: "corr-listings-403",
        httpStatus: 403,
        kind: "forbidden",
      }),
    );
    expect(radarProps.calls.at(-1)?.listings).toEqual([]);
  });

  it("does not manufacture zero counts for collections that were refused", async () => {
    const fetchMock = stubDeniedNetworkReads();
    navigation.search = "ws=network&tab=overview";
    render(
      <NetworkFindAreasWorkspace activeRoleId="platform-admin"
        liveCandidates={unavailableCandidates} liveHeatZones={unavailableHeatZones} />,
    );

    const state = screen.getByLabelText("展店與店網摘要");
    await waitFor(() => expect(state).toHaveTextContent("今日新物件（未授權）"));
    for (const label of ["今日新物件", "進行中候選", "待審 Review"]) {
      expect(state).toHaveTextContent(`— ${label}（未授權）`);
      expect(state).not.toHaveTextContent(`0 ${label}`);
    }
    // Rebalance really answered 200 with no stores: that zero is authoritative.
    await waitFor(() => expect(state).toHaveTextContent("0 重配候選"));
    // Presentation only: nothing was written and no grant was requested.
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.method ?? "GET").toBe("GET");
    }
    expect(fetchMock.mock.calls.every(([url]) => !/\/(users|roles)\b/.test(String(url)))).toBe(true);
  });

  it.each(["empty", "denied"])("uses the %s dedicated Review reload's source for count and gate", async (result) => {
    navigation.search = "ws=network&tab=review";
    let decided = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      const respond = (body: unknown) => new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/decide")) {
        decided = true;
        return respond({});
      }
      if (url.endsWith("/network-reviews")) {
        if (decided && result === "denied") return forbiddenResponse("corr-reviews-reload-403", "sitescore");
        return respond({ source: "api", reviews: decided ? [] : [{ ...legacyReviewPrerequisites, id: "REV-LIVE-1", status: "pending" }] });
      }
      if (url.includes("/network-listings")) return respond({
        source: "api", heatZones: [], listingSources: [], candidates: [],
        listings: [{ id: "L-LIVE-1", heatZoneId: "HZ-LIVE-1", status: "new", hardRuleFailures: [] }],
        siteReviews: ["REV-LIVE-1", "REV-LIVE-2"].map((id) => ({
          id, candidateId: "CS-LIVE-1", status: "pending", requestedByRoleId: "expansionManager",
          reviewerRoleIds: ["opsLead"], requestedAt: "2026-10-11T12:00:00Z", reasonRequired: true,
        })),
        expansionSteps: [], assistedIntakes: [],
      });
      if (url.endsWith("/network-scoring")) return respond({ source: "api", candidates: [] });
      if (url.endsWith("/network-rebalance")) return respond({ source: "api", stores: [] });
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager"
      liveCandidates={unavailableCandidates} liveHeatZones={unavailableHeatZones} />);

    const state = screen.getByLabelText("展店與店網摘要");
    await waitFor(() => expect(state).toHaveTextContent("1 今日新物件"));
    await waitFor(() => expect(state).toHaveTextContent("1 待審 Review"));
    expect(screen.getByTestId("network-tab-count-5")).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: "Submit test review decision" }));
    await waitFor(() => expect(state).toHaveTextContent(`${result === "empty" ? 0 : 2} 待審 Review`));
    const gate = screen.getByTestId("operator-data-unavailable");
    if (result === "empty") {
      expect(screen.queryByTestId("network-tab-count-5")).toBeNull();
      expect(gate).toHaveAttribute("data-status", "empty");
      expect(gate).not.toHaveAttribute("data-failure-kind");
    } else {
      expect(screen.getByTestId("network-tab-count-5")).toHaveTextContent("2");
      expect(gate).toHaveAttribute("data-failure-kind", "forbidden");
      expect(screen.getByTestId("operator-data-unavailable-correlation")).toHaveTextContent("corr-reviews-reload-403");
    }
    expect(within(state).getByText("待審 Review", { exact: false })).toHaveAttribute("data-count-state", "known");
    expect(screen.queryByTestId("review-authority")).toBeNull();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("cold-opens Radar even when every unrelated Network snapshot is unavailable", async () => {
    render(
      <NetworkFindAreasWorkspace
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );

    expect(screen.getByTestId("listing-radar-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });

    expect(screen.getByTestId("listing-radar-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });

  it("uses the server-provided Radar tab on the durable intake route", () => {
    navigation.pathname = "/intake/IN-3001";
    navigation.search = "";

    render(
      <NetworkFindAreasWorkspace
        initialTabId="radar"
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );

    expect(screen.getByTestId("listing-radar-panel")).toBeInTheDocument();
    expect(radarProps.calls.at(-1)).toMatchObject({ intakeDetailOpen: true });
  });

  // ADD-006 §3.1: the canonical detail is the first workspace surface under the
  // global Operator topbar. Nothing from the Network workspace chrome — heading,
  // KPI strip, expansion stepper, tab strip — may precede it, and the compliance
  // strip is suppressed by the panel itself. Source cards and the Listing Radar
  // stay below, inside the same single production graph.
  it.each([
    ["durable intake route", "/intake/IN-3001", ""],
    ["operator detail query context", "/operator", "ws=network&tab=radar&selected=IN-3001&dialog=detail"],
    ["operator field-fix context", "/operator", "ws=network&tab=radar&selected=IN-3001&dialog=fix&field=address_raw"],
    ["operator decision context", "/operator", "ws=network&tab=radar&selected=IN-3001&dialog=decide&decision=dup"],
    ["operator assignment/SLA context", "/operator", "ws=network&tab=radar&selected=IN-3001&dialog=assignmentSla"],
  ])("renders the intake detail as the first workspace surface (%s)", (_label, pathname, search) => {
    navigation.pathname = pathname;
    navigation.search = search;

    render(
      <NetworkFindAreasWorkspace
        initialTabId="radar"
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );

    const workspace = screen.getByTestId("network-find-areas-workspace");
    expect(workspace).toHaveAttribute("data-intake-detail-open", "true");
    expect(workspace.firstElementChild).toBe(screen.getByTestId("listing-radar-panel"));
    expect(radarProps.calls.at(-1)).toMatchObject({ intakeDetailOpen: true });

    expect(screen.queryByRole("heading", { name: "展店與店網" })).toBeNull();
    expect(screen.queryByLabelText("Network Find Areas state")).toBeNull();
    expect(screen.queryByLabelText("Network tabs")).toBeNull();
    expect(screen.queryByTestId("network-tab-1")).toBeNull();
    expect(screen.queryByTestId("operator-data-unavailable")).toBeNull();
  });

  it("keeps the Network workspace chrome in the ordinary inbox context", () => {
    navigation.search = "ws=network&tab=radar&selected=IN-3001";

    render(
      <NetworkFindAreasWorkspace
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );

    const workspace = screen.getByTestId("network-find-areas-workspace");
    expect(workspace).not.toHaveAttribute("data-intake-detail-open");
    expect(screen.getByRole("heading", { name: "展店與店網" })).toBeInTheDocument();
    expect(screen.getByLabelText("Network tabs")).toBeInTheDocument();
    expect(radarProps.calls.at(-1)).toMatchObject({ intakeDetailOpen: false });
  });

  it("writes a history entry without dropping unrelated query parameters", () => {
    navigation.search =
      "ws=network&tab=radar&tenant=tw&selected=IN-3011&flag=a&flag=b";
    window.history.replaceState(
      null,
      "",
      `/operator?${navigation.search}#intake/IN-3011`,
    );

    render(
      <NetworkFindAreasWorkspace
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );
    fireEvent.click(screen.getByTestId("network-tab-5"));

    expect(navigation.push).toHaveBeenCalledWith(
      "/operator?ws=network&tab=review&tenant=tw&selected=IN-3011&flag=a&flag=b#intake/IN-3011",
      { scroll: false },
    );
  });

  it("restores the selected tab whenever URL search state changes", () => {
    const view = render(
      <NetworkFindAreasWorkspace
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );

    expect(screen.getByTestId("network-tab-1")).toHaveAttribute(
      "aria-selected",
      "true",
    );

    navigation.search = "ws=network&tab=review&tenant=tw";
    view.rerender(
      <NetworkFindAreasWorkspace
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );
    expect(screen.getByTestId("network-tab-5")).toHaveAttribute(
      "aria-selected",
      "true",
    );

    navigation.search = "ws=network&tab=radar&tenant=tw";
    view.rerender(
      <NetworkFindAreasWorkspace
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );
    expect(screen.getByTestId("network-tab-1")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  // ADD-006 §3.4: a Listing Radar click must not be lost while initial
  // preference hydration, shell bootstrap or URL hydration is still in flight
  // and the console keeps re-publishing the server-rendered tab.
  it("keeps a Radar click that the URL has not caught up with yet", () => {
    navigation.search = "ws=network";

    const view = render(
      <NetworkFindAreasWorkspace
        initialTabId="overview"
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );
    expect(screen.getByTestId("network-tab-0")).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByTestId("network-tab-1"));
    expect(navigation.push).toHaveBeenCalledWith("/operator?ws=network&tab=radar", { scroll: false });
    expect(screen.getByTestId("network-tab-1")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("listing-radar-panel")).toBeInTheDocument();

    // The URL transition has not landed and the console re-publishes the stale
    // server tab: the selection still survives.
    view.rerender(
      <NetworkFindAreasWorkspace
        initialTabId="overview"
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );
    expect(screen.getByTestId("network-tab-1")).toHaveAttribute("aria-selected", "true");

    // Once the URL reports a tab it becomes authoritative again, so deep links
    // and browser back/forward are never overridden by a stale click.
    navigation.search = "ws=network&tab=review";
    view.rerender(
      <NetworkFindAreasWorkspace
        initialTabId="overview"
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );
    expect(screen.getByTestId("network-tab-5")).toHaveAttribute("aria-selected", "true");

    navigation.search = "ws=network";
    view.rerender(
      <NetworkFindAreasWorkspace
        initialTabId="overview"
        liveCandidates={unavailableCandidates}
        liveHeatZones={unavailableHeatZones}
      />,
    );
    expect(screen.getByTestId("network-tab-0")).toHaveAttribute("aria-selected", "true");
  });
});
