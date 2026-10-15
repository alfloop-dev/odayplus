import "@testing-library/jest-dom/vitest";
import { readFileSync } from "node:fs";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hasScoreExecutionContext, hasScoreExecutionReceipt, hasVerifiedCurrentScoreAdmission } from "../networkReadValidation";
import { operatorFixturesAllowed } from "../../operatorDataMode";
import { NetworkFindAreasWorkspace as GrantedReadNetworkWorkspace } from "../../NetworkFindAreasWorkspace";

// Explicit read-grant fixture: shell projects listing and SiteScore read families.
// This is a renderer premise, not a real Principal/production permission grant.
const NETWORK_READ_FIXTURE_TABS = ["areas", "radar", "candidates", "score", "compare", "review"] as const;
function NetworkFindAreasWorkspace(props: import("../../NetworkFindAreasWorkspace").NetworkFindAreasWorkspaceProps) {
  return <GrantedReadNetworkWorkspace allowedTabIds={NETWORK_READ_FIXTURE_TABS} {...props} />;
}


const nav = vi.hoisted(() => ({ pathname: "/operator", search: "ws=network&tab=candidates&candidate=second", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname, useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }),
}));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({
  ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div />,
}));

// Synthetic contract regressions, not live acceptance evidence.
const ids = ["first", "second"];
const candidates = ids.map((id) => ({ id, listingId: `listing-${id}`, heatZoneId: "zone", title: id,
  address: "Recorded location", status: "scored", modelVersion: null, datasetSnapshotId: null,
  score: null, recommendation: null, missingData: [] }));
const network = { source: "api", candidates, heatZones: [], listingSources: [], siteReviews: [], assistedIntakes: [],
  listings: ids.map((id) => ({ id: `listing-${id}`, address: "Recorded location", sourceId: "recorded-source", status: "new" })),
  expansionSteps: [
    { id: "sitescore", label: "SiteScore", tabIndex: 3, entityId: "first", state: "next", summary: "Requires a production model binding." },
  ] };
const scoringCandidates = candidates.map((candidate) => ({ ...candidate, zoneLabel: "Area", modelVersion: "", datasetSnapshotId: "",
  stage: "evaluated", scored: true, inCompare: false,
  readEvidence: { source: "canonical", candidateId: candidate.id, listingId: candidate.listingId, addressId: `address-${candidate.id}`, datasetSnapshotId: null, fields: {} },
  currentAdmission: { candidateId: candidate.id, listingId: candidate.listingId, addressId: `address-${candidate.id}`, checkedAt: "2026-10-13T00:00:00Z",
    currentInputs: { state: "verified", reasonCode: null }, source: { state: "verified", reasonCode: null },
    pit: { state: "verified", reasonCode: null }, model: { state: "verified", reasonCode: null } },
  gate: { state: "ready", passed: true, missing: [], otherMissing: [], checks: [], okCount: 0, totalCount: 0, blockNote: "" } }));
const scorecards = ids.map((id) => ({ id, title: id, zoneLabel: "Area", heatZoneId: "zone", score: null, recommendation: "GO",
  reportId: `report-${id}`, reportVersion: 1, sitescoreRunId: `run-${id}`, modelVersion: "recorded-model",
  datasetSnapshotId: "recorded-snapshot", generatedAt: "2026-10-13T00:00:00Z", confidence: null, payback: null,
  revenuePath: { m1: null, m3: null, m6: null, m12: null }, band: { p10: "—", p50: "—", p90: "—" },
  subScores: {}, capex: null, rentAssumption: null, drivers: [], reasons: [], risks: [], conditions: [], conditionTitle: "" }));
const scoring = { source: "canonical", candidates: scoringCandidates, scorecards, compareSet: [],
  compare: { columns: [], metrics: [], recommendation: null, empty: true } };
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function reads(scoringStatus = 200, reviewRead?: () => Response | Promise<Response>) {
  const mock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).includes("network-listings?")
    ? json(network) : String(input).includes("network-scoring") ? json(scoring, scoringStatus)
      : String(input).includes("network-reviews") && reviewRead ? reviewRead() : json({ detail: "denied" }, 403));
  vi.stubGlobal("fetch", mock);
  return mock;
}

