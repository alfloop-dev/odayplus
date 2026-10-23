import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace as Workspace } from "../../NetworkFindAreasWorkspace";
import { NETWORK_TAB_IDS } from "../networkUrlState";

// Explicit synthetic bootstrap tabs for these component controls. Backend
// scope/role denials and read revalidation assertions remain authoritative.
function NetworkFindAreasWorkspace(props: Parameters<typeof Workspace>[0]) {
  return <Workspace allowedTabIds={NETWORK_TAB_IDS} {...props} />;
}
import type { Candidate } from "../../types";

const nav = vi.hoisted(() => ({ pathname: "/operator", search: "ws=network&tab=radar", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname, useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }),
}));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({
  ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div />,
}));

// Synthetic test responses with recorded relationships; these are not live acceptance evidence.
const listingId = "6e7c4cc6-840a-451b-a54b-8d56a5930201";
const intakeId = "6e7c4cc6-840a-451b-a54b-8d56a5930202";
const candidateId = "6e7c4cc6-840a-451b-a54b-8d56a5930203";
const listing = { id: listingId, sourceId: "recorded-source", heatZoneId: null, address: "Recorded location", status: "new",
  areaPing: 25, rentPerMonth: 30000, hardRuleFailures: null, geocodeConfidence: null };
const intake = { id: intakeId, stage: "READY", matchResult: { targetListingId: listingId, outcome: "NEW", outcomeLabel: "New", confidence: 1,
  agreeingSignals: [], contradictingSignals: [], summary: "Recorded test relation" } };
const networkCandidate: Candidate = {
  id: candidateId, listingId, heatZoneId: "recorded-zone", title: "Requested candidate",
  address: "Recorded location", status: "new", score: null, recommendation: null,
  modelVersion: null, datasetSnapshotId: null, missingData: ["Required evidence"],
};
const network = (candidate = false) => ({ source: "api", heatZones: [], listingSources: [], siteReviews: [], expansionSteps: [],
  listings: [listing], candidates: candidate ? [networkCandidate] : [], assistedIntakes: [intake] });
const scoring = (parent = listingId) => ({ source: "api", candidates: [{ id: candidateId, listingId: parent, heatZoneId: "recorded-zone",
  title: "Requested candidate", zoneLabel: "Area", address: "Recorded location", modelVersion: "", datasetSnapshotId: "", stage: "new",
  scored: false, score: null, recommendation: null, inCompare: false,
  gate: { state: "blocked", passed: false, missing: ["Required evidence"], otherMissing: [], checks: [], okCount: 0, totalCount: 1, blockNote: "" } }],
  scorecards: [], compare: { columns: [], metrics: [], recommendation: null, empty: true }, compareSet: [] });
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function reads(readListing: () => Response | Promise<Response>, readScoring: () => Response | Promise<Response> = () => json(scoring())) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("network-listings?")) return readListing();
    if (url.includes("network-scoring")) return readScoring();
    return json({ detail: "not authorized for this synthetic navigation test" }, 403);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  nav.pathname = "/operator";
  nav.search = "ws=network&tab=radar";
  nav.push.mockReset();
  window.history.replaceState(null, "", `/operator?${nav.search}`);
  window.sessionStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function clickListing() {
  const action = await screen.findByTestId(`listing-navigate-${listingId}`);
  await waitFor(() => expect(action).toBeEnabled());
  fireEvent.click(action);
}

