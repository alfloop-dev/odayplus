import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OperatorConsole } from "../OperatorConsole";
import { parseHeatZoneSelection } from "../network/heatZoneNavigation";
import { parseNetworkTabIndex } from "../network/networkUrlState";

// The Find Areas consumer has its own guarded-read tests. Here only the
// Console's central URL writer is under test, so the workspace is a marker.
vi.mock("../NetworkFindAreasWorkspace", () => ({
  NetworkFindAreasWorkspace: () => <div data-testid="network-find-areas-workspace" />,
}));

type Mode = "fixture" | "authenticated";
type Role = { id: string; label: string; subtitle: string; allowedWorkspaces: string[]; heroName: string };

// Exact opaque IDs from the existing Task Center producer tests, including
// characters that must survive URL encoding unchanged.
const ZONE_IDS = ["zone-a", "HZ/台北?A&B#%"] as const;
const PLANNER_A: Role = { id: "expansion-manager", label: "Planner A", subtitle: "First desk",
  allowedWorkspaces: ["today", "network"], heroName: "Planner A" };
const PLANNER_B: Role = { id: "expansion-staff", label: "Planner B", subtitle: "Second desk",
  allowedWorkspaces: ["today", "network"], heroName: "Planner B" };
const TODAY_ONLY: Role = { id: "pm-audit", label: "Auditor C", subtitle: "Today desk",
  allowedWorkspaces: ["today"], heroName: "Auditor C" };
const NO_REQUEST = { requested: false, id: null, invalid: false };
const initialHref = window.location.href;

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs();
  sessionStorage.clear(); window.history.replaceState(null, "", initialHref);
});

/** Legal paired URL exactly as the existing Task Center producer emits it. */
function pairedSearch(zoneId: string) {
  return `?${new URLSearchParams({ ws: "network", tab: "areas", entity: zoneId, heatZone: zoneId })}`;
}

/** What the unchanged strict Find Areas parser concludes from the current URL. */
function currentHeatZoneSelection() {
  const params = new URLSearchParams(window.location.search);
  return parseHeatZoneSelection(params, parseNetworkTabIndex(params));
}

function currentParams() {
  return new URL(window.location.href).searchParams;
}

function envelope(mode: Mode, active: Role, roles: Role[]) {
  const authenticated = mode === "authenticated";
  const allowedWorkspaces = active.allowedWorkspaces;
  return {
    meta: { source: authenticated ? "operator-shell-production" : "operator-shell-api-envelope",
      dataMode: authenticated ? "live" : "fixture", role: active, counts: {} },
    navigation: { roles, allowedWorkspaces,
      workspaces: ["today", "network"].map(id => ({ id, label: id === "network" ? "Network" : "Today",
        shortLabel: id, description: `${id} workspace`, allowed: allowedWorkspaces.includes(id) })) },
    today: { hero: { name: active.heroName, roleLabel: active.label, scope: "Local", dateLabel: "Test" },
      kpis: [{ label: "Component test KPI", value: "1" }], queue: [], decisions: [], riskRows: [], auditFeed: [] },
    header: { counts: {} }, approvals: [], auditFeed: [], decisions: [], kpis: [], notifications: [],
    riskRows: [], search: { count: 0, items: [] }, workQueue: [],
  };
}

/** Existing legal auth/envelope fixture: canonical session only in authenticated mode. */
function stubShell(mode: Mode, roles: Role[]) {
  const authenticated = mode === "authenticated";
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", authenticated ? "true" : "false");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === "/auth/session") {
      return authenticated
        ? new Response(JSON.stringify({ subject: "unit-current-planner", expiresAt: Math.floor(Date.now() / 1000) + 3600 }), { status: 200 })
        : new Response(JSON.stringify({ authenticated: false }), { status: 401 });
    }
    const persona = new Headers(init?.headers).get("X-Operator-Role");
    const active = roles.find(role => role.id === persona) ?? roles[0];
    return new Response(JSON.stringify(envelope(mode, active, roles)), { status: 200 });
  }));
}

