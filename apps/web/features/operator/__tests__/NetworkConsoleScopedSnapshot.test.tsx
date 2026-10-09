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
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole } from "../OperatorConsole";

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

function stubProductionFetch(snapshot: unknown) {
  const networkListingUrls: URL[] = [];
  const legacyDomainPaths: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input instanceof Request ? input.url : input), "http://localhost");
    if (url.pathname === "/api/v1/operator/bootstrap") return jsonResponse(liveEnvelope);
    if (url.pathname === "/api/v1/operator/network-listings") {
      networkListingUrls.push(url);
      return jsonResponse(snapshot);
    }
    if (/\/(heatzones|listings\/candidates|sitescore)/.test(url.pathname)) {
      legacyDomainPaths.push(url.pathname);
      return jsonResponse({ detail: "role does not permit view on heatzone" }, 403);
    }
    return jsonResponse({ detail: "not routed" }, 503);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { legacyDomainPaths, networkListingUrls };
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

  it("initialises from the scoped operator snapshot while legacy domain reads are denied", async () => {
    const { legacyDomainPaths, networkListingUrls } = stubProductionFetch(scopedSnapshot);

    render(<OperatorConsole searchParams={{ ws: "network" }} />);

    expect(await screen.findByTestId("network-panel-find-areas")).toBeInTheDocument();
    // The legacy domain bindings really were requested and denied.
    await waitFor(() => expect(legacyDomainPaths.some((path) => path.endsWith("/heatzones"))).toBe(true));
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

    const state = screen.getByLabelText("Network Find Areas state");
    expect(state).toHaveTextContent("2 HeatZones");
    expect(state).toHaveTextContent("1 listings");
    expect(screen.queryByTestId("operator-data-unavailable")).toBeNull();
    expect(screen.queryByText("fixture data")).toBeNull();
  });

  it("keeps authorized listing rows in Listing Radar when HeatZone aggregates are withheld", async () => {
    const { networkListingUrls } = stubProductionFetch(zonesWithheldSnapshot);

    const { unmount } = render(<OperatorConsole searchParams={{ ws: "network" }} />);

    const state = await screen.findByLabelText("Network Find Areas state");
    await waitFor(() => expect(state).toHaveTextContent("1 listings"), { timeout: 5000 });
    expect(state).toHaveTextContent("0 HeatZones");
    // Find Areas owns the HeatZones, so it alone reports the withheld aggregate as empty.
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

  it("reports an authorized empty scoped snapshot as empty instead of a binding error", async () => {
    const { legacyDomainPaths, networkListingUrls } = stubProductionFetch(emptyScopedSnapshot);

    render(<OperatorConsole searchParams={{ ws: "network" }} />);

    await waitFor(() => expect(networkListingUrls.length).toBeGreaterThan(0));
    await waitFor(() => expect(legacyDomainPaths.some((path) => path.endsWith("/heatzones"))).toBe(true));
    await waitFor(() =>
      expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "empty"),
    );
    expect(networkListingUrls.every((url) => !url.searchParams.has("selectedHeatZoneId"))).toBe(true);
    expect(screen.queryByTestId("network-panel-find-areas")).toBeNull();
    expect(screen.queryByText("fixture data")).toBeNull();
  });
});