describe("Radar navigation revalidates current reads and never writes", () => {
  it("re-reads the exact relation before opening the existing durable intake", async () => {
    let count = 0;
    const fetchMock = reads(() => { count += 1; return json(network()); });
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    await waitFor(() => expect(nav.push).toHaveBeenCalledTimes(1));
    expect(count).toBe(2);
    expect(nav.push.mock.calls[0][0]).toContain(`/intake/${intakeId}?`);
    const href = new URL(nav.push.mock.calls[0][0], "https://operator.example.test");
    expect(href.searchParams.get("selected")).toBe(intakeId);
    expect(href.searchParams.get("dialog")).toBe("detail");
    expect(fetchMock.mock.calls.every(([, init]) => !init || !init.method || init.method === "GET")).toBe(true);
  });

  it("stops when the relation disappears during the click re-read", async () => {
    let count = 0;
    reads(() => json(++count === 1 ? network() : { ...network(), assistedIntakes: [] }));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    expect(await screen.findByRole("alert")).toHaveTextContent("關聯已變更");
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("keeps a refused re-read as a read error rather than an empty authorized collection", async () => {
    let count = 0;
    reads(() => ++count === 1 ? json(network()) : json({ detail: "View denied" }, 403));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "error");
    expect(screen.queryByText("No listings match the selected filter")).not.toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("requires both candidate ID and listing ID to agree with the separate scoring read", async () => {
    reads(() => json(network(true)), () => json(scoring("different-parent")));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    expect(await screen.findByRole("alert")).toHaveTextContent("指定候選點目前無法讀取");
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("navigates to the exact candidate and clears stale intake context without POST convert", async () => {
    nav.search += "&zone=old-zone&receipt=old-receipt";
    const fetchMock = reads(() => json(network(true)));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    await waitFor(() => expect(nav.push).toHaveBeenCalledTimes(1));
    const href = new URL(nav.push.mock.calls[0][0], "https://operator.example.test");
    expect(href.searchParams.get("tab")).toBe("candidates");
    expect(href.searchParams.get("candidate")).toBe(candidateId);
    expect(href.searchParams.has("zone")).toBe(false);
    expect(href.searchParams.has("receipt")).toBe(false);
    expect(screen.getByTestId(`candidate-detail-score-${candidateId}`)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/convert"))).toBe(false);
  });

  it("discards a pending click read after a role change, including a return to the old role", async () => {
    let count = 0;
    let release!: (response: Response) => void;
    reads(() => ++count === 2 ? new Promise<Response>((resolve) => { release = resolve; }) : json(network()));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    await waitFor(() => expect(count).toBe(2));
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    await waitFor(() => expect(count).toBe(3));
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(count).toBe(4));
    await act(async () => { release(json(network())); });
    expect(nav.push).not.toHaveBeenCalled();
  });
});


describe("Radar navigation lifetime and read ordering", () => {
  it("does not reclaim navigation after the user changes tabs during a pending read", async () => {
    let count = 0;
    let release!: (response: Response) => void;
    reads(() => ++count === 2 ? new Promise<Response>((resolve) => { release = resolve; }) : json(network()));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    await waitFor(() => expect(count).toBe(2));
    fireEvent.click(screen.getByTestId("network-tab-0"));
    expect(nav.push).toHaveBeenCalledTimes(1);
    await act(async () => { release(json(network())); });
    expect(nav.push).toHaveBeenCalledTimes(1);
    expect(String(nav.push.mock.calls[0][0])).not.toContain(`/intake/${intakeId}`);
  });

  it("does not navigate after the originating workspace unmounts", async () => {
    let count = 0;
    let release!: (response: Response) => void;
    reads(() => ++count === 2 ? new Promise<Response>((resolve) => { release = resolve; }) : json(network()));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    await waitFor(() => expect(count).toBe(2));
    view.unmount();
    await act(async () => { release(json(network())); });
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("leaves a durable intake pathname when navigating its Radar to an existing candidate", async () => {
    nav.pathname = `/intake/${intakeId}`;
    nav.search = `selected=${intakeId}&dialog=detail`;
    reads(() => json(network(true)));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    await waitFor(() => expect(nav.push).toHaveBeenCalledTimes(1));
    const href = new URL(nav.push.mock.calls[0][0], "https://operator.example.test");
    expect(href.pathname).toBe("/operator");
    expect(href.searchParams.get("ws")).toBe("network");
    expect(href.searchParams.get("candidate")).toBe(candidateId);
    expect(href.searchParams.has("selected")).toBe(false);
  });

  it("withholds the previous principal's listing body while the new role read is pending", async () => {
    let count = 0;
    reads(() => ++count === 1 ? json(network()) : new Promise<Response>(() => undefined));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId(`listing-row-${listingId}`);
    await act(async () => {
      view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    });
    expect(screen.queryByTestId(`listing-row-${listingId}`)).not.toBeInTheDocument();
    expect(screen.queryByText("Recorded location")).not.toBeInTheDocument();
  });

  it("does not let an older scoring success overwrite the newer click-time denial", async () => {
    let scoreReads = 0;
    let release!: (response: Response) => void;
    reads(() => json(network(true)), () => ++scoreReads === 1
      ? new Promise<Response>((resolve) => { release = resolve; })
      : json({ detail: "Current candidate read denied" }, 403));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await clickListing();
    expect(await screen.findByRole("alert")).toHaveTextContent("指定候選點目前無法讀取");
    await act(async () => { release(json(scoring())); });
    fireEvent.click(screen.getByTestId("network-tab-2"));
    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "error");
    expect(screen.queryByTestId("network-panel-candidates")).not.toBeInTheDocument();
  });
});
