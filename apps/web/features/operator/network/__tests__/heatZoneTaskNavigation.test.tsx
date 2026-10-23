import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { NetworkFindAreasWorkspace as Workspace } from "../../NetworkFindAreasWorkspace";
import type { OperatorHeatZone } from "../../types";
import { heatZoneTaskHref } from "../heatZoneNavigation";
import {
  heatZoneRequestArgument,
  heatZoneSnapshotReceipt,
  resolveHeatZoneRequestedTarget,
} from "../heatZoneRequestedSnapshot";
import { NETWORK_TAB_IDS } from "../networkUrlState";

// Owner consumer controls for ODP-NETWORK-HEATZONE-LINK-CONSUMER-261018.
// Synthetic contract regressions in production mode, not live acceptance evidence.

const nav = vi.hoisted(() => ({ pathname: "/operator", search: "ws=network&tab=areas", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }),
}));
// The map needs WebGL; the tray and detail carry the selected identity.
vi.mock("../HeatZoneMap", () => ({ HeatZoneMap: () => <div data-testid="heat-zone-map-stub" /> }));
// The original Survey dialog stays the only assignment entry; capture its target.
vi.mock("../SurveyAssignmentDialog", () => ({
  SurveyAssignmentDialog: ({ target }: { target: { kind: string; id: string } }) => (
    <div data-testid="survey-dialog-target" data-kind={target.kind} data-id={target.id} />
  ),
}));

function NetworkFindAreasWorkspace(props: ComponentProps<typeof Workspace>) {
  return <Workspace allowedTabIds={NETWORK_TAB_IDS} createAuthenticatedReadGuard={guard} {...props} />;
}
const guard = (readCurrent: () => boolean) => async () => readCurrent();

const OPAQUE_ID = "HZ/台北?A&B#% 甲";
function zone(id: string, label: string, rank: number): OperatorHeatZone {
  return {
    id, label, rank, centroid: [121.5 + rank / 100, 25.03], demandGap: 0.8, competitionIndex: 0.4,
    cannibalizationRisk: "medium", rentBand: "NT$420k-520k/month", confidence: 0.9, recommendedLens: "demand",
    reasons: ["Recorded reason"], risks: ["Recorded risk"], nextStep: "Recorded next step",
  };
}
// The default zone ranks first: any first-row/default fallback would show it.
const DEFAULT_ZONE = zone("zone-default", "伺服器預設熱區", 1);
const TARGET_ZONE = zone(OPAQUE_ID, "指定熱區甲", 2);
const OTHER_ZONE = zone("zone-b", "另一授權熱區乙", 3);

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}
function snapshot(selectedHeatZoneId: string | undefined, heatZones: OperatorHeatZone[], extra: Record<string, unknown> = {}) {
  return { source: "api", heatZones, selectedHeatZoneId, listingSources: [], listings: [], candidates: [], siteReviews: [], ...extra };
}
type ListingsHandler = (selected: string | null) => Response | Promise<Response>;
function reads(handler: ListingsHandler) {
  const mock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = new URL(String(input), "http://local");
    if (url.pathname.endsWith("/network-listings")) return handler(url.searchParams.get("selectedHeatZoneId"));
    return json({ detail: "denied" }, 403);
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}
function listingArguments(mock: ReturnType<typeof reads>) {
  return mock.mock.calls.flatMap(([input]) => {
    const url = new URL(String(input), "http://local");
    return url.pathname.endsWith("/network-listings") ? [url.searchParams.get("selectedHeatZoneId")] : [];
  });
}
function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}
function exactSearch(id: string) {
  return new URL(heatZoneTaskHref({ workspace: "network", tab: "areas", entityId: id, heatZoneId: id })!, "http://local")
    .searchParams.toString();
}
async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}
function gate() {
  return screen.getByTestId("operator-data-unavailable");
}
function expectNoZoneDetail() {
  expect(screen.queryByTestId("find-areas-zone-detail")).not.toBeInTheDocument();
  expect(screen.queryByTestId("find-areas-zone-actions")).not.toBeInTheDocument();
  expect(screen.queryByTestId("network-panel-find-areas")).not.toBeInTheDocument();
  expect(screen.queryByText(DEFAULT_ZONE.label)).not.toBeInTheDocument();
}

