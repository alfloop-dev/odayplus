import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole, operatorBootstrapRetryPolicy } from "../OperatorConsole";
import type { NetworkFindAreasWorkspaceProps } from "../NetworkFindAreasWorkspace";
import "../NetworkFindAreasWorkspace";

const confirmedRefreshControl = vi.hoisted(() => ({ enabled: false, invoked: 0 }));
let canonicalHistoryBefore: { url: string; state: unknown } | null = null;
vi.mock("../NetworkFindAreasWorkspace", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../NetworkFindAreasWorkspace")>();
  return { ...actual, NetworkFindAreasWorkspace: (props: NetworkFindAreasWorkspaceProps) => {
    if (!confirmedRefreshControl.enabled) return <actual.NetworkFindAreasWorkspace {...props} />;
    // Only the nine declared shell refresh controls use this leaf adapter.
    // Real Today navigates to the permitted Network owner; its actual console
    // callback is invoked as a unit premise, without a native approval claim.
    return <button type="button" data-testid="unit-confirmed-network-refresh" onClick={() => {
      confirmedRefreshControl.invoked += 1;
      void props.onReviewDecisionConfirmed?.();
    }}>Invoke declared Network confirmation callback</button>;
  } };
});

// Fully mounted lazy Network reads need the same Next navigation premise as
// existing workspace renderer tests. This supplies routing, never authority.
vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (listener: () => void) => {
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  };
  const location = () => useSyncExternalStore(subscribe, () => window.location.href);
  const move = (method: "pushState" | "replaceState") => vi.fn((href: string) => {
    window.history[method](null, "", href);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  const router = { push: move("pushState"), replace: move("replaceState"), refresh: vi.fn() };
  return { usePathname: () => new URL(location()).pathname, useSearchParams: () => new URL(location()).searchParams,
    useRouter: () => router };
});

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
      workspaces: [{ id: "today", label: "Today", shortLabel: "Today", description: "Current scope", allowed: true }], allowedWorkspaces: workspaces, allowedTabs: { network: role === "expansion-manager" ? ["areas", "radar", "candidates", "score", "compare", "review"] : [] } },
    header: { counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 1 } },
    today: { hero: { name, roleLabel: label, scope: "Unit current account", dateLabel: "2026-10-13" }, kpis: [{ label: `${name} current row`, value: "1" }], queue: [], decisions: decision ? [{ id: "SYNTHETIC-APR", title: "Unit refresh callback navigation", meta: "Declared UI callback premise, not approval authority", status: "pending", cta: "Unit refresh", tone: "info", target: { workspace: "network", entityId: "SYNTHETIC-APR", tab: "review" } }] : [], riskRows: [], auditFeed: [] },
    notifications: [], search: { count: 0, items: [] } };
}
// Synthetic but complete scoped snapshots. These controls exercise the real
// Console-owned canonical read lease, not a legacy preload or a fake grant.
function canonicalNetworkSnapshot(label: string) {
  const id = `HZ-${label.replace(/[^a-z0-9]+/gi, "-")}`;
  return { source: "api", selectedHeatZoneId: id, selectedLens: "demand",
    heatZones: [{ id, label, rank: 1, centroid: [121.5, 25], demandGap: 0.4,
      competitionIndex: 0.3, cannibalizationRisk: "low", rentBand: "unit", confidence: 0.7,
      recommendedLens: "demand", reasons: [], risks: [], nextStep: "" }],
    listingSources: [], listings: [], candidates: [], siteReviews: [], expansionSteps: [], assistedIntakes: [] };
}
function failingRead<T>() {
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((_resolve, fail) => { reject = fail; });
  return { promise, reject };
}
const delays = [...operatorBootstrapRetryPolicy.delaysMs];
// Resolve the real module during fixture collection, as other routing controls do.
// Read/assertion deadlines remain unchanged; fresh-browser startup is a separate gate.
beforeEach(() => { window.sessionStorage.clear(); confirmedRefreshControl.enabled = false; confirmedRefreshControl.invoked = 0; operatorBootstrapRetryPolicy.delaysMs = delays.map(() => 0); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); });
afterEach(() => {
  if (confirmedRefreshControl.enabled) {
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  }
  cleanup(); operatorBootstrapRetryPolicy.delaysMs = [...delays]; vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks();
  if (canonicalHistoryBefore !== null) {
    window.history.replaceState(canonicalHistoryBefore.state, "", canonicalHistoryBefore.url);
    canonicalHistoryBefore = null;
  }
});
type Config = { actor?: string; role?: "expansion-manager" | "ops-lead"; storedRole?: string; session?: unknown;
  initialHref?: string; initialSearchParams?: Record<string, string | string[] | undefined>;
  canonicalNetwork?: (actor: string) => Response | Promise<Response>;
  canonicalTabs?: readonly string[];
  nativeIntake?: (path: string, init: RequestInit | undefined, actor: string) => Response | Promise<Response>;
  firstBootstrap?: Response | Promise<Response>; refresh?: Response | Promise<Response>; decision?: boolean; tasks?: (actor: string) => Response | Promise<Response>; legacy?: (path: string, actor: string) => Response | Promise<Response>; workspace?: string; issues?: boolean; sessionStatus?: number; invalidRole?: string; denied?: number };
