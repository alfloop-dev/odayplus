import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiBinding } from "../../../../src/lib/api/binding";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import type { Candidate, OperatorHeatZone } from "../../types";
import { LISTING_FIXTURES } from "../../fixtures";
import type { NetworkScoringSnapshot, ScoringCandidate } from "../networkScoringTypes";

const apiCandidate: ScoringCandidate = {
  id: "CS-live",
  listingId: null,
  heatZoneId: "HZ-live",
  title: "Live candidate",
  zoneLabel: "Live zone",
  address: "Live address",
  modelVersion: "v3",
  datasetSnapshotId: "snapshot-live",
  stage: "needdata",
  gate: {
    state: "blocked", passed: false, missing: ["address"], otherMissing: [],
    blockNote: "", checks: [], okCount: 0, totalCount: 1,
  },
  scored: false,
  score: null,
  recommendation: null,
  inCompare: false,
};

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

  it("can enter and leave intake detail without changing hook order", () => {
    const props = { liveCandidates: unavailableCandidates, liveHeatZones: unavailableHeatZones };
    const view = render(<NetworkFindAreasWorkspace {...props} />);
    expect(screen.getByLabelText("Network tabs")).toBeInTheDocument();

    navigation.search = "ws=network&tab=radar&selected=IN-3001&dialog=detail";
    view.rerender(<NetworkFindAreasWorkspace {...props} />);
    expect(screen.queryByLabelText("Network tabs")).not.toBeInTheDocument();

    navigation.search = "ws=network&tab=radar";
    view.rerender(<NetworkFindAreasWorkspace {...props} />);
    expect(screen.getByLabelText("Network tabs")).toBeInTheDocument();
  });

  it.each([{ rows: [apiCandidate] }, { rows: [] }])("uses scoring candidate rows without leaking fallback rows (%j)", async ({ rows }) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const snapshot: NetworkScoringSnapshot = {
      source: "fixture",
      modelVersion: "v3",
      candidates: rows,
      scorecards: [],
      batchResults: [],
      compare: { columns: [], metrics: [], recommendation: null, empty: true },
      compareSet: [],
    };
    vi.mocked(fetch).mockImplementation((url) => {
      if (String(url) === "/api/v1/operator/network-scoring") {
        return Promise.resolve(new Response(JSON.stringify(snapshot)));
      }
      if (String(url) === "/api/v1/operator/network-reviews") {
        return Promise.resolve(new Response(JSON.stringify({ source: "api", reviews: [] })));
      }
      return new Promise<Response>(() => undefined);
    });

    const view = render(
      <NetworkFindAreasWorkspace listings={LISTING_FIXTURES.map((listing) => ({ ...listing, status: "archived" }))} />,
    );
    const stats = screen.getByLabelText("Network Find Areas state");
    await waitFor(() => {
      expect(within(stats).getByText("進行中候選", { exact: false })).toHaveTextContent(`${rows.length} 進行中候選`);
      expect(within(stats).getByText("待審 Review", { exact: false })).toHaveTextContent("0 待審 Review");
    });
    expect(within(stats).getByText("今日新物件", { exact: false })).toHaveTextContent("0 今日新物件");
    expect(screen.getByTestId("network-tab-2")).toHaveTextContent(rows.length ? "Candidates1" : "Candidates");
    expect(screen.getByTestId("network-tab-5")).toHaveTextContent("審核Review");

    // Reprojecting the same scoring payload on every render would repeatedly
    // set localCandidates in an effect and cause a maximum-depth render loop.
    view.rerender(<NetworkFindAreasWorkspace />);
    expect(within(stats).getByText("進行中候選", { exact: false })).toHaveTextContent(`${rows.length} 進行中候選`);
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