const context = { tenantId: "33333333-3333-4333-8333-333333333333", candidateSiteId: "second", reportId: "report-second", reportVersion: 1, jobVersion: 8, originalJobId: "original-job", predecessorJobId: "original-job" };
const queued = { source: "canonical", created: true, executionRequestId: "request-v2", candidateSiteId: "second", jobId: "job-v2", status: "QUEUED", actualVersion: 1, jobVersion: 1, baseReportId: "report-second", baseReportVersion: 1, result: null, lastError: null, correlationId: "correlation-v2" };
const complete = { ...queued, created: false, status: "SUCCEEDED", actualVersion: 4, jobVersion: 4, result: { report_id: "report-v2", report_version: 2, sitescore_run_id: "run-v2", prediction_run_id: "prediction-v2", model_version: "recorded-model" } };
beforeEach(() => { nav.search = "ws=network&tab=sitescore&candidate=second"; window.history.replaceState(null, "", `/operator?${nav.search}`); window.sessionStorage.clear(); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); });
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("queued canonical score execution", () => {
  it.each(["currentInputs", "source", "pit", "model"])("keeps historical reports readable while current %s admission is unknown", async (key) => {
    const unverified = { ...scoring, candidates: scoringCandidates.map((candidate) => candidate.id === "second" ? { ...candidate,
      currentAdmission: { ...candidate.currentAdmission, [key]: { state: "unverified", reasonCode: "UNVERIFIED" } } } : candidate) };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("network-scoring") ? json(unverified)
      : String(input).includes("network-listings") ? json(network) : json({}, 403)));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("sitescore-card-second");
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded reason" } });
    expect(screen.getByTestId("sitescore-rescore-second")).toBeDisabled();
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
  it("requires exact current identities and the independent recorded data gate", () => {
    const candidate = scoringCandidates[1];
    expect(hasVerifiedCurrentScoreAdmission(candidate)).toBe(true);
    for (const changed of [{ currentAdmission: undefined }, { currentAdmission: { ...candidate.currentAdmission, candidateId: "foreign" } },
      { currentAdmission: { ...candidate.currentAdmission, listingId: "foreign" } }, { currentAdmission: { ...candidate.currentAdmission, addressId: "foreign" } },
      { gate: { ...candidate.gate, passed: false } }]) expect(hasVerifiedCurrentScoreAdmission({ ...candidate, ...changed })).toBe(false);
  });
  it("enables the native rescore control from a complete current producer readback", async () => {
    // A dedicated integration invocation supplies bytes from the real local PG
    // Operator factory and governed registered-artifact metadata read.
    const path = process.env.ODAY_CANONICAL_SCORE_READBACK;
    const producer = path ? JSON.parse(readFileSync(path, "utf8")) : { network, scoring, candidateId: "second" };
    nav.search = `ws=network&tab=sitescore&candidate=${producer.candidateId}`;
    window.history.replaceState(null, "", `/operator?${nav.search}`);
    const candidate = producer.scoring.candidates.find((row: { id: string }) => row.id === producer.candidateId);
    expect(hasVerifiedCurrentScoreAdmission(candidate)).toBe(true);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("network-scoring") ? json(producer.scoring)
      : String(input).includes("network-listings") ? json(producer.network) : json({}, 403)));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId(`sitescore-card-${producer.candidateId}`);
    fireEvent.change(screen.getByLabelText(`重新評分原因 ${producer.candidateId}`), { target: { value: "Recorded reason" } });
    expect(screen.getByTestId(`sitescore-rescore-${producer.candidateId}`)).toBeEnabled();
  });
  it.each([null, [], { ...queued, actualVersion: 0 }, { ...queued, status: "SUCCEEDED" }, { ...queued, result: {} }, { ...queued, lastError: { code: {} } }])("rejects malformed receipt %j", (value) => { expect(hasScoreExecutionReceipt(value)).toBe(false); });
  it("accepts exact request facts and rejects fabricated context versions", () => { expect(hasScoreExecutionReceipt(queued)).toBe(true); expect(hasScoreExecutionReceipt(complete)).toBe(true); expect(hasScoreExecutionContext(context)).toBe(true); expect(hasScoreExecutionContext({ ...context, jobVersion: null })).toBe(false); });
  it("uses exact current base with a new request key and keeps saved report readable until committed readback", async () => {
    let published = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback());
      if (url.includes("execution-context")) return json(context);
      if (url.endsWith("/second/score")) return json(acceptedCommand(init, "create"), 202);
      if (url.includes("execution-requests")) { published = true; return json(complete); }
      if (url.includes("network-scoring")) return json(published ? { ...scoring, scorecards: scorecards.map((card) => card.id === "second" ? { ...card, reportId: "report-v2", reportVersion: 2, sitescoreRunId: "run-v2" } : card) } : scoring);
      if (url.includes("network-listings")) return json(network);
      return json({ detail: "denied" }, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    const button = await screen.findByTestId("sitescore-rescore-second");
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible(); expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded reason" } }); fireEvent.click(button);
    await screen.findByText(/評分工作 QUEUED/);
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
    const write = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/second/score"));
    expect(JSON.parse(write![1]!.body as string)).toEqual({ expectedReportId: "report-second", expectedReportVersion: 1, expectedJobVersion: 8, reason: "Recorded reason", expectedActorSubjectId: sessionActorA, expectedTenantId: contextTenant });
    expect((write![1]!.headers as Record<string, string>)["Idempotency-Key"]).toMatch(/^[a-f0-9-]{36}$/);
    await waitFor(() => expect(screen.getByTestId("sitescore-execution-status")).toHaveTextContent("SUCCEEDED"), { timeout: 3000 });
    expect(screen.getByTestId("sitescore-execution-status")).toHaveTextContent("報告版本 2");
  });
  it("cold-reloads a failed durable request and retries its own actual versions", async () => {
    const failed = { ...queued, status: "FAILED", created: false, actualVersion: 4, jobVersion: 5, lastError: { code: "recorded-failure", retryable: false } };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback());
      if (url.includes("execution-context")) return json({ ...context, activeExecutionRequest: failed });
      if (url.endsWith("/request-v2/retry")) return json(acceptedCommand(init, "retry", { ...queued, actualVersion: 5, jobVersion: 6 }), 202);
      if (url.includes("execution-requests")) return json({ ...queued, actualVersion: 6, jobVersion: 7, status: "RUNNING" });
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByText(/評分工作 FAILED/);
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded retry reason" } });
    fireEvent.click(screen.getByText("重試這次評分工作"));
    await screen.findByText(/評分工作 QUEUED/);
    const write = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/request-v2/retry"));
    expect(JSON.parse(write![1]!.body as string)).toEqual({ expectedVersion: 4, expectedJobVersion: 5, reason: "Recorded retry reason", expectedActorSubjectId: sessionActorA, expectedTenantId: contextTenant });
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/second/score"))).toBe(false);
  });

  it("discards a delayed create receipt after exact candidate URL changes", async () => {
    let resolveWrite: ((value: Response) => void) | undefined;
    const response = new Promise<Response>((resolve) => { resolveWrite = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input) === "/auth/session" ? json(sessionReadback()) : String(input).includes("execution-context") ? json(context)
      : String(input).endsWith("/second/score") ? response : String(input).includes("network-scoring") ? json(scoring)
        : String(input).includes("network-listings") ? json(network) : json({}, 403)));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("sitescore-rescore-second");
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded reason" } });
    fireEvent.click(screen.getByTestId("sitescore-rescore-second"));
    await waitFor(() => expect(screen.getByTestId("sitescore-rescore-second")).toBeDisabled());
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/second/score"))).toBe(true));
    nav.search = "ws=network&tab=sitescore&candidate=first";
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await act(async () => { resolveWrite!(json({ executionRequest: queued }, 202)); });
    expect(screen.queryByTestId("sitescore-execution-status")).not.toBeInTheDocument();
    expect(screen.getByTestId("sitescore-card-first")).toBeVisible();
  });

  it.each(["headers", "json"])("discards delayed cold-read %s after a newer request is accepted", async (phase) => {
    let resolveRestore: ((value: unknown) => void) | undefined;
    const restore = new Promise<unknown>((resolve) => { resolveRestore = resolve; });
    let bodyStarted = false;
    const restoreResponse = json({});
    restoreResponse.json = () => { bodyStarted = true; return restore; };
    let contextReads = 0;
    const oldFailed = { ...queued, executionRequestId: "old-request", jobId: "old-job", status: "FAILED", actualVersion: 4, jobVersion: 5 };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback());
      if (url.includes("execution-context")) return ++contextReads === 1 ? phase === "headers" ? restore : restoreResponse : json(context);
      if (url.endsWith("/second/score")) return json(acceptedCommand(init, "create"), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    }));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("sitescore-rescore-second");
    await waitFor(() => expect(contextReads).toBe(1));
    if (phase === "json") await waitFor(() => expect(bodyStarted).toBe(true));
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded reason" } });
    fireEvent.click(screen.getByTestId("sitescore-rescore-second"));
    await screen.findByText(/評分工作 QUEUED/);
    await act(async () => { const value = { ...context, activeExecutionRequest: oldFailed }; resolveRestore!(phase === "headers" ? json(value) : value); });
    expect(screen.getByTestId("sitescore-execution-status")).toHaveTextContent("QUEUED");
    expect(screen.getByTestId("sitescore-execution-status")).not.toHaveTextContent("FAILED");
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });

  it("rejects foreign request receipt without replacing saved report", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input) === "/auth/session" ? json(sessionReadback()) : String(input).includes("execution-context") ? json(context)
      : String(input).endsWith("/second/score") ? json(acceptedCommand(init, "create", { ...queued, candidateSiteId: "foreign" }), 202)
        : String(input).includes("network-scoring") ? json(scoring) : String(input).includes("network-listings") ? json(network) : json({}, 403)));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("sitescore-rescore-second");
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded reason" } }); fireEvent.click(screen.getByTestId("sitescore-rescore-second"));
    await screen.findByText(/重新評分請求未確認/);
    expect(screen.queryByTestId("sitescore-execution-status")).not.toBeInTheDocument(); expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
});