async function renderOnPairedHeatZone(mode: Mode, zoneId: string, roles: Role[] = [PLANNER_A, PLANNER_B]) {
  sessionStorage.setItem("oday.operator.role", roles[0].id);
  stubShell(mode, roles);
  window.history.replaceState(null, "", `/operator${pairedSearch(zoneId)}`);
  // Precondition: the original paired link is a valid exact HeatZone request.
  expect(currentHeatZoneSelection()).toEqual({ requested: true, id: zoneId, invalid: false });
  render(<OperatorConsole searchParams={{ ws: "network", tab: "areas", entity: zoneId, heatZone: zoneId }} />);
  expect(await screen.findByTestId("network-find-areas-workspace")).toBeInTheDocument();
  expect(window.location.search).toBe(pairedSearch(zoneId));
}

function expectNoHeatZoneIdentity(workspace: "network" | "today") {
  const params = currentParams();
  expect(params.get("ws")).toBe(workspace);
  expect(params.get("tab")).toBe("overview");
  expect(params.getAll("heatZone")).toEqual([]);
  expect(params.getAll("entity")).toEqual([]);
  // `overview` maps back to Find Areas; the strict parser must see no request,
  // not a half pair it has to refuse.
  expect(currentHeatZoneSelection()).toEqual(NO_REQUEST);
}

async function traverse(direction: "back" | "forward") {
  await act(async () => {
    const observed = new Promise<void>((resolve) => window.addEventListener("popstate", () => resolve(), { once: true }));
    window.history[direction]();
    await observed;
  });
}

const MODES = ["fixture", "authenticated"] as const;
const MATRIX = MODES.flatMap(mode => ZONE_IDS.map(zoneId => [mode, zoneId] as const));

it.each(MATRIX)("Network Home exits a paired HeatZone link to the ordinary no-request state (%s, %s)", async (mode, zoneId) => {
  await renderOnPairedHeatZone(mode, zoneId);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Network/ })); });
  expectNoHeatZoneIdentity("network");
  expect(screen.getByTestId("network-find-areas-workspace")).toBeInTheDocument();

  // History keeps the exact original identity; Forward returns to the exit.
  await traverse("back");
  expect(window.location.search).toBe(pairedSearch(zoneId));
  expect(currentHeatZoneSelection()).toEqual({ requested: true, id: zoneId, invalid: false });
  await traverse("forward");
  expectNoHeatZoneIdentity("network");
});

it.each(MATRIX)("leaving Network for Today and returning carries no obsolete HeatZone identity (%s, %s)", async (mode, zoneId) => {
  await renderOnPairedHeatZone(mode, zoneId);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Today/ })); });
  expectNoHeatZoneIdentity("today");
  await waitFor(() => expect(screen.queryByTestId("network-find-areas-workspace")).toBeNull());
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Network/ })); });
  expectNoHeatZoneIdentity("network");
  expect(await screen.findByTestId("network-find-areas-workspace")).toBeInTheDocument();
});

it.each(MATRIX)("a role switch that keeps Network clears the obsolete HeatZone identity (%s, %s)", async (mode, zoneId) => {
  await renderOnPairedHeatZone(mode, zoneId);
  fireEvent.click(screen.getByTitle("Planner A"));
  const option = await screen.findByRole("button", { name: /Planner B\s*Second desk/ });
  await act(async () => { fireEvent.click(option); });
  expect(sessionStorage.getItem("oday.operator.role")).toBe(PLANNER_B.id);
  expectNoHeatZoneIdentity("network");
});

it.each(ZONE_IDS)("a role switch that falls back to the default workspace clears the obsolete HeatZone identity (%s)", async (zoneId) => {
  // Fixture personas keep their own grants, so the next role can lack Network.
  await renderOnPairedHeatZone("fixture", zoneId, [PLANNER_A, TODAY_ONLY]);
  fireEvent.click(screen.getByTitle("Planner A"));
  const option = await screen.findByRole("button", { name: /Auditor C\s*Today desk/ });
  await act(async () => { fireEvent.click(option); });
  expect(sessionStorage.getItem("oday.operator.role")).toBe(TODAY_ONLY.id);
  expectNoHeatZoneIdentity("today");
  await waitFor(() => expect(screen.queryByTestId("network-find-areas-workspace")).toBeNull());
});
