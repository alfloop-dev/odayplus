import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