const sessionActorA = "11111111-1111-4111-8111-111111111111";
const sessionActorB = "22222222-2222-4222-8222-222222222222";
const contextTenant = "33333333-3333-4333-8333-333333333333";
const sessionReadback = (subject = sessionActorA) => ({ subject, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
function acceptedCommand(init: RequestInit | undefined, operation: "create" | "retry", executionRequest = queued,
  overrides: Record<string, unknown> = {}, actorSubjectId = sessionActorA) {
  const headers = init?.headers as Record<string, string>;
  return { executionRequest, commandReceipt: { operation, actorSubjectId, tenantId: contextTenant,
    idempotencyKey: headers["Idempotency-Key"], correlationId: headers["X-Correlation-Id"],
    executionRequestId: executionRequest.executionRequestId, jobId: executionRequest.jobId, ...overrides } };
}
async function startCanonicalScore() {
  await screen.findByTestId("sitescore-rescore-second");
  fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded reason" } });
  fireEvent.click(screen.getByTestId("sitescore-rescore-second"));
}

describe("canonical actor intent integrity", () => {
  it.each(["missing", "non-ok", "malformed", "expired"])("refuses %s actual auth session without posting", async (mode) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return mode === "missing" ? json({}) : mode === "non-ok" ? json(sessionReadback(), 401)
        : mode === "malformed" ? json({ subject: sessionActorA, expiresAt: true }) : json({ ...sessionReadback(), expiresAt: 1 });
      if (url.includes("execution-context")) return json({ ...context, tenantId: contextTenant });
      if (url.endsWith("/second/score")) return json(acceptedCommand(init, "create"), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/登入身分.*重新載入/);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
  it.each(["actorSubjectId", "tenantId", "idempotencyKey", "correlationId", "operation", "executionRequestId", "jobId"])("rejects a 202 with mismatched command %s", async (field) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback());
      if (url.includes("execution-context")) return json({ ...context, tenantId: contextTenant });
      if (url.endsWith("/second/score")) return json(acceptedCommand(init, "create", queued, { [field]: "foreign-command" }), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/重新評分請求未確認/);
    expect(screen.queryByTestId("sitescore-execution-status")).not.toBeInTheDocument();
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
  it("does not confirm accepted create after actual subject changes A to B", async () => {
    let posted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback(posted ? sessionActorB : sessionActorA));
      if (url.includes("execution-context")) return json({ ...context, tenantId: contextTenant });
      if (url.endsWith("/second/score")) { posted = true; return json(acceptedCommand(init, "create"), 202); }
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/登入身分.*重新載入/);
    expect(posted).toBe(true);
    expect(screen.queryByTestId("sitescore-execution-status")).not.toBeInTheDocument();
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
  it("pins actor A intent even when actor B changes the reason before another attempt", async () => {
    let subject = sessionActorA;
    let posts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback(subject));
      if (url.includes("execution-context")) return json({ ...context, tenantId: contextTenant });
      if (init?.method === "POST") { posts += 1; throw new Error("Lost response"); }
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/重新評分請求無法確認/);
    expect(posts).toBe(1);
    subject = sessionActorB;
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "A different reason" } });
    fireEvent.click(screen.getByTestId("sitescore-rescore-second"));
    await screen.findByText(/登入身分.*重新載入/);
    expect(posts).toBe(1);
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
  it("rejects contradictory recorded data gate states despite passed and current observations", () => {
    for (const state of ["hard", "geo", "needdata"]) {
      expect(hasVerifiedCurrentScoreAdmission({ ...scoringCandidates[1], gate: { ...scoringCandidates[1].gate, state, passed: true } })).toBe(false);
    }
  });
});

