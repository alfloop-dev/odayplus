import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole, operatorBootstrapRetryPolicy } from "../OperatorConsole";

// Fully mounted lazy Network reads need the same Next navigation premise as
// existing workspace renderer tests. This supplies routing, never authority.
vi.mock("next/navigation", () => ({
  usePathname: () => window.location.pathname || "/operator",
  useSearchParams: () => new URLSearchParams(window.location.search),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

// Unit DOM has no WebGL. Only the leaf map renderer is replaced; Network
// transport, rows, session checks, roles and every assertion stay real to this
// mounted test. Genuine browser acceptance uses the actual MapLibre component.
vi.mock("../network/HeatZoneMap", () => ({ HeatZoneMap: () => null }));

// Renderer/transport controls. Actual local cookie→BFF→API probes separately
// establish A102/B101 no-hint200/ops-hint403/expansion-hint200. Complete success
// bootstrap body was not retained, so these full envelopes are synthetic UI
// fixtures, not a signed or actual browser acceptance claim.
const A = "00000000-0000-0000-0000-000000000102";
const B = "00000000-0000-0000-0000-000000000101";
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function envelope(role: "expansion-manager" | "ops-lead", name: string, decision = false) {
  const label = role === "expansion-manager" ? "展店經理" : "營運主管";
  const workspaces = role === "expansion-manager" ? ["today", "network"] : ["today", "store"];
  return { meta: { source: "operator-shell-production", dataMode: "live", role: { id: role, label, subtitle: "Unit transport", allowedWorkspaces: workspaces, heroName: name }, counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 1 } },
    navigation: { roles: [{ id: "ops-lead", label: "營運主管", subtitle: "Catalog, not a grant", allowedWorkspaces: ["today", "store"] },
      { id: "expansion-manager", label: "展店經理", subtitle: "Catalog, not a grant", allowedWorkspaces: ["today", "network"] }],
      workspaces: [{ id: "today", label: "Today", shortLabel: "Today", description: "Current scope", allowed: true }], allowedWorkspaces: workspaces, allowedTabs: { network: [] as string[] } },
    header: { counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 1 } },
    today: { hero: { name, roleLabel: label, scope: "Unit current account", dateLabel: "2026-10-13" }, kpis: [{ label: `${name} current row`, value: "1" }], queue: [], decisions: decision ? [{ id: "SYNTHETIC-APR", title: "Unit refresh trigger", meta: "Synthetic UI transport premise, not approval authority", status: "pending", cta: "Unit refresh", tone: "info", target: { workspace: "govern", entityId: "SYNTHETIC-APR", tab: "approvals" } }] : [], riskRows: [], auditFeed: [] },
    notifications: [], search: { count: 0, items: [] } };
}
const delays = [...operatorBootstrapRetryPolicy.delaysMs];
beforeEach(() => { window.sessionStorage.clear(); operatorBootstrapRetryPolicy.delaysMs = delays.map(() => 0); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); });
afterEach(() => { cleanup(); operatorBootstrapRetryPolicy.delaysMs = [...delays]; vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });
type Config = { actor?: string; role?: "expansion-manager" | "ops-lead"; storedRole?: string; session?: unknown;
  firstBootstrap?: Response | Promise<Response>; refresh?: Response | Promise<Response>; decision?: boolean; tasks?: (actor: string) => Response | Promise<Response>; legacy?: (path: string, actor: string) => Response | Promise<Response>; network?: (path: string, actor: string) => Response | Promise<Response>; workspace?: string; tab?: string; issues?: boolean; sessionStatus?: number; invalidRole?: string; denied?: number };