beforeEach(() => {
  nav.pathname = "/operator"; nav.search = "ws=network&tab=areas"; nav.push.mockReset();
  window.history.replaceState(null, "", `/operator?${nav.search}`); window.sessionStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("exact Task Center HeatZone target on Find Areas", () => {
  it("reloads the exact opaque identity as the original API argument and opens only that confirmed zone", async () => {
    nav.search = exactSearch(OPAQUE_ID);
    const fetchMock = reads((selected) => json(snapshot(selected ?? DEFAULT_ZONE.id, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE])));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    const detail = await screen.findByTestId("find-areas-zone-detail");
    expect(detail).toHaveTextContent(TARGET_ZONE.label);
    expect(detail).not.toHaveTextContent(DEFAULT_ZONE.label);
    expect(listingArguments(fetchMock)).toEqual([OPAQUE_ID]);
    expect(screen.getByTestId("find-areas-tray").querySelector(`[aria-current="true"]`)).toHaveAttribute("data-zone-id", OPAQUE_ID);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    // Original Survey entry and HEAT_ZONE target remain the only assignment path.
    fireEvent.click(within(screen.getByTestId("find-areas-zone-actions")).getByRole("button", { name: "指派找點任務" }));
    const survey = await screen.findByTestId("survey-dialog-target");
    expect(survey).toHaveAttribute("data-kind", "HEAT_ZONE");
    expect(survey).toHaveAttribute("data-id", OPAQUE_ID);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it.each([
    ["wrong/default selection", () => json(snapshot(DEFAULT_ZONE.id, [DEFAULT_ZONE, TARGET_ZONE])), "不一致"],
    ["no selected echo", () => json(snapshot(undefined, [DEFAULT_ZONE, TARGET_ZONE])), "不一致"],
    ["missing target row", () => json(snapshot(OPAQUE_ID, [DEFAULT_ZONE, OTHER_ZONE])), "找不到"],
    ["duplicate target rows", () => json(snapshot(OPAQUE_ID, [DEFAULT_ZONE, TARGET_ZONE, { ...TARGET_ZONE, label: "重複列" }])), "多筆"],
    ["non-API source", () => json(snapshot(OPAQUE_ID, [DEFAULT_ZONE, TARGET_ZONE], { source: "fixture" })), null],
  ])("keeps the requested target unavailable on %s without any other zone", async (_name, answer, detail) => {
    nav.search = exactSearch(OPAQUE_ID);
    const fetchMock = reads(answer);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(gate()).toHaveAttribute("data-status", expect.stringMatching(/error|seed/)));
    await settle();
    expectNoZoneDetail();
    expect(screen.queryByTestId("survey-dialog-target")).not.toBeInTheDocument();
    if (detail) expect(screen.getByTestId("operator-data-unavailable-technical")).toHaveTextContent(detail);
    expect(listingArguments(fetchMock)).toEqual([OPAQUE_ID]);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("keeps a currently denied target on the original refusal gate", async () => {
    nav.search = exactSearch(OPAQUE_ID);
    reads(() => json({ detail: "role does not permit view on heat zone" }, 403));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(gate()).toHaveAttribute("data-failure-kind", "forbidden"));
    expectNoZoneDetail();
  });

  it.each([
    "ws=network&tab=areas&heatZone=zone-default",
    "ws=network&tab=areas&entity=zone-default",
    "ws=network&tab=areas&entity=zone-default&heatZone=zone-b",
    "ws=network&tab=areas&entity=%20&heatZone=%20",
    "ws=network&tab=areas&entity=zone-default&heatZone=zone-default&heatZone=zone-b",
    "ws=network&tab=areas&entity=&heatZone=zone-default",
  ])("renders malformed identity %s as invalid and never adopts the server default", async (query) => {
    nav.search = query;
    const fetchMock = reads(() => json(snapshot(DEFAULT_ZONE.id, [DEFAULT_ZONE, OTHER_ZONE])));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(listingArguments(fetchMock).length).toBe(1));
    await settle();
    expect(gate()).toHaveAttribute("data-status", "error");
    expect(screen.getByTestId("operator-data-unavailable-technical")).toHaveTextContent("缺漏、空白或不一致");
    expect(listingArguments(fetchMock)).toEqual([null]);
    expectNoZoneDetail();
  });

  it("refuses a controlled zone prop that conflicts with the explicit request, choosing neither", async () => {
    nav.search = exactSearch(OPAQUE_ID);
    const fetchMock = reads((selected) => json(snapshot(selected ?? DEFAULT_ZONE.id, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE])));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" selectedHeatZoneId={OTHER_ZONE.id} />);
    await waitFor(() => expect(listingArguments(fetchMock).length).toBe(1));
    await settle();
    expect(screen.getByTestId("operator-data-unavailable-technical")).toHaveTextContent("工作台指定的熱區不一致");
    expect(listingArguments(fetchMock)).toEqual([null]);
    expectNoZoneDetail();
    expect(screen.queryByText(OTHER_ZONE.label)).not.toBeInTheDocument();
    // A matching controlled prop is the same exact target.
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" selectedHeatZoneId={OPAQUE_ID} />);
    expect(await screen.findByTestId("find-areas-zone-detail")).toHaveTextContent(TARGET_ZONE.label);
    expect(listingArguments(fetchMock).at(-1)).toBe(OPAQUE_ID);
  });

  it("does not restore an abandoned role's late confirmed response", async () => {
    nav.search = exactSearch(OPAQUE_ID);
    const first = deferred(); let calls = 0;
    reads(() => ++calls === 1 ? first.promise : json({ detail: "new role denied" }, 403));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(calls).toBe(1));
    expect(gate()).toHaveAttribute("data-status", "loading");
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    await waitFor(() => expect(calls).toBe(2));
    await act(async () => first.resolve(json(snapshot(OPAQUE_ID, [TARGET_ZONE]))));
    await settle();
    expectNoZoneDetail();
    expect(screen.queryByText(TARGET_ZONE.label)).not.toBeInTheDocument();
    expect(gate()).toHaveAttribute("data-status", "error");
  });

  it("does not let a late response for the previous URL target restore it after the URL changes", async () => {
    nav.search = exactSearch(OPAQUE_ID);
    const pending = new Map<string | null, ReturnType<typeof deferred>>();
    const fetchMock = reads((selected) => { const next = deferred(); pending.set(selected, next); return next.promise; });
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(pending.has(OPAQUE_ID)).toBe(true));
    nav.search = exactSearch(OTHER_ZONE.id);
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(pending.has(OTHER_ZONE.id)).toBe(true));
    await act(async () => pending.get(OPAQUE_ID)!.resolve(json(snapshot(OPAQUE_ID, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE]))));
    await settle();
    expectNoZoneDetail();
    expect(gate()).toHaveAttribute("data-status", "loading");
    await act(async () => pending.get(OTHER_ZONE.id)!.resolve(json(snapshot(OTHER_ZONE.id, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE]))));
    const detail = await screen.findByTestId("find-areas-zone-detail");
    expect(detail).toHaveTextContent(OTHER_ZONE.label);
    expect(detail).not.toHaveTextContent(TARGET_ZONE.label);
    expect(listingArguments(fetchMock)).toEqual([OPAQUE_ID, OTHER_ZONE.id]);
  });

  it("does not present a confirmed target's earlier snapshot while the next URL's read is in flight", async () => {
    nav.search = exactSearch(OPAQUE_ID);
    let held: ReturnType<typeof deferred> | null = null;
    reads((selected) => {
      if (selected === OPAQUE_ID) return json(snapshot(OPAQUE_ID, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE]));
      held = deferred(); return held.promise;
    });
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    expect(await screen.findByTestId("find-areas-zone-detail")).toHaveTextContent(TARGET_ZONE.label);
    nav.search = exactSearch(OTHER_ZONE.id);
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(held).not.toBeNull());
    expectNoZoneDetail();
    expect(screen.queryByText(TARGET_ZONE.label)).not.toBeInTheDocument();
    expect(gate()).toHaveAttribute("data-status", "loading");
  });

  // ODP-NETWORK-HEATZONE-EPOCH-FIX-261018: returning to a byte-identical URL is
  // a new location intent. The earlier confirmed snapshot answered an older
  // epoch and must stay unavailable until a fresh current read settles.
  const FRESH_TARGET_ZONE = { ...TARGET_ZONE, label: "重新核對熱區丙" };
  it.each([
    ["fresh denial", () => json({ detail: "role does not permit view on heat zone" }, 403), "forbidden"],
    ["fresh absence", () => json(snapshot(OPAQUE_ID, [DEFAULT_ZONE, OTHER_ZONE])), "missing"],
    ["fresh success", () => json(snapshot(OPAQUE_ID, [DEFAULT_ZONE, FRESH_TARGET_ZONE, OTHER_ZONE])), "confirmed"],
  ] as const)("keeps an earlier confirmed target unavailable on Back to the same URL until the fresh read settles (%s)", async (_name, freshAnswer, outcome) => {
    const targetSearch = exactSearch(OPAQUE_ID);
    nav.search = targetSearch;
    const heldOther = deferred(); const heldFresh = deferred(); let targetReads = 0;
    const fetchMock = reads((selected) => {
      if (selected === OTHER_ZONE.id) return heldOther.promise;
      return ++targetReads === 1 ? json(snapshot(OPAQUE_ID, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE])) : heldFresh.promise;
    });
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    // A is currently confirmed with its details and actions.
    expect(await screen.findByTestId("find-areas-zone-detail")).toHaveTextContent(TARGET_ZONE.label);
    expect(screen.getByTestId("find-areas-zone-actions")).toBeInTheDocument();
    // Navigate to B; its read stays pending.
    nav.search = exactSearch(OTHER_ZONE.id);
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(listingArguments(fetchMock)).toEqual([OPAQUE_ID, OTHER_ZONE.id]));
    expectNoZoneDetail();
    expect(gate()).toHaveAttribute("data-status", "loading");
    // Back to the byte-identical A URL before B or a fresh A read settles.
    nav.search = targetSearch;
    expect(nav.search).toBe(exactSearch(OPAQUE_ID));
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(listingArguments(fetchMock)).toEqual([OPAQUE_ID, OTHER_ZONE.id, OPAQUE_ID]));
    await settle();
    expectNoZoneDetail();
    expect(screen.queryByText(TARGET_ZONE.label)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "指派找點任務" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("survey-dialog-target")).not.toBeInTheDocument();
    expect(gate()).toHaveAttribute("data-status", "loading");
    // B's late answer belongs to an abandoned location and restores nothing.
    await act(async () => heldOther.resolve(json(snapshot(OTHER_ZONE.id, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE]))));
    await settle();
    expectNoZoneDetail();
    expect(screen.queryByText(TARGET_ZONE.label)).not.toBeInTheDocument();
    expect(screen.queryByText(OTHER_ZONE.label)).not.toBeInTheDocument();
    expect(gate()).toHaveAttribute("data-status", "loading");
    // Only the fresh current A read decides the outcome.
    await act(async () => heldFresh.resolve(freshAnswer()));
    await settle();
    if (outcome === "confirmed") {
      const detail = await screen.findByTestId("find-areas-zone-detail");
      expect(detail).toHaveTextContent(FRESH_TARGET_ZONE.label);
      expect(detail).not.toHaveTextContent(TARGET_ZONE.label);
      fireEvent.click(within(screen.getByTestId("find-areas-zone-actions")).getByRole("button", { name: "指派找點任務" }));
      const survey = await screen.findByTestId("survey-dialog-target");
      expect(survey).toHaveAttribute("data-kind", "HEAT_ZONE");
      expect(survey).toHaveAttribute("data-id", OPAQUE_ID);
    } else {
      if (outcome === "forbidden") await waitFor(() => expect(gate()).toHaveAttribute("data-failure-kind", "forbidden"));
      else expect(screen.getByTestId("operator-data-unavailable-technical")).toHaveTextContent("找不到");
      expect(gate()).toHaveAttribute("data-status", "error");
      expectNoZoneDetail();
      expect(screen.queryByText(TARGET_ZONE.label)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "指派找點任務" })).not.toBeInTheDocument();
      expect(screen.queryByTestId("survey-dialog-target")).not.toBeInTheDocument();
    }
    expect(listingArguments(fetchMock)).toEqual([OPAQUE_ID, OTHER_ZONE.id, OPAQUE_ID]);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("moves entity and heatZone together when the user selects another authorized zone", async () => {
    nav.search = `${exactSearch(OPAQUE_ID)}&candidate=stale-candidate&zone=stale-zone`;
    const fetchMock = reads((selected) => json(snapshot(selected ?? DEFAULT_ZONE.id, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE])));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    expect(await screen.findByTestId("find-areas-zone-detail")).toHaveTextContent(TARGET_ZONE.label);
    fireEvent.click(screen.getByTestId("find-areas-tray").querySelector(`[data-zone-id="${OTHER_ZONE.id}"]`)!);
    expect(nav.push).toHaveBeenCalledTimes(1);
    const pushed = new URL(String(nav.push.mock.calls[0][0]), "http://local");
    expect(pushed.pathname).toBe("/operator");
    expect(pushed.searchParams.get("ws")).toBe("network");
    expect(pushed.searchParams.get("tab")).toBe("areas");
    expect(pushed.searchParams.getAll("entity")).toEqual([OTHER_ZONE.id]);
    expect(pushed.searchParams.getAll("heatZone")).toEqual([OTHER_ZONE.id]);
    expect(pushed.searchParams.has("candidate")).toBe(false);
    expect(pushed.searchParams.has("zone")).toBe(false);
    // The row stays bound to the current URL identity until navigation lands.
    expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent(TARGET_ZONE.label);
    nav.search = pushed.searchParams.toString();
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent(OTHER_ZONE.label));
    expect(screen.getByTestId("find-areas-tray").querySelector(`[aria-current="true"]`)).toHaveAttribute("data-zone-id", OTHER_ZONE.id);
    expect(listingArguments(fetchMock).at(-1)).toBe(OTHER_ZONE.id);
    // Back restores the earlier exact identity through a fresh read.
    nav.search = exactSearch(OPAQUE_ID);
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent(TARGET_ZONE.label));
    expect(listingArguments(fetchMock).at(-1)).toBe(OPAQUE_ID);
  });

  it("keeps the ordinary visit's original server-default selection and local-only clicks", async () => {
    const fetchMock = reads(() => json(snapshot(OTHER_ZONE.id, [DEFAULT_ZONE, TARGET_ZONE, OTHER_ZONE])));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent(OTHER_ZONE.label));
    expect(listingArguments(fetchMock)[0]).toBeNull();
    fireEvent.click(screen.getByTestId("find-areas-tray").querySelector(`[data-zone-id="${DEFAULT_ZONE.id}"]`)!);
    await waitFor(() => expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent(DEFAULT_ZONE.label));
    expect(nav.push).not.toHaveBeenCalled();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });

  it("leaves the Candidates zone scope and other tabs unaffected by the areas identity", async () => {
    nav.search = `ws=network&tab=candidates&zone=${DEFAULT_ZONE.id}&entity=${OTHER_ZONE.id}&heatZone=${OTHER_ZONE.id}`;
    const fetchMock = reads(() => json(snapshot(DEFAULT_ZONE.id, [DEFAULT_ZONE, OTHER_ZONE])));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(listingArguments(fetchMock).length).toBeGreaterThan(0));
    await settle();
    expect(listingArguments(fetchMock)).not.toContain(OTHER_ZONE.id);
    expect(screen.queryByText(/熱區連結/)).not.toBeInTheDocument();
    expect(screen.getByTestId("network-tab-2")).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByTestId("network-tab-1"));
    const radar = new URL(String(nav.push.mock.calls.at(-1)?.[0]), "http://local");
    expect(radar.searchParams.get("tab")).toBe("radar");
    expect(radar.searchParams.has("zone")).toBe(false);
  });

  it("does not consume the identity when Find Areas is not an allowed tab", async () => {
    nav.search = exactSearch(OPAQUE_ID);
    const fetchMock = reads(() => json(snapshot(DEFAULT_ZONE.id, [DEFAULT_ZONE, TARGET_ZONE])));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" allowedTabIds={["radar"]} />);
    await waitFor(() => expect(listingArguments(fetchMock).length).toBeGreaterThan(0));
    await settle();
    expect(listingArguments(fetchMock)).not.toContain(OPAQUE_ID);
    expect(screen.queryByTestId("find-areas-zone-detail")).not.toBeInTheDocument();
  });
});

