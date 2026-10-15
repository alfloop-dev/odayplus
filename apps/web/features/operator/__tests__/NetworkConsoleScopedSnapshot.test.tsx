/**
 * ODP-OPERATOR-READ-AUTHORIZATION-001 R7 — production Network composition.
 *
 * The verified operator-only reader (platform_admin + auditor + operator_viewer)
 * is denied the general heatzone/candidate/SiteScore domain reads on purpose.
 * The real Console mount supplies neither `selectedHeatZoneId` nor `heatZones`,
 * so Find Areas must initialise from the scoped operator snapshot itself:
 * request it without a selection, adopt the zone the API names, and never
 * gate a ready scoped snapshot on the denied legacy bindings. An authorized
 * but empty scoped snapshot is reported as empty, not as an error or fixtures.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole } from "../OperatorConsole";
import { resolveNetworkCountState } from "../NetworkFindAreasWorkspace";
import { NETWORK_TAB_IDS } from "../network/networkUrlState";

const nav = vi.hoisted(() => ({ search: "ws=network" }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/operator",
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const role = {
  id: "platform-admin",
  label: "Platform Admin",
  subtitle: "Scoped operator reader",
  allowedWorkspaces: ["today", "network"],
  heroName: "Scoped Reader",
};

const liveEnvelope = {
  meta: {
    source: "operator-shell-production",
    dataMode: "live",
    role,
    counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 0 },
  },
  navigation: {
    roles: [role],
    workspaces: [
      { id: "today", label: "Today", shortLabel: "Today", description: "Live queue", allowed: true },
      { id: "network", label: "Network", shortLabel: "Network", description: "Network", allowed: true },
    ],
    allowedWorkspaces: ["today", "network"],
    allowedTabs: { network: [...NETWORK_TAB_IDS] },
  },
  header: {
    counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 0 },
  },
  today: {
    hero: { name: "Scoped Reader", roleLabel: "Platform Admin", scope: "Tenant scope", dateLabel: "2026-10-09" },
    kpis: [{ label: "Live unresolved", value: "0", tone: "info" }],
    queue: [],
    decisions: [],
    riskRows: [],
    auditFeed: [],
  },
  notifications: [],
  search: { count: 0, items: [] },
};

const scopedSnapshot = {
  source: "api",
  selectedHeatZoneId: "HZ-SCOPED-02",
  selectedLens: "demand",
  heatZones: [
    {
      id: "HZ-SCOPED-01",
      label: "Scoped Zone One",
      rank: 1,
      centroid: [121.51, 25.04],
      demandGap: 0.4,
      competitionIndex: 0.3,
      cannibalizationRisk: "low",
      rentBand: "NT$80k",
      confidence: 0.7,
      recommendedLens: "demand",
      reasons: [],
      risks: [],
      nextStep: "",
    },
    {
      id: "HZ-SCOPED-02",
      label: "Scoped Zone Two",
      rank: 2,
      centroid: [121.52, 25.05],
      demandGap: 0.6,
      competitionIndex: 0.2,
      cannibalizationRisk: "medium",
      rentBand: "NT$90k",
      confidence: 0.8,
      recommendedLens: "demand",
      reasons: [],
      risks: [],
      nextStep: "",
    },
  ],
  listingSources: [
    { id: "SRC-LIVE", name: "Live broker feed", status: "connected", complianceNote: "approved" },
  ],
  listings: [
    {
      id: "L-SCOPED-1",
      sourceId: "SRC-LIVE",
      heatZoneId: "HZ-SCOPED-02",
      address: "Scoped Road 1",
      status: "new",
      rentPerMonth: 90000,
      areaPing: 30,
      geocodeConfidence: 0.9,
      hardRuleFailures: [],
    },
  ],
  candidates: [],
  siteReviews: [],
  expansionSteps: [],
  assistedIntakes: [],
};

// Combined brand/region/store/assigned-area scope: the producer withholds the
// whole-zone HeatZone aggregates but keeps the individually authorized rows.
const zonesWithheldSnapshot = {
  ...scopedSnapshot,
  selectedHeatZoneId: null,
  heatZones: [],
};

const emptyScopedSnapshot = {
  ...scopedSnapshot,
  selectedHeatZoneId: null,
  heatZones: [],
  listingSources: [],
  listings: [],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// The canonical envelope (shared/api/errors.py) of a guarded read refusal.
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

const DENIED = Symbol("denied");

function stubProductionFetch(snapshot: unknown, denyScoring = false, reviews?: unknown, allowedTabs: readonly string[] = NETWORK_TAB_IDS, persona = "platform-admin") {
  const networkListingUrls: URL[] = [];
  const legacyDomainPaths: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input instanceof Request ? input.url : input), "http://localhost");
    if (url.pathname === "/api/v1/operator/bootstrap") {
      const allowedWorkspaces = allowedTabs.length ? ["today", "network"] : ["today"];
      const projectedRole = { ...role, id: persona, allowedWorkspaces };
      return jsonResponse({ ...liveEnvelope, meta: { ...liveEnvelope.meta, role: projectedRole },
        navigation: { ...liveEnvelope.navigation, roles: [projectedRole], allowedWorkspaces,
          allowedTabs: { network: allowedTabs } } });
    }
    if (url.pathname === "/api/v1/operator/network-listings") {
      networkListingUrls.push(url);
      return snapshot === DENIED ? forbiddenResponse("corr-listings-403", "operator_network") : jsonResponse(snapshot);
    }
    if (url.pathname === "/api/v1/operator/network-reviews" && reviews !== undefined) {
      return jsonResponse(reviews);
    }
    if ((snapshot === DENIED || denyScoring) && /\/operator\/network-(scoring|reviews)$/.test(url.pathname)) {
      return forbiddenResponse("corr-scoring-403", "sitescore");
    }
    if (url.pathname === "/api/v1/operator/network-rebalance") {
      return jsonResponse({ source: "api", stores: [] });
    }
    if (/\/(heatzones|listings\/candidates|sitescore)/.test(url.pathname)) {
      legacyDomainPaths.push(url.pathname);
      return jsonResponse({ detail: "role does not permit view on heatzone" }, 403);
    }
    return jsonResponse({ detail: "not routed" }, 503);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, legacyDomainPaths, networkListingUrls };
}

describe("Network Find Areas in the production Operator Console composition", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.sessionStorage.setItem("oday.operator.role", "platform-admin");
    nav.search = "ws=network";
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("consumes auditor+admin bootstrap tab grants without requesting denied Network reads", async () => {
    const { fetchMock, legacyDomainPaths } = stubProductionFetch(DENIED, true, undefined, ["rebalance"]);
    render(<OperatorConsole searchParams={{ ws: "network", tab: "review" }} />);
    await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(1));
    expect(screen.getByTestId("network-tab-6")).toHaveAttribute("aria-selected", "true");
    const networkReads = fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("/network-"));
    expect(networkReads).toEqual(["/api/v1/operator/network-rebalance"]);
    expect(legacyDomainPaths).toEqual([]);
    const summary = screen.getByLabelText("展店與店網摘要");
    await waitFor(() => expect(summary).toHaveTextContent("0 重配候選"));
    for (const label of ["今日新物件", "進行中候選", "待審 Review"]) {
      expect(within(summary).getByText(label, { exact: false })).toHaveAttribute("data-count-state", "withheld");
      expect(summary).toHaveTextContent(`— ${label}（未授權）`);
    }
    expect(summary.querySelector('[data-count-state="pending"]')).toBeNull();
  });

  it("settles viewer summary chips and never requests withheld Rebalance or composition", async () => {
    window.sessionStorage.setItem("oday.operator.role", "pm-audit");
    const tabs = ["areas", "radar", "candidates", "score", "compare", "review"];
    const { fetchMock, legacyDomainPaths } = stubProductionFetch(scopedSnapshot, false,
      { source: "api", reviews: [] }, tabs, "pm-audit");
    render(<OperatorConsole searchParams={{ ws: "network", tab: "rebalance" }} />);
    const summary = await screen.findByLabelText("展店與店網摘要");
    await waitFor(() => expect(summary).toHaveTextContent("1 今日新物件"));
    await waitFor(() => expect(summary).toHaveTextContent("0 待審 Review"));
    expect(within(summary).getByText("重配候選", { exact: false })).toHaveAttribute("data-count-state", "withheld");
    expect(summary).toHaveTextContent("— 重配候選（未授權）");
    expect(summary.querySelector('[data-count-state="pending"]')).toBeNull();
    expect(fetchMock.mock.calls.map(([url]) => String(url)).filter((url) =>
      /network-rebalance|merge-split/.test(url))).toEqual([]);
    expect(legacyDomainPaths).toEqual([]);
    expect(screen.getAllByRole("tab")).toHaveLength(6);
  });

  it("hides a workspace with no readable tabs even when a stored deep link requests it", async () => {
    const { fetchMock } = stubProductionFetch(DENIED, true, undefined, []);
    render(<OperatorConsole searchParams={{ ws: "network" }} />);
    await waitFor(() => expect(screen.getByTestId("operator-console").querySelector('[data-data-status="ready"]')).not.toBeNull());
    expect(within(screen.getByRole("navigation", { name: "Operator workspaces" })).queryByText("Network")).toBeNull();
    expect(screen.queryByRole("tab", { name: /Rebalance/ })).toBeNull();
    expect(fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("/network-"))).toEqual([]);
  });

  it("initialises from the scoped operator snapshot without requesting denied legacy domains", async () => {
    const { legacyDomainPaths, networkListingUrls } = stubProductionFetch(scopedSnapshot);

    render(<OperatorConsole searchParams={{ ws: "network" }} />);

    expect(await screen.findByTestId("network-panel-find-areas")).toBeInTheDocument();
    // Production no longer preloads RBAC-only domain bindings.
    expect(legacyDomainPaths).toEqual([]);
    // First read carries no injected selection; the API-named zone is then used.
    expect(networkListingUrls[0].searchParams.has("selectedHeatZoneId")).toBe(false);
    await waitFor(() =>
      expect(networkListingUrls.map((url) => url.searchParams.get("selectedHeatZoneId"))).toContain(
        "HZ-SCOPED-02",
      ),
    );
    expect(networkListingUrls.every((url) => url.searchParams.get("selectedHeatZoneId") !== "HZ-SCOPED-01")).toBe(true);
    for (const call of vi.mocked(fetch).mock.calls) {
      const url = String(call[0]);
      if (url.includes("/api/v1/operator/network-listings")) {
        expect(call[1]?.headers).toMatchObject({ "X-Operator-Role": "platform-admin" });
      }
    }

    const state = screen.getByLabelText("展店與店網摘要");
    expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent("Scoped Zone Two");
    expect(state).toHaveTextContent("1 今日新物件");
    expect(screen.queryByTestId("operator-data-unavailable")).toBeNull();
    expect(screen.queryByText("fixture data")).toBeNull();
  });

  it("keeps authorized listing rows in Listing Radar when HeatZone aggregates are withheld", async () => {
    const { networkListingUrls } = stubProductionFetch(zonesWithheldSnapshot);

    const { unmount } = render(<OperatorConsole searchParams={{ ws: "network" }} />);

    const state = await screen.findByLabelText("展店與店網摘要");
    await waitFor(() => expect(state).toHaveTextContent("1 今日新物件"), { timeout: 5000 });
    // Package10 omits whole-zone/average-confidence KPIs, never turns their
    // withheld disposition into zero; the visible Find Areas gate still names it.
    expect(state).not.toHaveTextContent(/HeatZones|avg confidence/);
    expect(resolveNetworkCountState("ready", null, false, true)).toEqual({
      known: false, label: "未授權", reason: "withheld",
    });
    expect(within(state).getByText("今日新物件", { exact: false })).toHaveAttribute("data-count-state", "known");
    // Find Areas reports the missing aggregate without suppressing authorized rows.
    await waitFor(() =>
      expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "empty"),
    );
    expect(screen.getByTestId("operator-data-unavailable")).toHaveTextContent(
      "HeatZone aggregates are not authorized for this read scope",
    );
    expect(networkListingUrls.every((url) => !url.searchParams.has("selectedHeatZoneId"))).toBe(true);
    unmount();

    // The same production snapshot renders its authorized rows on Listing Radar.
    nav.search = "ws=network&tab=radar";
    render(<OperatorConsole searchParams={{ ws: "network", tab: "radar" }} />);
    const radar = await screen.findByTestId("network-panel-listings", {}, { timeout: 5000 });
    await waitFor(() => expect(radar).toHaveTextContent("Scoped Road 1"), { timeout: 5000 });
    expect(screen.queryByTestId("operator-data-unavailable")).toBeNull();
    expect(screen.queryByText("fixture data")).toBeNull();
  });

  it.each(["candidates", "sitescore"])(
    "keeps scoped candidate counts independent of withheld zones and the denied %s read",
    async (tab) => {
      const snapshot = {
        ...zonesWithheldSnapshot,
        candidates: [{
          id: "CS-SCOPED-1", listingId: "L-SCOPED-1", heatZoneId: "HZ-SCOPED-02",
          title: "Scoped candidate", address: "Scoped Road 1", status: "ready",
          score: 82, recommendation: "GO", modelVersion: "v1",
          datasetSnapshotId: "snapshot-scoped", missingData: [],
        }],
        siteReviews: [{
          id: "REV-SCOPED-1", candidateId: "CS-SCOPED-1", status: "pending",
          requestedByRoleId: "expansionManager", reviewerRoleIds: ["opsLead"],
          requestedAt: "2026-10-10T12:17:00Z", reasonRequired: true,
        }],
      };
      stubProductionFetch(snapshot, true);
      nav.search = `ws=network&tab=${tab}`;
      render(<OperatorConsole searchParams={{ ws: "network", tab }} />);

      const state = await screen.findByLabelText("展店與店網摘要");
      await waitFor(() => expect(state).toHaveTextContent("1 進行中候選"), { timeout: 5000 });
      expect(state).toHaveTextContent("1 今日新物件");
      expect(state).toHaveTextContent("1 待審 Review");
      expect(state).not.toHaveTextContent(/HeatZones|avg confidence/);
      expect(resolveNetworkCountState("ready", null, false, true)).toEqual({
        known: false, label: "未授權", reason: "withheld",
      });
      for (const label of ["今日新物件", "進行中候選", "待審 Review"]) {
        expect(within(state).getByText(label, { exact: false })).toHaveAttribute("data-count-state", "known");
      }
      await waitFor(() =>
        expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-failure-kind", "forbidden"),
      );
      expect(screen.getByTestId("operator-data-unavailable-correlation")).toHaveTextContent("corr-scoring-403");
      // The independently refused scoring binding must not render fabricated score cards.
      expect(screen.queryByText("Scoped candidate")).toBeNull();
      for (const [, init] of vi.mocked(fetch).mock.calls) {
        expect((init as RequestInit | undefined)?.method ?? "GET").toBe("GET");
      }
    },
  );

  it.each(["overview", "review"])(
    "uses an authorized empty dedicated Review queue instead of projected pending rows on %s",
    async (tab) => {
      stubProductionFetch({
        ...zonesWithheldSnapshot,
        siteReviews: [{
          id: "REV-SCOPED-1", candidateId: "CS-SCOPED-1", status: "pending",
          requestedByRoleId: "expansionManager", reviewerRoleIds: ["opsLead"],
          requestedAt: "2026-10-10T12:17:00Z", reasonRequired: true,
        }],
      }, true, { source: "api", reviews: [] });
      nav.search = `ws=network&tab=${tab}`;
      render(<OperatorConsole searchParams={{ ws: "network", tab }} />);

      const state = await screen.findByLabelText("展店與店網摘要");
      await waitFor(() => expect(state).toHaveTextContent("1 今日新物件"), { timeout: 5000 });
      await waitFor(() => expect(state).toHaveTextContent("0 待審 Review"));
      expect(within(state).getByText("待審 Review", { exact: false })).toHaveAttribute("data-count-state", "known");
      expect(state).not.toHaveTextContent("1 待審 Review");
      expect(screen.queryByTestId("network-tab-count-5")).toBeNull();
      if (tab === "review") {
        expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "empty");
        expect(screen.getByTestId("operator-data-unavailable")).not.toHaveAttribute("data-failure-kind");
        expect(screen.queryByText("REV-SCOPED-1")).toBeNull();
      }
      for (const [, init] of vi.mocked(fetch).mock.calls) {
        expect((init as RequestInit | undefined)?.method ?? "GET").toBe("GET");
      }
    },
  );

  it("reports an authorized empty scoped snapshot as empty instead of a binding error", async () => {
    const { legacyDomainPaths, networkListingUrls } = stubProductionFetch(emptyScopedSnapshot);

    render(<OperatorConsole searchParams={{ ws: "network" }} />);

    await waitFor(() => expect(networkListingUrls.length).toBeGreaterThan(0));
    expect(legacyDomainPaths).toEqual([]);
    await waitFor(() =>
      expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "empty"),
    );
    expect(networkListingUrls.every((url) => !url.searchParams.has("selectedHeatZoneId"))).toBe(true);
    expect(screen.queryByTestId("network-panel-find-areas")).toBeNull();
    expect(screen.queryByText("fixture data")).toBeNull();
  });

  it("still presents a received 403 after bootstrap as a denial, not a network failure or zero", async () => {
    const { legacyDomainPaths } = stubProductionFetch(DENIED);

    const { unmount } = render(<OperatorConsole searchParams={{ ws: "network" }} />);

    // The shell's own loading gate shows first; wait for the Find Areas gate.
    await waitFor(
      () => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-failure-kind", "forbidden"),
      { timeout: 5000 },
    );
    const gate = screen.getByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "error");
    expect(gate).not.toHaveTextContent("連線失敗");
    expect(gate).not.toHaveTextContent("瀏覽器無法連到營運資料服務");
    expect(screen.getByTestId("operator-data-unavailable-correlation")).toHaveTextContent("corr-listings-403");

    const state = screen.getByLabelText("展店與店網摘要");
    for (const label of ["今日新物件", "進行中候選", "待審 Review"]) {
      expect(state).toHaveTextContent(`— ${label}（未授權）`);
      expect(state).not.toHaveTextContent(`0 ${label}`);
    }
    await waitFor(() => expect(state).toHaveTextContent("0 重配候選"));
    // The denial is presented as received: no write, no grant request and no
    // retry into broader domain reads than the console already makes.
    for (const [, init] of vi.mocked(fetch).mock.calls) {
      expect((init as RequestInit | undefined)?.method ?? "GET").toBe("GET");
    }
    expect(new Set(legacyDomainPaths).size).toBeLessThanOrEqual(3);
    unmount();

    nav.search = "ws=network&tab=radar";
    render(<OperatorConsole searchParams={{ ws: "network", tab: "radar" }} />);
    const radar = await screen.findByTestId("network-panel-listings", {}, { timeout: 5000 });
    await waitFor(() =>
      expect(within(radar).getByTestId("operator-data-unavailable")).toHaveAttribute("data-failure-kind", "forbidden"),
      { timeout: 5000 },
    );
    expect(radar).not.toHaveTextContent("No listings match the selected filter");
    expect(radar).not.toHaveTextContent("0 筆");
    expect(within(radar).queryByTestId("network-listing-table")).toBeNull();
  }, 20000);

  it("keeps a genuine authorized empty 200 as an authoritative zero", async () => {
    stubProductionFetch(emptyScopedSnapshot);

    render(<OperatorConsole searchParams={{ ws: "network" }} />);

    const state = await screen.findByLabelText("展店與店網摘要", {}, { timeout: 5000 });
    await waitFor(() =>
      expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "empty"),
      { timeout: 5000 },
    );
    expect(state).toHaveTextContent("0 今日新物件");
    expect(state).toHaveTextContent("0 進行中候選");
    expect(state).toHaveTextContent("0 待審 Review");
    expect(state).not.toHaveTextContent(/HeatZones|avg confidence/);
    for (const label of ["今日新物件", "進行中候選", "待審 Review"]) {
      expect(within(state).getByText(label, { exact: false })).toHaveAttribute("data-count-state", "known");
    }
    expect(state).not.toHaveTextContent("未授權");
    expect(screen.getByTestId("operator-data-unavailable")).not.toHaveAttribute("data-failure-kind");
  });
});