function mount(config: Config = {}) {
  let actor = config.actor ?? A;
  let role = config.role ?? "expansion-manager";
  let boots = 0;
  let sessionOverride = config.session;
  let sessionStatus = config.sessionStatus ?? 200;
  if (config.storedRole) window.sessionStorage.setItem("oday.operator.role", config.storedRole);
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/auth/session") return json(sessionOverride ?? { subject: actor, expiresAt: Math.floor(Date.now() / 1000) + 3600 }, sessionStatus);
    if (url === "/api/v1/operator/bootstrap") {
      boots += 1;
      if (boots === 1 && config.firstBootstrap) return config.firstBootstrap;
      if (boots === 2 && config.refresh) return config.refresh;
      const hint = new Headers(init?.headers).get("X-Operator-Role");
      if (config.denied || hint && hint !== role) return json({ detail: "requested Operator Console role is outside principal roles" }, config.denied ?? 403);
      const body = envelope(role, actor === A ? "Account A" : actor === B ? "Account B" : actor === "unit-operations-account" ? "Operations account" : actor, config.decision && actor === A);
      body.navigation.allowedTabs.network = config.network ? ["radar"] : [];
      if (config.invalidRole) body.meta.role.id = config.invalidRole as typeof role;
      if (config.issues) Object.assign(body, { issues: [{ id: "UNIT-ISSUE", title: "Prior API issue marker", storeName: "Unit store", summary: "Prior API record", severity: "high" }] });
      return json(body);
    }
    if (url === "/api/v1/operator/approvals/SYNTHETIC-APR/decision" && config.decision) return json({ protocolPremiseOnly: true });
    if (url === "/api/v1/operator/shell/tasks" && config.tasks) return config.tasks(actor);
    if (config.network && new URL(url, window.location.origin).pathname === "/api/v1/operator/network-listings") return config.network(new URL(url, window.location.origin).pathname, actor);
    if (config.legacy && ["/api/v1/heatzones", "/api/v1/listings/candidates", "/api/v1/sitescore/reports"].includes(new URL(url, window.location.origin).pathname)) return config.legacy(new URL(url, window.location.origin).pathname, actor);
    return json({ detail: "unrelated read unavailable" }, 403);
  });
  vi.stubGlobal("fetch", fetch);
  const view = render(<OperatorConsole searchParams={{ ws: config.workspace ?? "today", ...(config.tab ? { tab: config.tab } : {}) }} />);
  return { fetch, view, setAccount: (next: string, nextRole = role) => { actor = next; role = nextRole; }, setSession: (value: unknown, status = 200) => { sessionOverride = value; sessionStatus = status; }, boots: () => boots };
}
function networkSnapshot(address?: string) {
  return { source: "api", selectedHeatZoneId: null, heatZones: [], listingSources: [{ id: "synthetic-source", name: "Synthetic source", status: "connected" }], candidates: [], siteReviews: [],
    listings: address ? [{ id: "OLD-A", sourceId: "synthetic-source", address, status: "new" }] : [], rebalanceStores: [], assistedIntakes: [], expansionSteps: [] };
}
function bootstrapCalls(fetch: ReturnType<typeof vi.fn>) { return fetch.mock.calls.filter(([url]) => String(url) === "/api/v1/operator/bootstrap"); }

