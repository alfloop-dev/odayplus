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

vi.mock("../ListingRadarPanel", () => ({
  ListingRadarPanel: () => (
    <div data-testid="listing-radar-panel">Listing Radar</div>
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

describe("NetworkFindAreasWorkspace route and gate behavior", () => {
  beforeEach(() => {
    navigation.pathname = "/operator";
    navigation.search = "ws=network&tab=radar";
    navigation.push.mockReset();
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
});
