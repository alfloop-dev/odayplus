import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ColdIntakeDetail } from "../ColdIntakeDetail";
import { OperatorConsole, operatorBootstrapRetryPolicy } from "../../../OperatorConsole";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import { ListingInboxIntakeView } from "../ListingInboxIntakeView";

vi.mock("next/navigation", () => ({
  usePathname: () => "/intake/OWN-INTAKE",
  useSearchParams: () => new URLSearchParams("selected=OWN-INTAKE&dialog=detail"),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
// Only the WebGL leaf is replaced in the unit DOM; the shell and detail
// transport, session checks, record rendering and assertions remain mounted.
vi.mock("../../HeatZoneMap", () => ({ HeatZoneMap: () => null }));

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const record = () => ({ id: "OWN-INTAKE", version: 1, stage: "NEEDS_REVIEW", originalUrl: "https://source.example/own",
  canonicalUrl: "https://source.example/own", policy: "APPROVED_RETRIEVAL", policyLabel: "已核驗來源", policyReason: "Owned test",
  parserVersion: "1", submitter: "A", owner: null, heatZoneId: null, sourceId: "OWN-SOURCE", rawSnapshot: null,
  snapshotId: null, capturedAt: null, correlationId: "own-correlation", parsedFields: {}, matchResult: null,
  auditEvents: [{ id: "OWN-AUDIT", occurredAt: "2026-10-14", actorName: "A", actorRoleId: "site_reviewer", action: "submit", targetId: "OWN-INTAKE", message: "Owned memory test", correlationId: "own-correlation" }] });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
const retryDelays = [...operatorBootstrapRetryPolicy.delaysMs];
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks();
  operatorBootstrapRetryPolicy.delaysMs = [...retryDelays]; window.sessionStorage.clear(); });

describe("cold authorized resource read while shell persona is unavailable", () => {
  it.each(["masked", "manual"])("retains a legitimately %s nullable intake without inventing a URL or parser result", async (kind) => {
    const value = { ...record(), originalUrl: null, canonicalUrl: kind === "manual" ? null : record().canonicalUrl,
      parserVersion: kind === "manual" ? null : "1", _masked: kind === "masked" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 }) : json(value)));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    expect(await screen.findByTestId("intake-detail-id")).toHaveTextContent("OWN-INTAKE");
    expect(screen.getByTestId("intake-audit-references")).toHaveTextContent("OWN-AUDIT");
    expect(screen.queryByTestId("intake-detail-actions")).toBeNull();
  });
  it.each(["camel", "snake"])("renders a real-shaped %s failure in read-only mode", async (kind) => {
    const failure = kind === "camel" ? { code: "OWN-FAILURE", summary: "Owned failed intake", nextAction: "CONTACT_OWNER", retryable: false }
      : { code: "OWN-FAILURE", message: "Owned failed intake", next_action: "CONTACT_OWNER", correlation_id: "own-correlation", retryable: false };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 }) : json({ ...record(), stage: "FAILED", failure })));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    expect(await screen.findByTestId("intake-error-recovery")).toHaveTextContent("OWN-FAILURE");
    expect(screen.queryByTestId("intake-detail-actions")).toBeNull();
  });
  it.each(["code", "message", "next_action", "correlation_id", "occurred_at", "current_version"])("refuses malformed failure.%s before rendering its payload", async (key) => {
    const failure: Record<string, unknown> = { code: "OWN-FAILURE", message: "Owned failed intake", next_action: "CONTACT_OWNER", retryable: false };
    failure[key] = { unexpected: "object" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 }) : json({ ...record(), stage: "FAILED", failure })));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "重新讀取收件" });
    expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it("a fresh authenticated direct URL survives all four bootstrap refusals without a stored persona", async () => {
    window.sessionStorage.clear(); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    operatorBootstrapRetryPolicy.delaysMs = retryDelays.map(() => 0);
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const path = new URL(String(input), window.location.origin).pathname;
      if (path === "/auth/session") return json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 });
      if (path === "/api/v1/operator/bootstrap") return json({ detail: "Shell unavailable" }, 503);
      if (path === "/api/v1/operator/network-listings/intake/OWN-INTAKE") return json(record());
      return json({ detail: "Unrelated read unavailable" }, 403);
    });
    vi.stubGlobal("fetch", fetch);
    render(<OperatorConsole searchParams={{ ws: "network", tab: "radar", selected: "OWN-INTAKE", dialog: "detail" }} />);
    expect(await screen.findByTestId("intake-detail-id")).toHaveTextContent("OWN-INTAKE");
    await waitFor(() => expect(fetch.mock.calls.filter(([url]) => String(url) === "/api/v1/operator/bootstrap")).toHaveLength(4));
    expect(screen.queryByTestId("operator-data-unavailable")).toBeNull();
    const resource = fetch.mock.calls.find(([url]) => String(url).endsWith("/intake/OWN-INTAKE"));
    expect(resource).toBeDefined(); expect(new Headers(resource?.[1]?.headers).has("X-Operator-Role")).toBe(false);
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(screen.queryByTestId("intake-detail-actions")).toBeNull();
  });
  it("reads only the selected resource through the typed same-origin API without a caller persona or commands", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 }) : json(record()));
    vi.stubGlobal("fetch", fetch); render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    expect(await screen.findByTestId("intake-detail-id")).toHaveTextContent("OWN-INTAKE");
    expect(screen.getByTestId("intake-audit-references")).toHaveTextContent("OWN-AUDIT");
    const calls = fetch.mock.calls.filter(([url]) => String(url) !== "/auth/session");
    expect(calls).toHaveLength(1); expect(String(calls[0][0])).toBe(window.location.origin + "/api/v1/operator/network-listings/intake/OWN-INTAKE");
    expect(new Headers((calls[0][1] as RequestInit | undefined)?.headers).has("X-Operator-Role")).toBe(false);
    expect(fetch.mock.calls.some(([url]) => String(url).includes("bootstrap"))).toBe(false);
    expect(screen.queryByTestId("intake-detail-actions")).toBeNull();
    expect(screen.queryByTestId("asg-btn-claim")).toBeNull();
    expect(screen.queryByTestId("fix-field-address_raw")).toBeNull();
  });
  it.each([401, 403])("retains an actual resource %s refusal without displaying its payload", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 }) : json({ detail: "Resource denied", ...record() }, status)));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "重新讀取收件" }); expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it.each(["HTTP", "JSON"])("rejects an A resource after cookie-only B rotation at the %s boundary", async (boundary) => {
    let actor = "A"; const http = deferred<Response>(); const body = deferred<unknown>(); const started = deferred<boolean>();
    const response = json({}); response.json = () => { started.resolve(true); return body.promise; };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/auth/session") return json({ subject: actor, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
      if (boundary === "HTTP") { started.resolve(true); return http.promise; }
      return response;
    }));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />); await started.promise;
    actor = "B"; await act(async () => { if (boundary === "HTTP") http.resolve(json(record())); else body.resolve(record()); });
    await screen.findByRole("button", { name: "重新讀取收件" }); expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it.each(["identity", "seed", "version", "shape"])("rejects a successful %s-conflicting payload", async (kind) => {
    const value: Record<string, unknown> = record();
    if (kind === "identity") value.id = "OTHER"; if (kind === "seed") value.source = "fixture";
    if (kind === "version") value.version = true; if (kind === "shape") value.parsedFields = { unsafe: { key: "x", label: "x", sourceValue: { nested: "object" } } };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 }) : json(value)));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "重新讀取收件" }); expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it("clears the prior record immediately when refreshing and cannot restore it after a refusal", async () => {
    let reads = 0; const refused = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 }) : ++reads === 1 ? json(record()) : refused.promise));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />); await screen.findByTestId("intake-detail-dialog");
    fireEvent.click(screen.getByTestId("intake-refresh-button")); expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
    await act(async () => refused.resolve(json({ detail: "Scope denied" }, 403)));
    await screen.findByRole("button", { name: "重新讀取收件" }); expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it("never requests a resource with a missing authenticated session", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({ error: "Session required" }, 401)); vi.stubGlobal("fetch", fetch);
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "重新讀取收件" }); expect(fetch).toHaveBeenCalledTimes(1);
    expect(String(fetch.mock.calls[0][0])).toBe("/auth/session");
  });
});