describe("authenticated bootstrap persona ownership", () => {
  it.each([undefined, "ops-lead", "pm-audit"])("cold open ignores unverified %s preference and adopts actual server persona", async (storedRole) => {
    const { fetch } = mount({ storedRole });
    await screen.findByText("Account A current row");
    expect(new Headers(bootstrapCalls(fetch)[0][1]?.headers).has("X-Operator-Role")).toBe(false);
    expect(screen.getByRole("button", { name: /展店經理/ })).toBeVisible();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });
  it("a legitimate operations account still resolves operations without a default caller hint", async () => {
    const { fetch } = mount({ actor: "unit-operations-account", role: "ops-lead", storedRole: "expansion-manager" });
    await screen.findByText("Operations account current row");
    expect(new Headers(bootstrapCalls(fetch)[0][1]?.headers).has("X-Operator-Role")).toBe(false);
    expect(screen.getByRole("button", { name: /營運主管/ })).toBeVisible();
  });
  it("reload cannot promote a stale stored role into authority", async () => {
    const first = mount(); await screen.findByText("Account A current row"); first.view.unmount();
    window.sessionStorage.setItem("oday.operator.role", "ops-lead");
    const second = mount(); await screen.findByText("Account A current row");
    expect(new Headers(bootstrapCalls(second.fetch)[0][1]?.headers).has("X-Operator-Role")).toBe(false);
  });
  it("an explicitly selected unsupported role remains a 403 and clears old account rows", async () => {
    const { fetch } = mount(); await screen.findByText("Account A current row");
    fireEvent.click(screen.getByRole("button", { name: /展店經理/ }));
    fireEvent.click(screen.getByRole("button", { name: /^營運主管/ }));
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-failure-kind", "forbidden"));
    expect(bootstrapCalls(fetch).some(([, init]) => new Headers(init?.headers).get("X-Operator-Role") === "ops-lead")).toBe(true);
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
  });
  it.each([{ subject: "", expiresAt: 9999999999 }, { subject: A, expiresAt: 1 }, {}, { subject: A, expiresAt: "future" }])(
    "invalid or expired session cannot admit API rows", async (session) => {
      const { fetch } = mount({ session });
      await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
      expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
      expect(bootstrapCalls(fetch)).toHaveLength(0);
    });
  it.each([{ subject: A, expiresAt: 1 }, { subject: "", expiresAt: 9999999999 }, {}])("POC session200 remains fail-closed when canonical session is invalid", async (session) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    const { fetch } = mount({ session });
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(bootstrapCalls(fetch)).toHaveLength(0);
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("開發／驗收 API 資料")).not.toBeInTheDocument();
  });
  it("unknown server persona cannot normalize into a ready operations profile", async () => {
    mount({ invalidRole: "unknown-server-role" });
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
  });
  it("discards late A JSON after B takes over and restarts without A's persona constraint", async () => {
    const body = deferred<unknown>(); const reading = deferred<boolean>();
    const first = json({}); first.json = () => { reading.resolve(true); return body.promise; };
    const { fetch, setAccount } = mount({ storedRole: "expansion-manager", firstBootstrap: first });
    await reading.promise; setAccount(B);
    await act(async () => { body.resolve(envelope("expansion-manager", "Account A")); });
    await screen.findByText("Account B current row");
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    const calls = bootstrapCalls(fetch);
    expect(new Headers(calls[1][1]?.headers).has("X-Operator-Role")).toBe(false);
  });
  it("a superseded response cannot erase current B rows after reset", async () => {
    const pending = deferred<Response>();
    const { fetch, setAccount } = mount({ storedRole: "expansion-manager", firstBootstrap: pending.promise });
    await waitFor(() => expect(bootstrapCalls(fetch)).toHaveLength(1));
    setAccount(B); fireEvent.click(screen.getByRole("button", { name: "重設視角" }));
    await screen.findByText("Account B current row");
    await act(async () => { pending.resolve(json(envelope("expansion-manager", "Account A"))); });
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(screen.getByText("Account B current row")).toBeVisible();
  });
  it("drops late old-account tasks after a same-persona account reset", async () => {
    const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const old = "unit-task-account-a", next = "unit-task-account-b";
    const { setAccount } = mount({ actor: old, role: "ops-lead", tasks: (actor) => actor === old ? late
      : json({ items: [{ id: "TASK-B", title: "Current B task", summary: "Current account", status: "todo" }] }) });
    await screen.findByText(`${old} current row`);
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    await reading.promise; setAccount(next); fireEvent.click(screen.getByRole("button", { name: "重設視角" }));
    await act(async () => { body.resolve({ items: [{ id: "TASK-A", title: "Old A task", summary: "Superseded account", status: "todo" }] }); });
    await screen.findByText(`${next} current row`);
    await screen.findByText("Current B task");
    expect(screen.queryByText("Old A task")).not.toBeInTheDocument();
    expect(screen.queryByText(`${old} current row`)).not.toBeInTheDocument();
  });

  it.each(["HTTP", "JSON"])("cookie-only rotation rejects late A Task %s without any UI reload", async (boundary) => {
    const pending = deferred<Response>(); const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const { setAccount, fetch } = mount({ tasks: (actor) => actor === A ? boundary === "HTTP" ? pending.promise : late
      : json({ items: [{ id: "TASK-B", title: "Current B task", summary: "Current account", status: "todo" }] }) });
    await screen.findByText("Account A current row");
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    if (boundary === "JSON") await reading.promise;
    else await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url) === "/api/v1/operator/shell/tasks")).toBe(true));
    setAccount(B);
    await act(async () => { if (boundary === "HTTP") pending.resolve(json({ items: [{ id: "TASK-A", title: "Old A task", status: "todo" }] }));
      else body.resolve({ items: [{ id: "TASK-A", title: "Old A task", status: "todo" }] }); });
    await screen.findByText("Account B current row"); await screen.findByText("Current B task");
    expect(screen.queryByText("Old A task")).not.toBeInTheDocument();
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(new Headers(bootstrapCalls(fetch).at(-1)?.[1]?.headers).has("X-Operator-Role")).toBe(false);
  });
  it.each(["HTTP", "JSON", "ERROR_JSON"])("cookie-only rotation rejects late A Network snapshot %s without any UI reload", async (boundary) => {
    window.history.replaceState(null, "", "/operator?ws=network&tab=radar");
    const pending = deferred<Response>(); const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}, boundary === "ERROR_JSON" ? 403 : 200);
    const deferredBody = () => { reading.resolve(true); return body.promise; };
    late.json = deferredBody;
    if (boundary === "ERROR_JSON") { const clone = json({}, 403); clone.json = deferredBody; late.clone = () => clone; }
    const { setAccount, fetch } = mount({ workspace: "network", tab: "radar", network: (_path, actor) => {
      if (actor === A) return boundary === "HTTP" ? pending.promise : late;
      return json(networkSnapshot());
    } });
    await screen.findByRole("button", { name: /展店經理/ });
    if (boundary !== "HTTP") await reading.promise;
    else await waitFor(() => expect(fetch.mock.calls.some(([url]) => new URL(String(url), window.location.origin).pathname === "/api/v1/operator/network-listings")).toBe(true), { timeout: 5000 });
    setAccount(B);
    await act(async () => { if (boundary === "HTTP") pending.resolve(json(networkSnapshot("Old A binding")));
      else body.resolve(boundary === "ERROR_JSON" ? { error: { code: "forbidden", message: "Old A binding", correlation_id: "unit-old-A" } } : networkSnapshot("Old A binding")); });
    expect(screen.queryAllByText(/Old A binding/)).toHaveLength(0);
    await waitFor(() => expect(bootstrapCalls(fetch).length).toBeGreaterThan(1));
    expect(new Headers(bootstrapCalls(fetch).at(-1)?.[1]?.headers).has("X-Operator-Role")).toBe(false);
    expect(screen.queryAllByText(/Old A binding/)).toHaveLength(0);
    // Today is an authorized read of the new account; no reload/role event caused the recovery.
    fireEvent.click(screen.getByRole("button", { name: /^Today/ }));
    await screen.findByText("Account B current row");
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
  });

  it.each(["Task", "Network"])("expired authenticated POC %s read cannot downgrade to no-auth fixture compatibility", async (surface) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const { setSession } = mount({ workspace: surface === "Network" ? "network" : "today", tasks: () => late,
      legacy: (path) => path === "/api/v1/heatzones" ? late : json({ candidates: [] }) });
    if (surface === "Task") { await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button")); }
    await reading.promise;
    setSession({ detail: "session expired" }, 401);
    await act(async () => { body.resolve(surface === "Task" ? { items: [{ id: "OLD", title: "Old expired task", status: "todo" }] }
      : { items: [{ h3_index: "OLD", label: "Old expired binding" }] }); });
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("Old expired task")).not.toBeInTheDocument();
    expect(screen.queryByText("Old expired binding")).not.toBeInTheDocument();
    expect(screen.queryByText("開發／驗收 API 資料")).not.toBeInTheDocument();
  });
  it.each(["Task", "Network"])("invalid session200 during %s JSON fails closed and clears old rows", async (surface) => {
    if (surface === "Network") window.history.replaceState(null, "", "/operator?ws=network&tab=radar");
    const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const { setSession } = mount({ workspace: surface === "Network" ? "network" : "today", tab: surface === "Network" ? "radar" : undefined, tasks: () => late,
      network: surface === "Network" ? () => late : undefined,
      legacy: (path) => path === "/api/v1/heatzones" ? late : json({ candidates: [] }) });
    if (surface === "Task") { await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button")); }
    await reading.promise; setSession({ subject: A, expiresAt: 1 });
    await act(async () => { body.resolve(surface === "Task" ? { items: [{ id: "OLD", title: "Old invalid task", status: "todo" }] }
      : networkSnapshot("Old invalid binding")); });
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("Old invalid task")).not.toBeInTheDocument();
    expect(screen.queryByText("Old invalid binding")).not.toBeInTheDocument();
  });

  // A synthetic successful POST is only the premise that invokes the existing
  // refresh callback. These controls do not establish business approval/auth.
  it.each(["HTTP", "JSON"])("cookie-only rotation rejects late A post-decision refresh %s without UI reload", async (boundary) => {
    const pending = deferred<Response>(); const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const { setAccount, fetch } = mount({ decision: true, refresh: boundary === "HTTP" ? pending.promise : late });
    await screen.findByText("Account A current row"); fireEvent.click(screen.getByRole("button", { name: "核准" }));
    if (boundary === "JSON") await reading.promise;
    else await waitFor(() => expect(bootstrapCalls(fetch)).toHaveLength(2));
    setAccount(B);
    await act(async () => { if (boundary === "HTTP") pending.resolve(json(envelope("expansion-manager", "Late refreshed A")));
      else body.resolve(envelope("expansion-manager", "Late refreshed A")); });
    await screen.findByText("Account B current row");
    expect(screen.queryByText("Late refreshed A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(new Headers(bootstrapCalls(fetch).at(-1)?.[1]?.headers).has("X-Operator-Role")).toBe(false);
  });

  it("a current same-account post-decision refresh still applies validated API data", async () => {
    mount({ decision: true, refresh: json(envelope("expansion-manager", "Refreshed current A")) });
    await screen.findByText("Account A current row"); fireEvent.click(screen.getByRole("button", { name: "核准" }));
    await screen.findByText("Refreshed current A current row");
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });
  it.each(["unknown", "foreign known", "fixture source"])("post-decision refresh rejects %s metadata before applying rows", async (kind) => {
    const response = envelope(kind === "foreign known" ? "ops-lead" : "expansion-manager", "Invalid refreshed A");
    if (kind === "unknown") response.meta.role.id = "unknown-profile" as typeof response.meta.role.id;
    if (kind === "fixture source") response.meta.source = "fixture";
    mount({ decision: true, refresh: json(response) });
    await screen.findByText("Account A current row"); fireEvent.click(screen.getByRole("button", { name: "核准" }));
    await screen.findByTestId("operator-data-unavailable");
    expect(screen.queryByText("Invalid refreshed A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
  });

  it("authenticated POC empty notifications never acquires bundled notification rows", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    mount(); await screen.findByText("Account A current row");
    fireEvent.click(screen.getByTestId("operator-notification-button"));
    const panel = document.querySelector('[data-screen-label="Notifications"]');
    expect(panel).not.toBeNull(); expect(panel?.querySelectorAll("article")).toHaveLength(0);
    expect(screen.getByTestId("operator-notification-button")).toHaveTextContent("通知0");
    expect(screen.getByText("開發／驗收 API 資料")).toBeVisible();
  });
  it("authenticated POC empty refresh clears prior API issue rows instead of retaining fallback state", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    mount({ decision: true, issues: true, refresh: json(envelope("expansion-manager", "Refreshed empty A")) });
    await screen.findByText("Account A current row");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    fireEvent.change(screen.getByLabelText("Command palette search Global search"), { target: { value: "UNIT-ISSUE" } });
    expect(await screen.findByText("Prior API issue marker")).toBeVisible();
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    await screen.findByText("Refreshed empty A current row");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByTestId("operator-command-palette")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Command palette search Global search"), { target: { value: "UNIT-ISSUE" } });
    expect(screen.queryByText("Prior API issue marker")).not.toBeInTheDocument();
  });
  it("explicit no-auth POC still retains visibly labelled fixture compatibility", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    const fixture = envelope("ops-lead", "Explicit fixture"); fixture.meta.source = "fixture";
    mount({ role: "ops-lead", sessionStatus: 401, session: { detail: "No authenticated session" }, firstBootstrap: json(fixture) });
    await screen.findByText("Explicit fixture current row");
    expect(document.querySelector('[data-data-status="fixture"]')).toBeVisible();
    expect(document.querySelector('[data-data-status="ready"]')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("operator-notification-button"));
    expect(document.querySelector('[data-screen-label="Notifications"]')?.querySelectorAll("article").length).toBeGreaterThan(0);
    expect(screen.queryByText("Production API 資料")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    expect(await within(screen.getByTestId("operator-task-center")).findByText("FIXTURE", { exact: true })).toBeVisible();
    expect(screen.getByLabelText("Task center summary")).not.toHaveTextContent(/0\s*Visible/);
  });

  it.each(["empty", "error"])("authenticated POC Task API %s result never populates bundled tasks", async (result) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    mount({ tasks: () => json(result === "empty" ? { items: [] } : { detail: "Unit task refusal" }, result === "empty" ? 200 : 503) });
    await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button"));
    await screen.findByTestId("operator-task-center-unavailable");
    expect(screen.getByLabelText("Task center summary")).toHaveTextContent(/0\s*Visible/);
    expect(within(screen.getByTestId("operator-task-center")).queryByText("FIXTURE", { exact: true })).not.toBeInTheDocument();
  });

  it("authenticated POC Task API 403 with valid session never uses fixture task rows", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    mount({ tasks: () => json({ detail: "Unit scoped refusal" }, 403) });
    await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button"));
    await screen.findByTestId("operator-task-center-unavailable");
    expect(screen.getByLabelText("Task center summary")).toHaveTextContent(/0\s*Visible/);
    expect(within(screen.getByTestId("operator-task-center")).queryByText("FIXTURE", { exact: true })).not.toBeInTheDocument();
  });
  it("authenticated POC seed Task payload cannot be normalized into API tasks", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    mount({ tasks: () => json({ source: "fixture", items: [{ id: "UNIT-SEED", title: "Seed Task marker", status: "todo" }] }) });
    await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button"));
    await screen.findByTestId("operator-task-center-unavailable");
    expect(screen.getByLabelText("Task center summary")).toHaveTextContent(/0\s*Visible/);
    expect(screen.queryByText("Seed Task marker")).not.toBeInTheDocument();
  });
  it("genuine authenticated POC still waits for the trusted persona and marks real API ready", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    const { fetch } = mount({ storedRole: "ops-lead" });
    await screen.findByText("Account A current row");
    expect(new Headers(bootstrapCalls(fetch)[0][1]?.headers).has("X-Operator-Role")).toBe(false);
    expect(screen.getByText("開發／驗收 API 資料")).toBeVisible();
  });
});

