import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace as GrantedReadNetworkWorkspace } from "../../NetworkFindAreasWorkspace";
import samples from "./fixtures/canonicalReviewDecision.json";

// Explicit read-grant fixture: shell projects listing and SiteScore read families.
// This is a renderer premise, not a real Principal/production permission grant.
const NETWORK_READ_FIXTURE_TABS = ["areas", "radar", "candidates", "score", "compare", "review"] as const;
function NetworkFindAreasWorkspace(props: import("../../NetworkFindAreasWorkspace").NetworkFindAreasWorkspaceProps) {
  return <GrantedReadNetworkWorkspace allowedTabIds={NETWORK_READ_FIXTURE_TABS} {...props} />;
}


const navigation = vi.hoisted(() => ({ search: "ws=network&tab=review", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/operator",
  useSearchParams: () => new URLSearchParams(navigation.search),
  useRouter: () => ({ push: navigation.push }),
}));
// The DOM has no WebGL; the real workspace and ReviewPanel remain mounted.
vi.mock("../HeatZoneMap", () => ({ HeatZoneMap: () => null }));
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json" },
});

// These are read-only renderer controls using declared producer samples and an
// explicit session adapter. No real Principal or accepted native write is proven.
function mount(source: "api" | "canonical", role: "expansion-manager" | "ops-lead" = "expansion-manager") {
  const sample = samples.GO;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") return json({ detail: "No business POST is admitted by this renderer control" }, 403);
    if (url === "/auth/session") return json({ subject: sample.context.actorSubjectId, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
    if (url.endsWith("/network-reviews")) return json({ ...sample.before, source });
    if (url.includes("network-listings")) return json(sample.network);
    if (url.endsWith("/network-scoring")) return json(sample.scoring);
    if (url.endsWith("/network-rebalance")) return json({ source: "api", stores: [] });
    return json({ detail: "Unrelated read unavailable" }, 403);
  });
  vi.stubGlobal("fetch", fetchMock);
  const confirmed = vi.fn();
  render(<NetworkFindAreasWorkspace activeRoleId={role} initialTabId="review" onReviewDecisionConfirmed={confirmed} />);
  return { fetchMock, confirmed, id: sample.decisionId };
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/operator?ws=network&tab=review");
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  navigation.push.mockReset();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("real ReviewPanel role eligibility and native decision admission", () => {
  it("production recorded legacy row stays readable without a decision entry or business POST", async () => {
    const run = mount("api");
    await screen.findByTestId(`review-card-${run.id}`);
    expect(screen.queryByTestId(`review-report-note-${run.id}`)).not.toBeInTheDocument();
    for (const action of ["go", "wait", "return", "reject"]) {
      expect(screen.queryByTestId(`review-btn-${action}-${run.id}`)).not.toBeInTheDocument();
    }
    expect(screen.getByTestId(`review-role-note-${run.id}`)).toBeVisible();
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
    expect(run.fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(run.confirmed).not.toHaveBeenCalled();
  });

  it("explicit fixture compatibility retains a recorded legacy decision dialog without claiming a write", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    const run = mount("api");
    await screen.findByTestId(`review-card-${run.id}`);
    fireEvent.click(screen.getByTestId(`review-btn-go-${run.id}`));
    await screen.findByTestId("review-decision-dialog");
    expect(run.fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(run.confirmed).not.toHaveBeenCalled();
  });

  it("canonical source retains the eligible role's native entry without asserting native acceptance", async () => {
    const run = mount("canonical");
    await waitFor(() => expect(screen.getByTestId(`review-btn-go-${run.id}`)).toBeEnabled());
    expect(run.fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(run.confirmed).not.toHaveBeenCalled();
  });

  it("canonical source does not grant an ineligible role decision authority", async () => {
    const run = mount("canonical", "ops-lead");
    await screen.findByTestId(`review-card-${run.id}`);
    for (const action of ["go", "wait", "return", "reject"]) {
      expect(screen.queryByTestId(`review-btn-${action}-${run.id}`)).not.toBeInTheDocument();
    }
    expect(run.fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(run.confirmed).not.toHaveBeenCalled();
  });
});

describe("real ReviewPanel read-only explanations", () => {
  it("eligible production legacy read identifies its unverified workflow without recommending role promotion", async () => {
    const run = mount("api");
    await screen.findByTestId(`review-card-${run.id}`);
    const note = screen.getByTestId(`review-role-note-${run.id}`);
    expect(note).toHaveTextContent("尚未與正式審核流程核對");
    expect(note).toHaveTextContent("目前僅供查閱");
    expect(note).toHaveTextContent("請從 Today 開啟對應的候選點審核");
    expect(note).not.toHaveTextContent("請切換至授權審核角色");
    expect(screen.queryByTestId(`review-report-note-${run.id}`)).not.toBeInTheDocument();
    for (const action of ["go", "wait", "return", "reject"]) {
      expect(screen.queryByTestId(`review-btn-${action}-${run.id}`)).not.toBeInTheDocument();
    }
    expect(run.fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(run.confirmed).not.toHaveBeenCalled();
  });

  it.each(["api", "canonical"] as const)("genuinely ineligible %s read retains the original role-denial explanation", async (source) => {
    const run = mount(source, "ops-lead");
    await screen.findByTestId(`review-card-${run.id}`);
    const note = screen.getByTestId(`review-role-note-${run.id}`);
    expect(note).toHaveTextContent("目前角色可準備／送審，但不能決策。請切換至授權審核角色（Site Reviewer）後再進行核決。");
    expect(note).not.toHaveTextContent("尚未與正式審核流程核對");
    for (const action of ["go", "wait", "return", "reject"]) {
      expect(screen.queryByTestId(`review-btn-${action}-${run.id}`)).not.toBeInTheDocument();
    }
    expect(run.fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(run.confirmed).not.toHaveBeenCalled();
  });
});
