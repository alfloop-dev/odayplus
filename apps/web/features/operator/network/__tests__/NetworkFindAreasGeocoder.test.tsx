import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiBinding } from "../../../../src/lib/api/binding";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import type { Candidate, OperatorHeatZone } from "../../types";

// The Find Areas tab renders the deck.gl/MapLibre canvas, which needs a real
// WebGL context. The geocoder wiring is what is under test here, so the map is
// stubbed exactly as the route-gate suite stubs Listing Radar.
vi.mock("../HeatZoneMap", () => ({
  HeatZoneMap: () => <div data-testid="heat-zone-map-stub" />,
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

const unavailableCandidates = unavailableBinding<Candidate>();
const unavailableHeatZones = unavailableBinding<OperatorHeatZone>();

function renderFindAreas(activeRoleId?: "expansion-manager" | "pm-audit" | "ops-lead") {
  return render(
    <NetworkFindAreasWorkspace
      activeRoleId={activeRoleId}
      liveCandidates={unavailableCandidates}
      liveHeatZones={unavailableHeatZones}
    />,
  );
}

describe("Find Areas geocoder wiring (UX-SCR-EXP-001)", () => {
  beforeEach(() => {
    navigation.pathname = "/operator";
    navigation.search = "ws=network&tab=find";
    navigation.push.mockReset();
    window.history.replaceState(null, "", "/operator?ws=network&tab=find");
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<Response>(() => undefined)));
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("mounts the governed geocoder search on the Find Areas tab", () => {
    renderFindAreas("expansion-manager");

    expect(screen.getByTestId("geocoder-search-panel")).toBeInTheDocument();
    expect(screen.getByTestId("geocoder-query-input")).toBeInTheDocument();
  });

  it("grants search but withholds selection from the read-only audit role", () => {
    renderFindAreas("pm-audit");

    expect(screen.getByTestId("geocoder-query-input")).toBeInTheDocument();
    expect(screen.getByTestId("geocoder-readonly")).toBeInTheDocument();
  });

  it("denies the surface entirely to a role with no listing grant", () => {
    renderFindAreas("ops-lead");

    expect(screen.getByTestId("geocoder-denied")).toBeInTheDocument();
    expect(screen.queryByTestId("geocoder-query-input")).toBeNull();
  });

  it("has authoritative facts and fails closed for unavailable secondary writes", () => {
    renderFindAreas("expansion-manager");
    const detail = within(screen.getByLabelText("Selected HeatZone detail"));
    expect(detail.getByTestId("find-areas-facts")).toHaveTextContent("資料信心");
    for (const name of ["指派找點任務", "建立物件搜尋條件", "加入季度展店計畫", "＋ 從網址新增物件（帶入本區）"]) {
      expect(detail.getByRole("button", { name })).toBeDisabled();
    }
    expect(detail.queryByRole("button", { name: "Submit Review" })).toBeNull();
    expect(detail.getByText(/開啟評分頁不會執行評分或送審/)).toBeInTheDocument();
  });

  it("restores URL hints and navigates to actual Radar and SiteScore tabs", () => {
    navigation.search = "ws=network&tab=areas&hz=HZ-02&lens=fit&flag=a&flag=b";
    renderFindAreas("expansion-manager");
    const detail = within(screen.getByLabelText("Selected HeatZone detail"));
    expect(detail.getByText(/HZ-02 · Lens：品牌適配/)).toBeInTheDocument();
    const lenses = within(screen.getByLabelText("HeatZone lenses"));
    expect(lenses.getByRole("button", { name: "品牌適配" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(lenses.getByRole("button", { name: "競店壓力" }));
    expect(navigation.push).toHaveBeenLastCalledWith(
      "/operator?ws=network&tab=areas&hz=HZ-02&lens=competition&flag=a&flag=b", { scroll: false },
    );
    fireEvent.click(detail.getByTestId("find-areas-primary"));
    expect(navigation.push).toHaveBeenLastCalledWith(
      "/operator?ws=network&tab=radar&hz=HZ-02&lens=fit&flag=a&flag=b", { scroll: false },
    );
  });

  it("labels the in-memory tracking toggle as non-durable", () => {
    renderFindAreas("expansion-manager");
    const detail = within(screen.getByLabelText("Selected HeatZone detail"));
    const track = detail.getByRole("button", { name: /本次工作階段已追蹤/ });
    expect(track).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(track);
    expect(detail.getByRole("button", { name: "加入本次工作階段追蹤" })).toHaveAttribute("aria-pressed", "false");
    expect(detail.getByText(/追蹤僅保留於本次工作階段/)).toBeInTheDocument();
  });

  it("shows no geocode receipt until an action has been taken", () => {
    renderFindAreas("expansion-manager");

    expect(screen.queryByTestId("find-areas-geocode-receipt")).toBeNull();
  });
});