describe("independent null-to-real session controls", () => {
 it("fixture-start then genuine B must probe current session before Task GET or fixture fallback", async () => {
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
  const fixture = envelope("ops-lead", "Explicit fixture"); fixture.meta.source = "fixture";
  const state = mount({ role: "ops-lead", sessionStatus: 401, session: { detail: "no session" }, firstBootstrap: json(fixture), tasks: () => json({ items: [] }) });
  await screen.findByText("Explicit fixture current row");
  const before = state.fetch.mock.calls.filter(([url]) => String(url) === "/auth/session").length;
  state.setAccount(B, "expansion-manager"); state.setSession({ subject: B, expiresAt: Math.floor(Date.now()/1000)+3600 });
  fireEvent.click(screen.getByTestId("operator-task-center-button"));
  await waitFor(() => expect(state.fetch.mock.calls.filter(([url]) => String(url) === "/auth/session").length).toBeGreaterThan(before));
  expect(within(screen.getByTestId("operator-task-center")).queryByText("FIXTURE", { exact: true })).not.toBeInTheDocument();
 });
});

describe("independent denied JSON session controls", () => {
  it("late A forbidden JSON must not settle B current account without a current-subject read", async () => {
    const body = deferred<unknown>(); const reading = deferred<boolean>();
    const denied = json({}, 403);
    const cloned = json({}, 403);
    cloned.json = () => { reading.resolve(true); return body.promise; };
    denied.clone = () => cloned;
    const { setAccount, fetch } = mount({ firstBootstrap: denied });
    await reading.promise;
    setAccount(B);
    await act(async () => { body.resolve({ detail: "actual old A forbidden context" }); });
    await screen.findByText("Account B current row");
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
    expect(new Headers(bootstrapCalls(fetch).at(-1)?.[1]?.headers).has("X-Operator-Role")).toBe(false);
  });
});