describe("requested HeatZone snapshot confirmation", () => {
  const selection = { requested: true, id: "zone-a", invalid: false } as const;
  const receipt = heatZoneSnapshotReceipt(3, "/operator?entity=zone-a&heatZone=zone-a", 2);
  const base = { selection, controlledSelectedId: undefined, loadState: "ready" as const,
    snapshotReceipt: receipt, currentReceipt: receipt,
    snapshot: { source: "api", selectedHeatZoneId: "zone-a", heatZones: [{ id: "zone-b" }, { id: "zone-a" }] } };

  it("derives the original API argument only from an explicit, non-conflicting request", () => {
    expect(heatZoneRequestArgument({ requested: false, id: null, invalid: false }, "prop")).toBeUndefined();
    expect(heatZoneRequestArgument({ requested: true, id: null, invalid: true }, undefined)).toBeNull();
    expect(heatZoneRequestArgument(selection, "zone-b")).toBeNull();
    expect(heatZoneRequestArgument(selection, "zone-a")).toBe("zone-a");
    expect(heatZoneRequestArgument(selection, undefined)).toBe("zone-a");
  });

  it("confirms only the same generation and URL read", () => {
    expect(resolveHeatZoneRequestedTarget(base)).toEqual({ state: "confirmed", id: "zone-a" });
    expect(resolveHeatZoneRequestedTarget({ ...base, currentReceipt: heatZoneSnapshotReceipt(4, "/operator?entity=zone-a&heatZone=zone-a", 2) }))
      .toEqual({ state: "pending", id: "zone-a" });
    expect(resolveHeatZoneRequestedTarget({ ...base, snapshotReceipt: null })).toEqual({ state: "pending", id: "zone-a" });
    expect(resolveHeatZoneRequestedTarget({ ...base, loadState: "loading" })).toEqual({ state: "pending", id: "zone-a" });
  });

  it("does not confirm a same-URL read from an earlier location epoch", () => {
    const url = "/operator?entity=zone-a&heatZone=zone-a";
    expect(heatZoneSnapshotReceipt(3, url, 0)).not.toBe(heatZoneSnapshotReceipt(3, url, 2));
    expect(resolveHeatZoneRequestedTarget({ ...base, snapshotReceipt: heatZoneSnapshotReceipt(3, url, 0) }))
      .toEqual({ state: "pending", id: "zone-a" });
  });

  it.each([
    [{ loadState: "error" as const, snapshot: null }, "read"],
    [{ loadState: "seed" as const }, "read"],
    [{ loadState: "empty" as const }, "missing"],
    [{ snapshot: { ...base.snapshot, heatZoneAvailability: { state: "unavailable" } } }, "read"],
    [{ snapshot: { ...base.snapshot, source: "fixture" } }, "source"],
    [{ snapshot: { ...base.snapshot, selectedHeatZoneId: "zone-b" } }, "mismatch"],
    [{ snapshot: { ...base.snapshot, heatZones: [{ id: "zone-b" }] } }, "missing"],
    [{ snapshot: { ...base.snapshot, heatZones: null } }, "missing"],
    [{ snapshot: { ...base.snapshot, heatZones: [{ id: "zone-a" }, { id: "zone-a" }] } }, "duplicate"],
  ])("keeps %j unavailable as %s", (override, reason) => {
    expect(resolveHeatZoneRequestedTarget({ ...base, ...override })).toEqual({ state: "unavailable", id: "zone-a", reason });
  });

  it("reports no intent, invalid and conflict before reading the snapshot", () => {
    expect(resolveHeatZoneRequestedTarget({ ...base, selection: { requested: false, id: null, invalid: false } })).toEqual({ state: "none" });
    expect(resolveHeatZoneRequestedTarget({ ...base, selection: { requested: true, id: null, invalid: true } })).toEqual({ state: "invalid" });
    expect(resolveHeatZoneRequestedTarget({ ...base, controlledSelectedId: "zone-b" })).toEqual({ state: "conflict", id: "zone-a" });
  });
});