describe("canonical actor context and retry compatibility", () => {
  const failed = { ...queued, created: false, status: "CANCELLED", actualVersion: 4, jobVersion: 5,
    correlationId: "original-creator-a-correlation", lastError: null };
  it.each(["actorSubjectId", "tenantId", "idempotencyKey", "correlationId", "operation", "executionRequestId", "jobId"])("rejects mismatched retry command %s while retaining its shared cold read", async (field) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback(sessionActorB));
      if (url.includes("execution-context")) return json({ ...context, activeExecutionRequest: failed });
      if (url.endsWith("/request-v2/retry")) return json(acceptedCommand(init, "retry", { ...queued, actualVersion: 5, jobVersion: 6 }, { [field]: "foreign" }, sessionActorB), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByText(/評分工作 CANCELLED/);
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded retry reason" } });
    fireEvent.click(screen.getByText("重試這次評分工作"));
    await screen.findByText(/重試資格或工作版本未確認/);
    expect(screen.getByTestId("sitescore-execution-status")).toHaveTextContent("CANCELLED");
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });
  it("allows current B to retry A's shared request with B command facts and original request correlation", async () => {
    const retryRequest = { ...queued, actualVersion: 5, jobVersion: 6, correlationId: failed.correlationId };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback(sessionActorB));
      if (url.includes("execution-context")) return json({ ...context, activeExecutionRequest: failed });
      if (url.endsWith("/request-v2/retry")) return json(acceptedCommand(init, "retry", retryRequest, {}, sessionActorB), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByText(/評分工作 CANCELLED/);
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/auth/session")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded retry reason" } });
    fireEvent.click(screen.getByText("重試這次評分工作"));
    await screen.findByText(/評分工作 QUEUED/);
    const write = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/request-v2/retry"))!;
    expect(JSON.parse(write[1]!.body as string)).toEqual({ expectedVersion: 4, expectedJobVersion: 5,
      reason: "Recorded retry reason", expectedActorSubjectId: sessionActorB, expectedTenantId: contextTenant });
    expect((write[1]!.headers as Record<string, string>)["If-Actor-Match"]).toBe(sessionActorB);
    expect((write[1]!.headers as Record<string, string>)["X-Correlation-Id"]).not.toBe(retryRequest.correlationId);
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
  it("does not use fixture persona as a canonical principal when the actual session is missing", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubEnv("ODP_PRODUCT_MODE", "poc");
    expect(operatorFixturesAllowed()).toBe(true);
    window.sessionStorage.setItem("oday.operator.subject", sessionActorA);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json({}, 401);
      if (url.includes("execution-context")) return json(context);
      if (url.endsWith("/second/score")) return json(acceptedCommand(init, "create"), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/登入身分.*重新載入/);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
  it("blocks retry without actual session while still showing the authorized shared receipt", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json({ subject: sessionActorB, expiresAt: "invalid" });
      if (url.includes("execution-context")) return json({ ...context, activeExecutionRequest: failed });
      if (url.endsWith("/request-v2/retry")) return json(acceptedCommand(init, "retry"), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByText(/評分工作 CANCELLED/);
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Recorded retry reason" } });
    fireEvent.click(screen.getByText("重試這次評分工作"));
    await screen.findByText(/登入身分.*重新載入/);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(screen.getByTestId("sitescore-execution-status")).toHaveTextContent("CANCELLED");
  });
  it.each([undefined, null, [], { operation: "create", actorSubjectId: true }])("rejects missing or malformed 202 command sidecar %j", async (receipt) => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback());
      if (url.includes("execution-context")) return json(context);
      if (url.endsWith("/second/score")) return json({ executionRequest: queued, commandReceipt: receipt }, 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    }));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/重新評分請求未確認/);
    expect(screen.queryByTestId("sitescore-execution-status")).not.toBeInTheDocument();
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });
  it.each([undefined, null, true, ""])("refuses missing or malformed actual context tenant %j", async (tenantId) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback());
      if (url.includes("execution-context")) return json({ ...context, tenantId });
      if (url.endsWith("/second/score")) return json(acceptedCommand(init, "create"), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/租戶.*重新載入/);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });
  it("pins actual context tenant even if another attempt changes reason and context tenant", async () => {
    let tenantId = contextTenant;
    let posts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback());
      if (url.includes("execution-context")) return json({ ...context, tenantId });
      if (init?.method === "POST") { posts += 1; throw new Error("Lost response"); }
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/重新評分請求無法確認/);
    tenantId = "foreign-tenant";
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "Another reason" } });
    fireEvent.click(screen.getByTestId("sitescore-rescore-second"));
    await screen.findByText(/租戶.*重新載入/);
    expect(posts).toBe(1);
  });
  it("discards context-bound intent when the actual subject changes before POST", async () => {
    let contextRead = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json(sessionReadback(contextRead ? sessionActorB : sessionActorA));
      if (url.includes("execution-context")) {
        // Cold restore is a shared GET, preceding this command's initial subject probe.
        if (fetchMock.mock.calls.some(([path]) => String(path) === "/auth/session")) contextRead = true;
        return json(context);
      }
      if (url.endsWith("/second/score")) return json(acceptedCommand(init, "create"), 202);
      return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await startCanonicalScore();
    await screen.findByText(/登入身分.*重新載入/);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("重新評分原因 second"), { target: { value: "B changes reason" } });
    fireEvent.click(screen.getByTestId("sitescore-rescore-second"));
    await waitFor(() => expect(screen.getByTestId("sitescore-rescore-second")).not.toBeDisabled());
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });
});