describe("owned reads from explicit no-session POC", () => {
  it("an explicit no-session POC404 compatibility endpoint still stays labelled fixture", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    const fixture = envelope("ops-lead", "Legacy missing session endpoint"); fixture.meta.source = "fixture";
    mount({ role: "ops-lead", sessionStatus: 404, session: { detail: "Legacy isolated endpoint absent" }, firstBootstrap: json(fixture), tasks: () => json({ items: [] }) });
    await screen.findByText("Legacy missing session endpoint current row");
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    expect(await within(screen.getByTestId("operator-task-center")).findByText("FIXTURE", { exact: true })).toBeVisible();
    expect(document.querySelector('[data-data-status="fixture"]')).toBeVisible();
    expect(document.querySelector('[data-data-status="ready"]')).not.toBeInTheDocument();
  });

  it.each(["HTTP", "JSON"])("null first-bootstrap %s cannot apply after genuine B becomes current", async (boundary) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    const pending = deferred<Response>(); const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const state = mount({ role: "ops-lead", sessionStatus: 401, session: { detail: "No session" }, firstBootstrap: boundary === "HTTP" ? pending.promise : late });
    if (boundary === "JSON") await reading.promise;
    else await waitFor(() => expect(bootstrapCalls(state.fetch)).toHaveLength(1));
    state.setAccount(B, "expansion-manager"); state.setSession({ subject: B, expiresAt: Math.floor(Date.now()/1000)+3600 });
    await act(async () => { if (boundary === "HTTP") pending.resolve(json(envelope("ops-lead", "Late no-session bootstrap")));
      else body.resolve(envelope("ops-lead", "Late no-session bootstrap")); });
    await screen.findByText("Account B current row");
    expect(screen.queryByText("Late no-session bootstrap current row")).not.toBeInTheDocument();
    expect(new Headers(bootstrapCalls(state.fetch).at(-1)?.[1]?.headers).has("X-Operator-Role")).toBe(false);
  });
  it.each([["Task", "HTTP"], ["Task", "JSON"], ["Network", "HTTP"], ["Network", "JSON"], ["refresh", "HTTP"], ["refresh", "JSON"]] as const)("null %s %s lease discovers real B without fixture-role authority", async (surface, boundary) => {
      window.sessionStorage.clear();
      vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
      const pending = deferred<Response>(); const body = deferred<unknown>(); const reading = deferred<boolean>();
      const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
      const initialRole = surface === "Network" ? "expansion-manager" : "ops-lead";
      const fixture = envelope(initialRole, "Explicit fixture", surface === "refresh"); fixture.meta.source = "fixture";
      const state = mount({ role: initialRole, storedRole: initialRole, sessionStatus: 401, session: { detail: "No session" }, firstBootstrap: json(fixture),
        workspace: surface === "Network" ? "network" : "today", decision: surface === "refresh",
        refresh: surface === "refresh" ? boundary === "HTTP" ? pending.promise : late : undefined,
        tasks: (actor) => actor === B ? json({ items: [] }) : boundary === "HTTP" ? pending.promise : late,
        legacy: (path, actor) => path === "/api/v1/heatzones" && actor !== B ? boundary === "HTTP" ? pending.promise : late : json(path === "/api/v1/listings/candidates" ? { candidates: [] } : { items: [] }) });
      if (surface === "Task") { await screen.findByText("Explicit fixture current row"); fireEvent.click(screen.getByTestId("operator-task-center-button")); }
      if (surface === "refresh") { await screen.findByText("Explicit fixture current row"); fireEvent.click(screen.getByRole("button", { name: "核准" })); }
      if (boundary === "JSON") await reading.promise;
      else await waitFor(() => expect(state.fetch.mock.calls.some(([url]) => surface === "Task" ? String(url) === "/api/v1/operator/shell/tasks"
        : surface === "Network" ? new URL(String(url), window.location.origin).pathname === "/api/v1/heatzones" : bootstrapCalls(state.fetch).length === 2)).toBe(true));
      state.setAccount(B, "expansion-manager"); state.setSession({ subject: B, expiresAt: Math.floor(Date.now()/1000)+3600 });
      const payload = surface === "Task" ? { items: [{ id: "OLD-NULL", title: "Old no-session task", status: "todo" }] }
        : surface === "Network" ? { items: [{ h3_index: "OLD-NULL", label: "Old no-session binding" }] } : envelope("ops-lead", "Old no-session refresh");
      await act(async () => { if (boundary === "HTTP") pending.resolve(json(payload)); else body.resolve(payload); });
      await waitFor(() => expect(bootstrapCalls(state.fetch).length).toBeGreaterThan(surface === "refresh" ? 2 : 1));
      expect(new Headers(bootstrapCalls(state.fetch).at(-1)?.[1]?.headers).has("X-Operator-Role")).toBe(false);
      if (surface !== "Network") await screen.findByText("Account B current row");
      else { fireEvent.click(screen.getByRole("button", { name: /^Today/ })); await screen.findByText("Account B current row"); }
      expect(screen.queryByText("Old no-session task")).not.toBeInTheDocument();
      expect(screen.queryByText("Old no-session binding")).not.toBeInTheDocument();
      expect(screen.queryByText("Old no-session refresh current row")).not.toBeInTheDocument();
      state.view.unmount();
  });
});