function mount(config: Config = {}) {
  confirmedRefreshControl.enabled = config.decision === true;
  let actor = config.actor ?? A;
  let role = config.role ?? "expansion-manager";
  let boots = 0;
  const bootstrapObservations: { subject: string; roleHint: string | null; serverRole: string }[] = [];
  let sessionOverride = config.session;
  let sessionStatus = config.sessionStatus ?? 200;
  if (config.storedRole) window.sessionStorage.setItem("oday.operator.role", config.storedRole);
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/auth/session") return json(sessionOverride ?? { subject: actor, expiresAt: Math.floor(Date.now() / 1000) + 3600 }, sessionStatus);
    if (url === "/api/v1/operator/bootstrap") {
      boots += 1;
      bootstrapObservations.push({ subject: actor, roleHint: new Headers(init?.headers).get("X-Operator-Role"), serverRole: role });
      if (boots === 1 && config.firstBootstrap) return config.firstBootstrap;
      if (boots === 2 && config.refresh) return config.refresh;
      const hint = new Headers(init?.headers).get("X-Operator-Role");
      if (config.denied || hint && hint !== role) return json({ detail: "requested Operator Console role is outside principal roles" }, config.denied ?? 403);
      const body = envelope(role, actor === A ? "Account A" : actor === B ? "Account B" : actor === "unit-operations-account" ? "Operations account" : actor, config.decision && actor === A);
      if (config.canonicalNetwork) {
        // The fixture grants only the read under test; no unrelated domain
        // request or hidden static persona is needed to start its HTTP/JSON.
        body.navigation.allowedTabs.network = [...(config.canonicalTabs ?? ["areas"])];
        body.navigation.workspaces.push({ id: "network", label: "Network", shortLabel: "Network", description: "Current scope", allowed: true });
      }
      if (config.invalidRole) body.meta.role.id = config.invalidRole as typeof role;
      if (config.issues) Object.assign(body, { issues: [{ id: "UNIT-ISSUE", title: "Prior API issue marker", storeName: "Unit store", summary: "Prior API record", severity: "high" }] });
      return json(body);
    }
    if (new URL(url, window.location.origin).pathname === "/api/v1/operator/network-listings" && config.canonicalNetwork) return config.canonicalNetwork(actor);
    if (config.nativeIntake && (/^\/api\/v1\/canonical-intakes(?:\/|$)/.test(new URL(url, window.location.origin).pathname)
      || /^\/api\/v1\/intakes\/[^/]+\/match-case$/.test(new URL(url, window.location.origin).pathname))) return config.nativeIntake(new URL(url, window.location.origin).pathname, init, actor);
    if (url === "/api/v1/operator/shell/tasks" && config.tasks) return config.tasks(actor);
    if (config.legacy && ["/api/v1/heatzones", "/api/v1/listings/candidates", "/api/v1/sitescore/reports"].includes(new URL(url, window.location.origin).pathname)) return config.legacy(new URL(url, window.location.origin).pathname, actor);
    return json({ detail: "unrelated read unavailable" }, 403);
  });
  vi.stubGlobal("fetch", fetch);
  if (config.canonicalNetwork || config.initialHref) {
    canonicalHistoryBefore ??= { url: window.location.href, state: window.history.state };
    window.history.replaceState(null, "", config.initialHref ?? "/operator?ws=network&tab=areas");
  }
  const view = render(<OperatorConsole searchParams={config.initialSearchParams ?? { ws: config.workspace ?? "today" }} />);
  return { fetch, view, bootstrapObservations, setAccount: (next: string, nextRole = role) => { actor = next; role = nextRole; }, setSession: (value: unknown, status = 200) => { sessionOverride = value; sessionStatus = status; }, boots: () => boots };
}
function bootstrapCalls(fetch: ReturnType<typeof vi.fn>) { return fetch.mock.calls.filter(([url]) => String(url) === "/api/v1/operator/bootstrap"); }

async function invokeConfirmedNetworkRefreshControl() {
  const invoked = confirmedRefreshControl.invoked;
  fireEvent.click(screen.getByRole("button", { name: "核准" }));
  const callback = await screen.findByTestId("unit-confirmed-network-refresh");
  expect(window.location.search).toContain("ws=network");
  fireEvent.click(callback);
  expect(confirmedRefreshControl.invoked).toBe(invoked + 1);
  // Keep the real Today rows visible while the original deferred read runs.
  fireEvent.click(screen.getByRole("button", { name: /^Today/ }));
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
}

// Synthetic typed transport values, not source evidence or processing proof.
function nativeReadProjection(intake_id: string, version: number, submitted_at: string) {
  return { source_info: { source_id: null, recorded_policy_state: null, last_transition_at: submitted_at, completed_at: null,
    url_fields: ["originalUrl", "canonicalUrl"].map((field_path) => ({ field_path, classification: "CONFIDENTIAL",
      effective: null, masked: true, mask_reason_code: "FIELD_MASKED" as const })) },
    processing_history: { intake_id, intake_version: version, through_sequence: 0, items: [], next_cursor: null, previous_cursor: null } };
}

