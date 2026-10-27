import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import type { ApiBinding } from "../../../../src/lib/api/binding";
import { NetworkFindAreasWorkspace as Workspace } from "../../NetworkFindAreasWorkspace";
import { NETWORK_TAB_IDS } from "../networkUrlState";
import { CanonicalIntakeCasePicker } from "../intake/CanonicalIntakeCasePicker";

// Existing route/data-gate cases explicitly model all read grants. The
// principal-projection cases below provide their exact bootstrap metadata.
function NetworkFindAreasWorkspace(props: ComponentProps<typeof Workspace>) {
  return <Workspace allowedTabIds={NETWORK_TAB_IDS} {...props} />;
}
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

const radarProps = vi.hoisted(() => ({ calls: [] as Array<Record<string, unknown>>, showNativePicker: false }));
const reviewPanelControl = vi.hoisted(() => ({ latest: null as unknown }));

vi.mock("../ListingRadarPanel", () => ({
  ListingRadarPanel: (props: Record<string, unknown>) => {
    radarProps.calls.push(props);
    const context = props.canonicalIntakeReadContext as ComponentProps<typeof CanonicalIntakeCasePicker>["context"] | undefined;
    return <div data-testid="listing-radar-panel">Listing Radar
      {radarProps.showNativePicker && context ? <CanonicalIntakeCasePicker context={context} /> : null}
    </div>;
  },
}));

