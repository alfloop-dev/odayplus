import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiBinding } from "../../../../src/lib/api/binding";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import type { Candidate, OperatorHeatZone } from "../../types";
import { ExpansionStepper } from "../ExpansionStepper";

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

function renderFindAreas(activeRoleId: "expansion-manager" | "ops-lead" = "expansion-manager") {
  return render(
    <NetworkFindAreasWorkspace
      activeRoleId={activeRoleId}
      liveCandidates={unavailableBinding<Candidate>()}
      liveHeatZones={unavailableBinding<OperatorHeatZone>()}
    />,
  );
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
    expect(actions).toHaveLength(6);
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
    [/^查看本區候選點/, "candidates"],
  ])("routes %s to the %s tab", (name, tab) => {
    renderFindAreas();
    fireEvent.click(screen.getByRole("button", { name }));
    expect(new URL(String(navigation.push.mock.calls.at(-1)?.[0]), "http://local").searchParams.get("tab")).toBe(tab);
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