describe("cold read metadata passed to the existing detail renderer", () => {
  it("renders an existing manual intake with no canonical URL in the inbox", () => {
    const value = { ...record(), originalUrl: null, canonicalUrl: null, parserVersion: null } as AssistedIntake;
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready"
      onAddSubmit={vi.fn()} onOpenDetail={vi.fn()} records={[value]} />);
    expect(screen.getByTestId("intake-inbox-row-OWN-INTAKE")).toHaveTextContent("UNAVAILABLE");
  });
  it.each(["submittedAt", "submitted_at", "assignmentId", "assignmentStatus", "slaInstanceId", "slaState", "dueAt", "slaDueAt",
    "assignedOwner", "assignedQueue", "target_owner_role"])("refuses object-valued %s before mounting the detail", async (key) => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 })
      : json({ ...record(), [key]: { unexpected: "object" } })));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "重新讀取收件" });
    expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it.each(["assignmentHistory", "slaHistory"])("refuses a non-array %s before mounting the detail", async (key) => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 })
      : json({ ...record(), [key]: { length: 1 } })));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "重新讀取收件" });
    expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it.each(["assignmentHistory", "slaHistory"].flatMap((history) =>
    ["timestamp", "occurredAt", "action", "type", "note", "reason", "description"].map((field) => [history, field])))
  ("refuses object-valued %s.%s before mounting the detail", async (history, field) => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 })
      : json({ ...record(), [history]: [{ [field]: { unexpected: "object" } }] })));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "重新讀取收件" });
    expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it("refuses a parsed field classification that cannot be rendered as text", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 })
      : json({ ...record(), parsedFields: { own: { key: "own", label: "Owned field", sourceValue: "Owned", classification: { unexpected: "object" } } } })));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "重新讀取收件" });
    expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });
  it.each(["camel", "legacy"])("renders valid nullable %s metadata and history in the existing read-only detail", async (kind) => {
    const value = { ...record(), submittedAt: kind === "camel" ? "2026-10-14T00:00:00Z" : null,
      submitted_at: kind === "legacy" ? "2026-10-14T00:00:00Z" : null,
      assignmentId: "OWN-ASSIGNMENT", assignmentStatus: "ASSIGNED", slaInstanceId: "OWN-SLA", slaState: "ON_TRACK",
      dueAt: null, slaDueAt: null, owner: null, assignedOwner: "Owned reviewer", assignedQueue: "Owned queue",
      [kind === "camel" ? "assignmentHistory" : "slaHistory"]: [{ timestamp: null, occurredAt: "2026-10-14T00:00:00Z",
        action: null, type: "ASSIGNED", note: null, reason: "Owned handoff", description: null }] };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
      ? json({ subject: "A", expiresAt: Math.floor(Date.now() / 1000) + 3600 }) : json(value)));
    render(<ColdIntakeDetail intakeId="OWN-INTAKE" onClose={vi.fn()} />);
    expect(await screen.findByTestId("intake-detail-id")).toHaveTextContent("OWN-INTAKE");
    expect(screen.getByTestId("assignment-sla-summary")).toHaveTextContent("OWN-ASSIGNMENT");
    expect(screen.getByTestId("assignment-sla-summary")).toHaveTextContent("Owned handoff");
    expect(screen.getByTestId("intake-submission-summary")).toHaveTextContent("2026-10-14T00:00:00Z");
    expect(screen.queryByTestId("asg-btn-claim")).toBeNull();
  });
});