vi.mock("../ReviewPanel", async (importOriginal) => ({
  // The workspace counts through the real queue selector; only the panel is stubbed.
  resolveEffectiveReviews: (await importOriginal<typeof import("../ReviewPanel")>()).resolveEffectiveReviews,
  ReviewPanel: (props: ComponentProps<typeof import("../ReviewPanel").ReviewPanel>) => {
    reviewPanelControl.latest = props;
    const { canDecide, reviews, onDecide } = props;
    return <div data-testid="review-authority" data-can-decide={String(canDecide)}>
      {canDecide && reviews[0] ? (
        <button onClick={() => onDecide?.(reviews[0].id, "GO", {
          reason: "Reviewed", conditions: "", requiredData: "", overrideAck: false,
        })} type="button">Submit test review decision</button>
      ) : null}
    </div>;
  },
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
    radarProps.showNativePicker = false;
    reviewPanelControl.latest = null;
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

  it.each(["query HTTP", "query JSON", "role", "tenant"] as const)(
    "keeps one native URL command bound to its actual account/context across a %s change", async (change) => {
      // Real picker, original native client and command hook; only the declared
      // local API transport and external account lease are engineering premises.
      radarProps.showNativePicker = true;
      const beforeTenant = sessionStorage.getItem("oday.operator.tenant");
      let finish!: () => void;
      const writes: RequestInit[] = [];
      const receipt = { intake_id: "00000000-0000-0000-0000-000000000095", intake_method: "URL",
        processing_state: "SUBMITTED", version: 1, submitted_at: "2026-10-19T08:00:00Z",
        correlation_id: "00000000-0000-0000-0000-000000000096",
        transition_id: "00000000-0000-0000-0000-000000000097",
        audit_event_id: "00000000-0000-0000-0000-000000000098",
        outbox_event_id: "00000000-0000-0000-0000-000000000099" };
      vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), window.location.origin).pathname;
        if (path === "/api/v1/canonical-intakes" && init?.method === "POST") {
          writes.push(init);
          if (change === "query JSON") {
            const response = new Response(JSON.stringify(receipt), { status: 201 });
            response.json = () => new Promise(resolve => { finish = () => resolve(receipt); });
            return Promise.resolve(response);
          }
          return new Promise<Response>(resolve => { finish = () => resolve(new Response(JSON.stringify(receipt), { status: 201 })); });
        }
        if (path === "/api/v1/canonical-intakes") return Promise.resolve(new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }));
        return Promise.resolve(forbiddenResponse("isolated-native-command", path));
      }));
      const guard = (current: () => boolean) => async () => current();
      let role: "expansion-manager" | "expansion-staff" = "expansion-manager";
      const view = render(<NetworkFindAreasWorkspace allowedTabIds={["radar"]} activeRoleId={role} createAuthenticatedReadGuard={guard} />);
      try {
        fireEvent.click(await screen.findByRole("button", { name: "選取收件／比對案件" }));
        fireEvent.click(await screen.findByRole("button", { name: "從網址建立正式收件" }));
        fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://source.example/same-native-command" } });
        fireEvent.change(screen.getByLabelText("正式收件範圍"), { target: { value: "0" } });
        fireEvent.change(screen.getByLabelText("送件原因"), { target: { value: "保留同一已送出指令及正式回執" } });
        fireEvent.click(screen.getByTestId("intake-submit-button"));
        await waitFor(() => { expect(writes).toHaveLength(1); expect(finish).toBeDefined(); });
        const key = new Headers(writes[0].headers).get("idempotency-key");
        expect(key).toBeTruthy();
        if (change.startsWith("query")) {
          navigation.search = "ws=network&tab=radar&search=changed-inbox-query";
          window.history.replaceState(null, "", `/operator?${navigation.search}`);
        } else if (change === "role") role = "expansion-staff";
        else sessionStorage.setItem("oday.operator.tenant", "different-observed-tenant");
        view.rerender(<NetworkFindAreasWorkspace allowedTabIds={["radar"]} activeRoleId={role} createAuthenticatedReadGuard={guard} />);
        if (change.startsWith("query")) {
          expect(screen.getByTestId("intake-add-dialog")).toBeVisible();
          expect(screen.getByTestId("intake-submit-button")).toBeDisabled();
        } else expect(screen.queryByTestId("canonical-intake-case-picker")).not.toBeInTheDocument();
        await act(async () => finish());
        if (change.startsWith("query")) expect(await screen.findByText(/正式收件 .*已送出/)).toHaveTextContent(receipt.intake_id);
        else expect(screen.queryByText(/正式收件 .*已送出/)).not.toBeInTheDocument();
        expect(writes).toHaveLength(1);
        expect(new Headers(writes[0].headers).get("idempotency-key")).toBe(key);
      } finally {
        if (beforeTenant === null) sessionStorage.removeItem("oday.operator.tenant");
        else sessionStorage.setItem("oday.operator.tenant", beforeTenant);
      }
    },
  );

  it.each([
    ["response", "URL"], ["JSON", "URL"],
    ["response", "tenant context"], ["JSON", "tenant context"],
  ] as const)("restarts all guarded background reads after %s is pending across a %s change", async (pendingStage, change) => {
    const paths = ["network-listings", "network-scoring", "network-rebalance", "network-reviews"];
    const counts = new Map<string, number>();
    const pending: Array<() => void> = [];
    let jsonReads = 0;
    const previousTenant = sessionStorage.getItem("oday.operator.tenant");
    const respond = (value: unknown) => new Response(JSON.stringify(value), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = paths.find(path => String(input).includes(path));
      if (!path) return Promise.resolve(new Response("{}", { status: 404 }));
      const count = (counts.get(path) ?? 0) + 1;counts.set(path, count);
      if (count > 1) return Promise.resolve(forbiddenResponse(`corr-current-${path}`, path));
      if (pendingStage === "response") return new Promise<Response>(resolve => {
        pending.push(() => resolve(respond({ source: "api", supersededPayload: path })));
      });
      const response = respond({ source: "api" });
      vi.spyOn(response, "json").mockImplementation(() => {
        jsonReads += 1;
        return new Promise(resolve => pending.push(() => resolve({ source: "api", supersededPayload: path })));
      });
      return Promise.resolve(response);
    }));
    // Only the lifecycle predicate is doubled. No cookie or principal is injected;
    // the workspace still captures its actual URL and observed-scope generation.
    const createAuthenticatedReadGuard = (current: () => boolean) => async () => current();
    const props = { activeRoleId: "platform-admin" as const, selectedHeatZoneId: "live-zone", createAuthenticatedReadGuard };
    try {
      if (change === "tenant context") {
        navigation.search = "ws=network&tab=compare";
        window.history.replaceState(null, "", "/operator?ws=network&tab=compare");
      }
      const view = render(<NetworkFindAreasWorkspace {...props} />);
      await waitFor(() => expect(paths.every(path => counts.get(path) === 1)).toBe(true));
      if (pendingStage === "JSON") await waitFor(() => expect(jsonReads).toBe(4));
      navigation.search = "ws=network&tab=compare";
      window.history.replaceState(null, "", "/operator?ws=network&tab=compare");
      if (change === "tenant context") {
        // This branch already started on Compare: the URL is unchanged,
        // so the new observed context itself must replace the old read.
        sessionStorage.setItem("oday.operator.tenant", "observed-new-tenant");
      }
      view.rerender(<NetworkFindAreasWorkspace {...props} />);
      await waitFor(() => expect(paths.every(path => counts.get(path) === 2)).toBe(true));
      // A later refusal is authoritative; finishing the old reads must not
      // overwrite its correlation or present the old payload as a fresh frame.
      const correlation = await screen.findByTestId("operator-data-unavailable-correlation");
      await waitFor(() => expect(correlation).toHaveTextContent("corr-current-network-scoring"));
      await act(async () => { pending.forEach(resolve => resolve()); });
      expect(correlation).toHaveTextContent("corr-current-network-scoring");
      expect(paths.map(path => counts.get(path))).toEqual([2, 2, 2, 2]);
      expect(screen.queryByText("supersededPayload")).toBeNull();
    } finally {
      if (previousTenant === null) sessionStorage.removeItem("oday.operator.tenant");
      else sessionStorage.setItem("oday.operator.tenant", previousTenant);
    }
  });

  it.each(["ready", "denied"] as const)("withholds the previous %s Rebalance frame while a new observed tenant context is pending", async previous => {
    const previousTenant = sessionStorage.getItem("oday.operator.tenant");
    sessionStorage.setItem("oday.operator.tenant", "observed-context-A");
    let rebalanceReads = 0;
    let resolveCurrent: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>(resolve => { resolveCurrent = resolve; });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith("/network-rebalance")) {
        rebalanceReads += 1;
        if (rebalanceReads > 1) return pending;
        return Promise.resolve(previous === "denied" ? forbiddenResponse("corr-prior-rebalance", "operator_network")
          : new Response(JSON.stringify({ source: "api", stores: [{ id: "prior-context-only" }] }), {
            status: 200, headers: { "Content-Type": "application/json" },
          }));
      }
      return Promise.resolve(forbiddenResponse("corr-unrelated-read", "operator_network"));
    }));
    const createAuthenticatedReadGuard = (current: () => boolean) => async () => current();
    const props = { activeRoleId: "platform-admin" as const, selectedHeatZoneId: "live-zone", createAuthenticatedReadGuard };
    try {
      const view = render(<NetworkFindAreasWorkspace {...props} />);
      const summary = screen.getByLabelText("展店與店網摘要");
      const count = within(summary).getByText("重配候選", { exact: false });
      await waitFor(() => expect(count).toHaveAttribute("data-count-state", previous === "ready" ? "known" : "unread"));
      if (previous === "ready") expect(summary).toHaveTextContent("1 重配候選");
      sessionStorage.setItem("oday.operator.tenant", "observed-context-B");
      view.rerender(<NetworkFindAreasWorkspace {...props} />);
      // This is a synchronous render assertion: stale counts are withheld before
      // the replacement HTTP response, including while that response is pending.
      expect(count).toHaveAttribute("data-count-state", "pending");
      expect(summary).not.toHaveTextContent("1 重配候選");
      await waitFor(() => expect(rebalanceReads).toBe(2));
      await act(async () => { resolveCurrent(forbiddenResponse("corr-current-rebalance", "operator_network")); });
      expect(count).toHaveAttribute("data-count-state", "unread");
      expect(summary).not.toHaveTextContent("1 重配候選");
    } finally {
      if (previousTenant === null) sessionStorage.removeItem("oday.operator.tenant");
      else sessionStorage.setItem("oday.operator.tenant", previousTenant);
    }
  });

  it("shows only auditor+admin readable tabs and never preloads the three refused endpoints", async () => {
    navigation.search = "ws=network&tab=review";
    render(<NetworkFindAreasWorkspace activeRoleId="pm-audit" allowedTabIds={["rebalance"]} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/v1/operator/network-rebalance");
    expect(screen.getAllByRole("tab")).toHaveLength(1);
    expect(screen.getByTestId("network-tab-6")).toHaveAttribute("aria-selected", "true");
    for (const index of [0, 1, 2, 3, 4, 5, 7]) {
      expect(screen.queryByTestId(`network-tab-${index}`)).toBeNull();
    }
    expect(screen.queryByTestId("review-authority")).toBeNull();
  });

  it("keeps the viewer's six readable tabs but hides unscoped Rebalance and composition", async () => {
    navigation.search = "ws=network&tab=rebalance";
    render(<NetworkFindAreasWorkspace activeRoleId="pm-audit" selectedHeatZoneId="live-zone"
      allowedTabIds={["areas", "radar", "candidates", "score", "compare", "review"]} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(screen.getAllByRole("tab")).toHaveLength(6);
    expect(screen.getByTestId("network-tab-0")).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("network-tab-6")).toBeNull();
    expect(screen.queryByTestId("network-tab-7")).toBeNull();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual(expect.arrayContaining([
      expect.stringContaining("network-listings?"), "/api/v1/operator/network-scoring",
      "/api/v1/operator/network-reviews",
    ]));
  });

  it.each(["ready", "denied"])("withholds previous %s counts and failures when bootstrap revokes reads", async (previous) => {
    const fetchMock = stubDeniedNetworkReads();
    if (previous === "ready") {
      fetchMock.mockImplementation(async () => new Response(JSON.stringify({
        source: "api", heatZones: [], listingSources: [], candidates: [],
        listings: [{ id: "L-PRIOR", status: "new", hardRuleFailures: [] }],
        siteReviews: [], expansionSteps: [], assistedIntakes: [], reviews: [], stores: [],
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    const view = render(<NetworkFindAreasWorkspace activeRoleId="platform-admin" selectedHeatZoneId="live-zone" />);
    const summary = screen.getByLabelText("展店與店網摘要");
    await waitFor(() => expect(summary).toHaveTextContent(previous === "ready" ? "1 今日新物件" : "今日新物件（未授權）"));
    fetchMock.mockClear();
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="pm-audit" selectedHeatZoneId="live-zone" allowedTabIds={["rebalance"]} />);
    await waitFor(() => expect(summary).toHaveTextContent("0 重配候選"));
    for (const label of ["今日新物件", "進行中候選", "待審 Review"]) {
      expect(within(summary).getByText(label, { exact: false })).toHaveAttribute("data-count-state", "withheld");
      expect(summary).toHaveTextContent(`— ${label}（未授權）`);
    }
    expect(summary).not.toHaveTextContent("1 今日新物件");
    expect(summary.querySelector('[data-count-state="pending"]')).toBeNull();
    expect(screen.queryByTestId("operator-data-unavailable-correlation")).toBeNull();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual(["/api/v1/operator/network-rebalance"]);
  });

  it("fails closed without bootstrap tab metadata in production", () => {
    render(<Workspace activeRoleId="pm-audit" />);
    expect(screen.getByText("目前身分沒有可讀取的 Network 分頁。")).toBeInTheDocument();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
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
  ] as const)("presents fixture-compatible role eligibility for %s without impersonation", (roleId, canDecide) => {
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

  // Dedicated READ/count/gate controls. A fresh mount models a page reload;
  // production legacy /decide success is no longer a prerequisite or claim.
  it.each(["empty", "denied"])("READ_ONLY: uses the %s dedicated Review reread's source for count and gate", async (result) => {
    navigation.search = "ws=network&tab=review";
    let reread = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      const respond = (body: unknown) => new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/network-reviews")) {
        if (reread && result === "denied") return forbiddenResponse("corr-reviews-reload-403", "sitescore");
        return respond({ source: "api", reviews: reread ? [] : [{ ...legacyReviewPrerequisites, id: "REV-LIVE-1", status: "pending" }] });
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
    const workspace = <NetworkFindAreasWorkspace activeRoleId="expansion-manager"
      liveCandidates={unavailableCandidates} liveHeatZones={unavailableHeatZones} />;
    const first = render(workspace);

    let state = screen.getByLabelText("展店與店網摘要");
    await waitFor(() => expect(state).toHaveTextContent("1 今日新物件"));
    await waitFor(() => expect(state).toHaveTextContent("1 待審 Review"));
    expect(screen.getByTestId("network-tab-count-5")).toHaveTextContent("1");
    first.unmount();
    reread = true;
    render(workspace);
    state = screen.getByLabelText("展店與店網摘要");
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
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("production noncanonical review refuses a direct legacy callback without a business POST", async () => {
    navigation.search = "ws=network&tab=review";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      const response = (body: unknown) => new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/network-reviews")) return response({ source: "api", reviews: [legacyReviewPrerequisites] });
      if (url.includes("network-listings")) return response({ source: "api", listings: [], candidates: [], siteReviews: [] });
      if (url.endsWith("/network-scoring")) return response({ source: "api", candidates: [] });
      if (url.endsWith("/network-rebalance")) return response({ source: "api", stores: [] });
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager"
      liveCandidates={unavailableCandidates} liveHeatZones={unavailableHeatZones} />);
    await waitFor(() => expect(screen.getByTestId("review-authority")).toHaveAttribute("data-can-decide", "false"));
    const props = reviewPanelControl.latest as ComponentProps<typeof import("../ReviewPanel").ReviewPanel>;
    expect(props.onRequestDecision?.(legacyReviewPrerequisites.id, "GO")).toBe(false);
    expect(await props.onDecide?.(legacyReviewPrerequisites.id, "GO", {
      reason: "Direct callback cannot grant production native authority", conditions: "", requiredData: "", overrideAck: false,
    })).toBe(false);
    expect(screen.queryByRole("button", { name: "Submit test review decision" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
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