async function beginCanonicalCommand(boundary: "HTTP" | "JSON" | "ERROR_JSON" | "uncertain",
  probe?: (init: RequestInit | undefined, actor: string) => Response | Promise<Response>) {
  const response = deferred<Response>(), body = deferred<unknown>(), reading = deferred<boolean>();
  const receipt = { intake_id: "00000000-0000-0000-0000-000000000095", intake_method: "URL",
    processing_state: "SUBMITTED", version: 1, submitted_at: "2026-10-29T03:00:00Z",
    correlation_id: "00000000-0000-0000-0000-000000000096", transition_id: "00000000-0000-0000-0000-000000000097",
    audit_event_id: "00000000-0000-0000-0000-000000000098", outbox_event_id: "00000000-0000-0000-0000-000000000099" };
  const writes: RequestInit[] = [];
  const tabs = ["areas", "radar"];
  const state = mount({ workspace: "network", canonicalTabs: tabs,
    canonicalNetwork: () => json(canonicalNetworkSnapshot("Current native context")),
    nativeIntake: (path, init, actor) => {
      if (init?.method !== "POST") {
        if (path === "/api/v1/canonical-intakes/submissions/receipt" && probe) return probe(init, actor);
        if (path.endsWith("/match-case")) return json({ detail: "CANONICAL_MATCH_CASE_NOT_FOUND" }, 404);
        if (path === "/api/v1/canonical-intakes/" + receipt.intake_id) return json({ ...receipt, ...nativeReadProjection(receipt.intake_id, receipt.version, receipt.submitted_at), tenant_id: B,
          submitter_subject_id: actor, scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null } });
        return json({ items: [], next_cursor: null });
      }
      writes.push(init);
      if (writes.length > 1) return json(receipt, 201);
      if (boundary === "JSON" || boundary === "ERROR_JSON") {
        const pending = json({}, boundary === "ERROR_JSON" ? 503 : 200); pending.json = () => { reading.resolve(true); return body.promise; };
        return pending;
      }
      reading.resolve(true); return response.promise;
    } });
  fireEvent.click(await screen.findByTestId("network-tab-1"));
  fireEvent.click(await screen.findByRole("button", { name: "選取收件／比對案件" }));
  fireEvent.click(await screen.findByRole("button", { name: "從網址建立正式收件" }));
  fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://source.example/console-owned-command" } });
  fireEvent.change(screen.getByLabelText("正式收件範圍"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("送件原因"), { target: { value: "保留同一個送件及查詢編號" } });
  fireEvent.click(screen.getByTestId("intake-submit-button"));
  await reading.promise;
  expect(writes).toHaveLength(1);
  return { ...state, writes, receipt, tabs, finish: async () => {
    await act(async () => {
      if (boundary === "JSON" || boundary === "ERROR_JSON") body.resolve(receipt);
      else response.resolve(boundary === "uncertain" ? json({ detail: "response lost" }, 503) : json(receipt, 201));
    });
  } };
}