it.each(["before-headers", "before-json", "after-headers", "after-json"])("discards %s actual session wait after exact candidate URL changes", async (phase) => {
  let resolveSession: ((value: unknown) => void) | undefined;
  const deferred = new Promise<unknown>((resolve) => { resolveSession = resolve; });
  let sessionReads = 0;
  let delayed = false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    if (url === "/auth/session") {
      const target = phase.startsWith("before") ? 1 : 3;
      if (++sessionReads === target) {
        delayed = true;
        if (phase.endsWith("headers")) return await deferred as Response;
        const response = json({});
        response.json = () => deferred;
        return response;
      }
      return json(sessionReadback());
    }
    if (url.includes("execution-context")) return json(context);
    if (url.endsWith("/second/score")) return json(acceptedCommand(init, "create"), 202);
    return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
  });
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
  await startCanonicalScore();
  await waitFor(() => expect(delayed).toBe(true));
  const postedBeforeNavigation = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").length;
  expect(postedBeforeNavigation).toBe(phase.startsWith("before") ? 0 : 1);
  nav.search = "ws=network&tab=sitescore&candidate=first";
  view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
  await act(async () => { resolveSession!(phase.endsWith("headers") ? json(sessionReadback()) : sessionReadback()); });
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(postedBeforeNavigation);
  expect(screen.queryByTestId("sitescore-execution-status")).not.toBeInTheDocument();
  expect(screen.getByTestId("sitescore-card-first")).toBeVisible();
});