describe("authenticated bootstrap persona ownership", () => {
  it("reads the exact original receipt through the real Console after an uncertain command and full workspace teardown", async () => {
    let original: unknown;
    const state = await beginCanonicalCommand("uncertain", () => json({ observation: "RECEIPT_COMMITTED",
      observed_at: "2026-10-29T07:00:00Z", original_http_status: 201, receipt: original }));
    original = state.receipt;
    await state.finish();
    await screen.findByText(/結果尚未確認；重試會沿用同一請求/);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: /^Today/ }));
    await screen.findByText("Account A current row");
    fireEvent.click(screen.getByRole("button", { name: /^Network/ }));
    fireEvent.click(await screen.findByRole("button", { name: "查詢原操作回執" }));
    await screen.findByText(/原操作已收到回執/);
    expect(state.writes).toHaveLength(1);
    const reads = state.fetch.mock.calls.filter(([path]) => String(path) === "/api/v1/canonical-intakes/submissions/receipt");
    expect(reads).toHaveLength(1);
    const init = reads[0][1];
    expect(init?.method).toBeUndefined(); expect(init?.body).toBeUndefined();
    expect(new Headers(init?.headers).get("idempotency-key")).toBe(new Headers(state.writes[0].headers).get("idempotency-key"));
    expect(new Headers(init?.headers).has("if-match")).toBe(false);
    expect(screen.queryByText(/原操作結果尚未確認/)).not.toBeInTheDocument();
  });

  it.each(["HTTP", "JSON"] as const)("discards an old-account receipt observation across the real Console at %s", async (boundary) => {
    const response = deferred<Response>(), body = deferred<unknown>(), started = deferred<boolean>();
    const state = await beginCanonicalCommand("uncertain", () => {
      if (boundary === "HTTP") { started.resolve(true); return response.promise; }
      const pending = json({}); pending.json = () => { started.resolve(true); return body.promise; }; return pending;
    });
    await state.finish();
    await screen.findByText(/結果尚未確認；重試會沿用同一請求/);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(await screen.findByRole("button", { name: "查詢原操作回執" }));
    await started.promise;
    state.setAccount(B);
    const result = { observation: "RECEIPT_COMMITTED", observed_at: "2026-10-29T07:00:00Z",
      original_http_status: 201, receipt: state.receipt };
    await act(async () => { if (boundary === "HTTP") response.resolve(json(result)); else body.resolve(result); });
    await waitFor(() => expect(state.bootstrapObservations.some((entry) => entry.subject === B)).toBe(true));
    expect(screen.queryByRole("region", { name: "先前收件操作" })).not.toBeInTheDocument();
    expect(screen.queryByText(/原操作已收到回執/)).not.toBeInTheDocument();
    expect(screen.queryByText(state.receipt.audit_event_id)).not.toBeInTheDocument();
    expect(state.writes).toHaveLength(1);
  });
  it.each(["NOT_OBSERVED", "DENIED"] as const)("preserves the original command after a receipt observation is %s", async (outcome) => {
    const state = await beginCanonicalCommand("uncertain", () => outcome === "DENIED"
      ? json({ detail: "current view refused" }, 403) : json({ observation: "NOT_OBSERVED", observed_at: "2026-10-29T07:00:00Z" }));
    await state.finish();
    await screen.findByText(/結果尚未確認；重試會沿用同一請求/);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(await screen.findByRole("button", { name: "查詢原操作回執" }));
    if (outcome === "NOT_OBSERVED") await screen.findByText(/這次查詢尚未觀察到原回執/);
    else await screen.findByText(/HTTP 403/);
    expect(state.writes).toHaveLength(1);
    expect(screen.queryByText(/原操作已收到回執/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重試原操作" }));
    await screen.findByText(/原操作已收到回執/);
    expect(state.writes).toHaveLength(2);
    expect(state.writes[1].body).toBe(state.writes[0].body);
    expect(new Headers(state.writes[1].headers).get("idempotency-key")).toBe(new Headers(state.writes[0].headers).get("idempotency-key"));
  });

  it("keeps the actual receipt detail in Network during same-Console history restoration without repeating its POST", async () => {
    const { fetch, receipt, finish, writes } = await beginCanonicalCommand("HTTP");
    expect(new URL(window.location.href).searchParams.get("intakeKind")).toBe("canonical");
    const nativeReadStart = fetch.mock.calls.length;
    const browseHref = window.location.href;
    await finish();
    await waitFor(() => expect(window.location.pathname).toBe("/intake/" + receipt.intake_id));
    const exactHref = window.location.href;
    expect(new URL(exactHref).searchParams.get("intakeKind")).toBe("canonical");
    expect(new URL(exactHref).searchParams.get("canonicalCursor")).toBe("");
    expect(exactHref).not.toContain("source.example");
    const selected = await screen.findByRole("region", { name: "收件處理詳情" });
    await within(selected).findByText("收件 " + receipt.intake_id + " · 版本 1 · 已送出");
    act(() => {
      window.history.replaceState(null, "", browseHref);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await screen.findByRole("button", { name: "從網址建立正式收件" });
    expect(screen.queryByRole("region", { name: "所選收件與比對案件" })).not.toBeInTheDocument();
    act(() => {
      window.history.replaceState(null, "", exactHref);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    const restored = await screen.findByRole("region", { name: "收件處理詳情" });
    await within(restored).findByText("收件 " + receipt.intake_id + " · 版本 1 · 已送出");
    expect(screen.getByTestId("network-find-areas-workspace")).toBeVisible();
    expect(fetch.mock.calls.slice(nativeReadStart).filter(([path]) => String(path).includes("/api/v1/operator/network-listings/intake"))).toHaveLength(0);
    expect(writes).toHaveLength(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("intake-return-button"));
    await screen.findByRole("dialog", { name: "選取收件與比對案件" });
    expect(window.location.pathname).toBe("/operator");
    expect(new URL(window.location.href).searchParams.get("intakeKind")).toBe("canonical");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(new URL(window.location.href).searchParams.has("intakeKind")).toBe(false));
    expect(new URL(window.location.href).searchParams.has("selected")).toBe(false);
    expect(writes).toHaveLength(1);
  });
  it("restores the exact canonical deep link through the real Console without collection discovery, legacy fallback or POST", async () => {
    const target = "00000000-0000-0000-0000-000000000087";
    const config: Config = { workspace: "network", canonicalTabs: ["radar"],
      initialHref: "/intake/" + target + "?intakeKind=canonical&unrelated=preserve",
      initialSearchParams: { ws: "network", tab: "radar", selected: target, dialog: "detail", intakeKind: "canonical" },
      canonicalNetwork: () => json(canonicalNetworkSnapshot("Current native detail")),
      nativeIntake: (path) => path.includes("/match-case") ? json({ detail: "CANONICAL_MATCH_CASE_NOT_FOUND" }, 404)
        : path === "/api/v1/canonical-intakes/" + target ? json({ ...nativeReadProjection(target, 7, "2026-10-29T03:00:00Z"), intake_id: target, tenant_id: B, submitter_subject_id: A,
          correlation_id: "00000000-0000-0000-0000-000000000088", intake_method: "URL", processing_state: "SUBMITTED",
          version: 7, submitted_at: "2026-10-29T03:00:00Z", scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null } })
        : json({ detail: "collection deliberately refused" }, 403) };
    for (let reload = 0; reload < 2; reload += 1) {
      const { view, fetch } = mount(config);
      const selected = await screen.findByRole("region", { name: "所選收件與比對案件" });
      await within(screen.getByRole("region", { name: "收件處理詳情" })).findByText("收件 " + target + " · 版本 7 · 已送出");
      expect(screen.getByRole("region", { name: "收件處理詳情" })).toHaveAttribute("data-screen-label", "Intake 收件處理詳情頁");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(fetch.mock.calls.filter(([path]) => String(path).startsWith("/api/v1/canonical-intakes?"))).toHaveLength(0);
      expect(fetch.mock.calls.filter(([path]) => String(path).includes("/api/v1/operator/network-listings/intake"))).toHaveLength(0);
      expect(screen.queryByTestId("cold-intake-read-state")).not.toBeInTheDocument();
      expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
      expect(new URL(window.location.href).searchParams.get("unrelated")).toBe("preserve");
      view.unmount();
    }
  });

  it.each(["canonical", "unknown"])("never routes an explicit %s cold link through the legacy reader", async (intakeKind) => {
    const target = "00000000-0000-0000-0000-000000000087";
    const { fetch } = mount({ workspace: "network", initialHref: "/intake/" + target + "?intakeKind=" + intakeKind,
      initialSearchParams: { ws: "network", tab: "radar", selected: target, dialog: "detail", intakeKind },
      firstBootstrap: json({ detail: "bootstrap unavailable" }, 403) });
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(fetch.mock.calls.filter(([path]) => String(path).includes("/api/v1/operator/network-listings/intake"))).toHaveLength(0);
    expect(screen.queryByTestId("cold-intake-read-state")).not.toBeInTheDocument();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it.each(["canonical", "unknown"])("vetoes legacy cold reads after browser history changes to %s with stale legacy server props", async (intakeKind) => {
    const oldId = "legacy-cold-record", target = "00000000-0000-0000-0000-000000000087";
    const { fetch } = mount({ workspace: "network", initialHref: "/intake/" + oldId,
      initialSearchParams: { ws: "network", tab: "radar", selected: oldId, dialog: "detail" },
      firstBootstrap: json({ detail: "bootstrap unavailable" }, 403) });
    await screen.findByTestId("cold-intake-read-state");
    const before = fetch.mock.calls.length;
    await act(async () => {
      window.history.pushState(null, "", "/intake/" + target + "?intakeKind=" + intakeKind);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await screen.findByTestId("operator-data-unavailable");
    expect(screen.queryByTestId("cold-intake-read-state")).not.toBeInTheDocument();
    expect(fetch.mock.calls.slice(before).filter(([path]) => String(path).includes("/api/v1/operator/network-listings/intake"))).toHaveLength(0);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("preserves repeated browser selection values instead of choosing a legacy cold resource", async () => {
    const { fetch } = mount({ workspace: "network",
      initialHref: "/operator?ws=network&tab=radar&selected=legacy-A&selected=legacy-B&dialog=detail",
      initialSearchParams: { ws: "network", tab: "radar", selected: ["legacy-A", "legacy-B"], dialog: "detail" },
      firstBootstrap: json({ detail: "bootstrap unavailable" }, 403) });
    const cold = await screen.findByTestId("cold-intake-read-state");
    await within(cold).findByText("連結未指定唯一的收件紀錄。");
    expect(fetch.mock.calls.filter(([path]) => String(path).includes("/api/v1/operator/network-listings/intake"))).toHaveLength(0);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it.each(["HTTP", "JSON", "uncertain"] as const)("retains the original canonical command across full Network teardown at %s", async (boundary) => {
    const { fetch, writes, receipt, finish } = await beginCanonicalCommand(boundary);
    const originalKey = new Headers(writes[0].headers).get("idempotency-key");
    expect(originalKey).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Today/ }));
    await screen.findByText("Account A current row");
    expect(screen.queryByTestId("network-find-areas-workspace")).not.toBeInTheDocument();
    expect(writes[0].signal?.aborted).toBe(false);
    await finish();
    fireEvent.click(screen.getByRole("button", { name: /^Network/ }));
    await screen.findByTestId("network-find-areas-workspace");
    const summary = await screen.findByRole("region", { name: "先前收件操作" });
    if (boundary === "uncertain") fireEvent.click(within(summary).getByRole("button", { name: "重試原操作" }));
    await within(summary).findByText(/原操作已收到回執/);
    expect(summary).toHaveTextContent(receipt.intake_id);
    expect(writes).toHaveLength(boundary === "uncertain" ? 2 : 1);
    for (const write of writes) {
      expect(write.body).toBe(writes[0].body);
      expect(new Headers(write.headers).get("idempotency-key")).toBe(originalKey);
      expect(new Headers(write.headers).has("if-match")).toBe(false);
    }
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(writes.length);
    expect(screen.queryByTestId("intake-add-dialog")).not.toBeInTheDocument();
  });

  it.each(["HTTP", "JSON", "ERROR_JSON"] as const)("rejects late native %s after a cookie-only account change outside Network", async (boundary) => {
    const state = await beginCanonicalCommand(boundary);
    fireEvent.click(screen.getByRole("button", { name: /^Today/ }));
    await screen.findByText("Account A current row");
    state.setAccount(B);
    await state.finish();
    await screen.findByText("Account B current row");
    fireEvent.click(screen.getByRole("button", { name: /^Network/ }));
    await screen.findByTestId("network-find-areas-workspace");
    expect(screen.queryByRole("region", { name: "先前收件操作" })).not.toBeInTheDocument();
    expect(screen.queryByText(state.receipt.intake_id)).not.toBeInTheDocument();
    expect(state.writes).toHaveLength(1);
    expect(state.writes[0].signal?.aborted).toBe(true);
  });

  it.each(["tenant", "ABA", "expired", "unmount", "role", "grants"] as const)("invalidates a Console-owned native command on %s", async (change) => {
    const state = await beginCanonicalCommand("JSON");
    fireEvent.click(screen.getByRole("button", { name: /^Today/ }));
    await screen.findByText("Account A current row");
    if (change === "tenant") {
      window.sessionStorage.setItem("oday.operator.tenant", "changed-observed-tenant");
      state.view.rerender(<OperatorConsole searchParams={{ ws: "today" }} />);
    } else if (change === "ABA") {
      state.setAccount(B); fireEvent.click(screen.getByRole("button", { name: "重設視角" }));
      await screen.findByText("Account B current row");
      state.setAccount(A); fireEvent.click(screen.getByRole("button", { name: "重設視角" }));
      await screen.findByText("Account A current row");
    } else if (change === "role") {
      state.setAccount(A, "ops-lead");
      fireEvent.click(screen.getByRole("button", { name: /展店經理/ }));
      fireEvent.click(await screen.findByRole("button", { name: /^營運主管/ }));
      await screen.findByRole("button", { name: /營運主管/ });
      await screen.findByText("Account A current row");
    } else if (change === "grants") {
      state.tabs.splice(0);
      fireEvent.click(screen.getByRole("button", { name: "重設視角" }));
      await screen.findByText("Account A current row");
    } else if (change === "expired") state.setSession({ subject: A, expiresAt: 1 });
    else state.view.unmount();
    await state.finish();
    if (change === "expired") await screen.findByTestId("operator-data-unavailable");
    if (change === "tenant" || change === "ABA") {
      fireEvent.click(screen.getByRole("button", { name: /^Network/ }));
      await screen.findByTestId("network-find-areas-workspace");
    }
    expect(screen.queryByRole("region", { name: "先前收件操作" })).not.toBeInTheDocument();
    expect(screen.queryByText(state.receipt.intake_id)).not.toBeInTheDocument();
    expect(state.writes).toHaveLength(1);
    expect(state.writes[0].signal?.aborted).toBe(true);
  });

  it("checks the current account before retrying the original uncertain command", async () => {
    const state = await beginCanonicalCommand("uncertain");
    await state.finish();
    const summary = await screen.findByRole("region", { name: "先前收件操作" });
    state.setAccount(B);
    fireEvent.click(within(summary).getByRole("button", { name: "重試原操作" }));
    await waitFor(() => expect(state.bootstrapObservations.at(-1)?.subject).toBe(B));
    fireEvent.click(screen.getByRole("button", { name: /^Today/ }));
    await screen.findByText("Account B current row");
    expect(state.writes).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "先前收件操作" })).not.toBeInTheDocument();
    expect(screen.queryByText(state.receipt.intake_id)).not.toBeInTheDocument();
  });

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
    fireEvent.click(await screen.findByRole("button", { name: /^營運主管/ }));
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
  it.each(["HTTP", "JSON", "ERROR_JSON"])("cookie-only rotation rejects late A Network binding %s without any UI reload", async (boundary) => {
    const pending = deferred<Response>(); const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}, boundary === "ERROR_JSON" ? 403 : 200);
    const deferredBody = () => { reading.resolve(true); return body.promise; };
    late.json = deferredBody;
    if (boundary === "ERROR_JSON") { const clone = json({}, 403); clone.json = deferredBody; late.clone = () => clone; }
    const { setAccount, fetch } = mount({ workspace: "network", canonicalNetwork: (actor) =>
      actor === A ? boundary === "HTTP" ? pending.promise : late : json(canonicalNetworkSnapshot("Current B binding")) });
    await screen.findByRole("button", { name: /展店經理/ });
    if (boundary !== "HTTP") await reading.promise;
    else await waitFor(() => expect(fetch.mock.calls.some(([url]) => new URL(String(url), window.location.origin).pathname === "/api/v1/operator/network-listings")).toBe(true));
    setAccount(B);
    await act(async () => { if (boundary === "HTTP") pending.resolve(json(canonicalNetworkSnapshot("Old A binding")));
      else body.resolve(boundary === "ERROR_JSON" ? { error: { code: "forbidden", message: "Old A binding", correlation_id: "unit-old-A" } } : canonicalNetworkSnapshot("Old A binding")); });
    await waitFor(() => expect(bootstrapCalls(fetch).length).toBeGreaterThan(1));
    expect(new Headers(bootstrapCalls(fetch).at(-1)?.[1]?.headers).has("X-Operator-Role")).toBe(false);
    expect(screen.queryByText("Old A binding")).not.toBeInTheDocument();
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
      canonicalNetwork: surface === "Network" ? () => late : undefined });
    if (surface === "Task") { await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button")); }
    await reading.promise;
    setSession({ detail: "session expired" }, 401);
    await act(async () => { body.resolve(surface === "Task" ? { items: [{ id: "OLD", title: "Old expired task", status: "todo" }] }
      : canonicalNetworkSnapshot("Old expired binding")); });
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("Old expired task")).not.toBeInTheDocument();
    expect(screen.queryByText("Old expired binding")).not.toBeInTheDocument();
    expect(screen.queryByText("開發／驗收 API 資料")).not.toBeInTheDocument();
  });
  it.each(["Task", "Network"])("invalid session200 during %s JSON fails closed and clears old rows", async (surface) => {
    const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const { setSession } = mount({ workspace: surface === "Network" ? "network" : "today", tasks: () => late,
      canonicalNetwork: surface === "Network" ? () => late : undefined });
    if (surface === "Task") { await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button")); }
    await reading.promise; setSession({ subject: A, expiresAt: 1 });
    await act(async () => { body.resolve(surface === "Task" ? { items: [{ id: "OLD", title: "Old invalid task", status: "todo" }] }
      : canonicalNetworkSnapshot("Old invalid binding")); });
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("Old invalid task")).not.toBeInTheDocument();
    expect(screen.queryByText("Old invalid binding")).not.toBeInTheDocument();
  });

  // Explicit Network callback controls preserve shell read ownership checks.
  // They issue no business POST and do not establish native approval/auth.
  it.each(["HTTP", "JSON"])("cookie-only rotation rejects late A post-decision refresh %s without UI reload", async (boundary) => {
    const pending = deferred<Response>(); const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const { setAccount, fetch, bootstrapObservations } = mount({ decision: true, refresh: boundary === "HTTP" ? pending.promise : late });
    await screen.findByText("Account A current row"); await invokeConfirmedNetworkRefreshControl();
    if (boundary === "JSON") await reading.promise;
    else await waitFor(() => expect(bootstrapCalls(fetch)).toHaveLength(2));
    setAccount(B);
    await act(async () => { if (boundary === "HTTP") pending.resolve(json(envelope("expansion-manager", "Late refreshed A")));
      else body.resolve(envelope("expansion-manager", "Late refreshed A")); });
    await screen.findByText("Account B current row");
    expect(screen.queryByText("Late refreshed A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    const recoveryRequest = bootstrapObservations.find((request) => request.subject === B);
    expect(recoveryRequest).toBeDefined();
    expect(recoveryRequest?.roleHint).toBeNull();
    for (const request of bootstrapObservations.filter((request) => request.subject === B)) {
      if (request.roleHint !== null) expect(request.roleHint).toBe(request.serverRole);
    }
  });

  it("a current same-account post-decision refresh still applies validated API data", async () => {
    mount({ decision: true, refresh: json(envelope("expansion-manager", "Refreshed current A")) });
    await screen.findByText("Account A current row"); await invokeConfirmedNetworkRefreshControl();
    await screen.findByText("Refreshed current A current row");
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });
  it.each(["unknown", "foreign known", "fixture source"])("post-decision refresh rejects %s metadata before applying rows", async (kind) => {
    const response = envelope(kind === "foreign known" ? "ops-lead" : "expansion-manager", "Invalid refreshed A");
    if (kind === "unknown") response.meta.role.id = "unknown-profile" as typeof response.meta.role.id;
    if (kind === "fixture source") response.meta.source = "fixture";
    mount({ decision: true, refresh: json(response) });
    await screen.findByText("Account A current row"); await invokeConfirmedNetworkRefreshControl();
    await screen.findByTestId("operator-data-unavailable");
    expect(screen.queryByText("Invalid refreshed A current row")).not.toBeInTheDocument();
    expect(screen.queryByText("Account A current row")).not.toBeInTheDocument();
  });

  it("authenticated POC empty notifications never acquires bundled notification rows", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    mount(); await screen.findByText("Account A current row");
    fireEvent.click(screen.getByTestId("operator-notification-button"));
    await waitFor(() => expect(document.querySelector('[data-screen-label="Notifications"]')).not.toBeNull());
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
    fireEvent.change(await screen.findByLabelText("Command palette search Global search"), { target: { value: "UNIT-ISSUE" } });
    expect(await screen.findByText("Prior API issue marker")).toBeVisible();
    fireEvent.keyDown(window, { key: "Escape" });
    await invokeConfirmedNetworkRefreshControl();
    await screen.findByText("Refreshed empty A current row");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(await screen.findByTestId("operator-command-palette")).toBeVisible();
    fireEvent.change(await screen.findByLabelText("Command palette search Global search"), { target: { value: "UNIT-ISSUE" } });
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
    await waitFor(() => expect(document.querySelector('[data-screen-label="Notifications"]')).not.toBeNull());
    expect(document.querySelector('[data-screen-label="Notifications"]')?.querySelectorAll("article").length).toBeGreaterThan(0);
    expect(screen.queryByText("Production API 資料")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    expect(await within(await screen.findByTestId("operator-task-center")).findByText("FIXTURE", { exact: true })).toBeVisible();
    expect(screen.getByLabelText("Task center summary")).not.toHaveTextContent(/0\s*Visible/);
  });

  it.each(["empty", "error"])("authenticated POC Task API %s result never populates bundled tasks", async (result) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    mount({ tasks: () => json(result === "empty" ? { items: [] } : { detail: "Unit task refusal" }, result === "empty" ? 200 : 503) });
    await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button"));
    await screen.findByTestId("operator-task-center-unavailable");
    expect(screen.getByLabelText("Task center summary")).toHaveTextContent(/0\s*Visible/);
    expect(within(await screen.findByTestId("operator-task-center")).queryByText("FIXTURE", { exact: true })).not.toBeInTheDocument();
  });

  it("authenticated POC Task API 403 with valid session never uses fixture task rows", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    mount({ tasks: () => json({ detail: "Unit scoped refusal" }, 403) });
    await screen.findByText("Account A current row"); fireEvent.click(screen.getByTestId("operator-task-center-button"));
    await screen.findByTestId("operator-task-center-unavailable");
    expect(screen.getByLabelText("Task center summary")).toHaveTextContent(/0\s*Visible/);
    expect(within(await screen.findByTestId("operator-task-center")).queryByText("FIXTURE", { exact: true })).not.toBeInTheDocument();
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
  expect(within(await screen.findByTestId("operator-task-center")).queryByText("FIXTURE", { exact: true })).not.toBeInTheDocument();
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
    expect(await within(await screen.findByTestId("operator-task-center")).findByText("FIXTURE", { exact: true })).toBeVisible();
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
      // The two null refresh callback controls need the existing permitted
      // Network persona; operations has no Network grant. Task is unchanged.
      const initialRole = surface === "Task" ? "ops-lead" : "expansion-manager";
      const fixture = envelope(initialRole, "Explicit fixture", surface === "refresh"); fixture.meta.source = "fixture";
      const state = mount({ role: initialRole, storedRole: initialRole, sessionStatus: 401, session: { detail: "No session" }, firstBootstrap: json(fixture),
        workspace: surface === "Network" ? "network" : "today", decision: surface === "refresh",
        refresh: surface === "refresh" ? boundary === "HTTP" ? pending.promise : late : undefined,
        tasks: (actor) => actor === B ? json({ items: [] }) : boundary === "HTTP" ? pending.promise : late,
        legacy: (path, actor) => path === "/api/v1/heatzones" && actor !== B ? boundary === "HTTP" ? pending.promise : late : json(path === "/api/v1/listings/candidates" ? { candidates: [] } : { items: [] }) });
      if (surface === "Task") { await screen.findByText("Explicit fixture current row"); fireEvent.click(screen.getByTestId("operator-task-center-button")); }
      if (surface === "refresh") { await screen.findByText("Explicit fixture current row"); await invokeConfirmedNetworkRefreshControl(); }
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


describe("canonical Network read owner epochs", () => {
  it.each(["HTTP403", "JSON rejection"])("a late old A %s cannot clear current B Network rows", async (boundary) => {
    const pending = deferred<Response>(); const failedBody = failingRead<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return failedBody.promise; };
    let oldStarted = false;
    const state = mount({ workspace: "network", canonicalNetwork: (actor) => {
      if (actor === A && !oldStarted) { oldStarted = true; return boundary === "HTTP403" ? pending.promise : late; }
      return json(canonicalNetworkSnapshot(actor === B ? "Current B binding" : "Current A binding"));
    } });
    if (boundary === "JSON rejection") await reading.promise;
    else await waitFor(() => expect(state.fetch.mock.calls.some(([url]) => String(url).includes("/operator/network-listings"))).toBe(true));
    state.setAccount(B); fireEvent.click(screen.getByRole("button", { name: "重設視角" }));
    await screen.findByText("Account B current row");
    fireEvent.click(screen.getByRole("button", { name: /^Network/ }));
    await waitFor(() => expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent("Current B binding"));
    await act(async () => { if (boundary === "HTTP403") pending.resolve(json({ detail: "Old A refused" }, 403));
      else failedBody.reject(new Error("Old A JSON failed")); });
    expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent("Current B binding");
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
    expect(screen.queryByText("Old A refused")).not.toBeInTheDocument();
    expect(new Headers(bootstrapCalls(state.fetch).at(-1)?.[1]?.headers).has("X-Operator-Role")).toBe(false);
  });

  it("a completed A→B→A bootstrap epoch cannot adopt an earlier A HTTP response", async () => {
    const old = deferred<Response>(); let firstA = true;
    const state = mount({ workspace: "network", canonicalNetwork: (actor) => {
      if (actor === A && firstA) { firstA = false; return old.promise; }
      return json(canonicalNetworkSnapshot(actor === A ? "Current A binding" : "Current B binding"));
    } });
    await waitFor(() => expect(state.fetch.mock.calls.some(([url]) => String(url).includes("/operator/network-listings"))).toBe(true));
    state.setAccount(B); fireEvent.click(screen.getByRole("button", { name: "重設視角" }));
    await screen.findByText("Account B current row");
    state.setAccount(A); fireEvent.click(screen.getByRole("button", { name: "重設視角" }));
    await screen.findByText("Account A current row");
    fireEvent.click(screen.getByRole("button", { name: /^Network/ }));
    await waitFor(() => expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent("Current A binding"));
    await act(async () => { old.resolve(json(canonicalNetworkSnapshot("Old A binding"))); });
    expect(screen.getByTestId("find-areas-zone-detail")).toHaveTextContent("Current A binding");
    expect(screen.queryByText("Old A binding")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });
});