it("replays the same actor and unchanged create intent after a lost response using the same key", async () => {
  let posts = 0;
  let active = false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/auth/session") return json(sessionReadback());
    if (url.includes("execution-context")) return json({ ...context, activeExecutionRequest: active ? queued : null });
    if (url.endsWith("/second/score")) {
      active = true;
      if (++posts === 1) throw new Error("Lost accepted create response");
      return json(acceptedCommand(init, "create", { ...queued, created: false }), 202);
    }
    return url.includes("network-scoring") ? json(scoring) : url.includes("network-listings") ? json(network) : json({}, 403);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
  await startCanonicalScore();
  await screen.findByText(/重新評分請求無法確認/);
  expect(posts).toBe(1);
  fireEvent.click(screen.getByTestId("sitescore-rescore-second"));
  await screen.findByText(/評分工作 QUEUED/);
  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(writes).toHaveLength(2);
  expect(writes[0][1]!.body).toBe(writes[1][1]!.body);
  expect((writes[0][1]!.headers as Record<string, string>)["Idempotency-Key"]).toBe((writes[1][1]!.headers as Record<string, string>)["Idempotency-Key"]);
  expect((writes[0][1]!.headers as Record<string, string>)["X-Correlation-Id"]).toBe((writes[1][1]!.headers as Record<string, string>)["X-Correlation-Id"]);
  expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
});
