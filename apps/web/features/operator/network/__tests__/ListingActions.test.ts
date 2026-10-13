import { createOdpApiClient } from "@oday-plus/openapi-client";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { proxyApiRequest } from "../../../../src/lib/auth/proxy";
import { sealWebSessionReference, webSessionCookieName } from "../../../../src/lib/auth/session";
import { MockSessionStore, setSessionStoreForTests } from "../../../../src/lib/auth/sessionStore";
import type { Listing } from "../../types";
import { listingWriteReceiptMatches, resolveListingAction } from "../listingActions";
import { listingsApi, toListingApiError } from "../listingsClient";

const source: Listing = { id: "source/uuid", sourceId: "source", heatZoneId: null, address: "Source",
  status: "duplicate", rentPerMonth: 30000, areaPing: 25, geocodeConfidence: null, hardRuleFailures: [],
  duplicateOfId: "target-uuid", version: 7, hasLegalHold: false, tenantId: "recorded-tenant" };
const target: Listing = { ...source, id: "target-uuid", address: "Target", status: "new", duplicateOfId: undefined, version: 9 };
const context = { ready: true, fixturesAllowed: false, candidates: [] };
afterEach(() => { setSessionStoreForTests(undefined); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("exact current recorded Radar write projection", () => {
  it("retains exact generic source/target revisions regardless of order", () => {
    expect(resolveListingAction(source.id, [target, source], "expansion-manager", context)).toMatchObject({
      kind: "merge", sourceListingId: source.id, targetListingId: target.id, ifMatch: 'W/"7"', targetIfMatch: 'W/"9"' });
  });
  it.each(["missing-target", "self", "duplicate-id", "terminal-source", "terminal-target", "merged-target", "cycle",
    "candidate", "multiple-candidates", "unknown-candidates", "hold", "unknown-hold", "no-version", "bool-version",
    "other-tenant", "unknown-hardrule"])("refuses %s without choosing a fallback", (scenario) => {
    const a = { ...source }; const b = { ...target }; let rows = [a, b]; let candidates: unknown = [];
    if (scenario === "missing-target") rows = [a];
    if (scenario === "self") a.duplicateOfId = a.id;
    if (scenario === "duplicate-id") rows.push({ ...a });
    if (scenario === "terminal-source") a.status = "archived";
    if (scenario === "terminal-target") b.status = "archived";
    if (scenario === "merged-target") b.mergedIntoId = "other";
    if (scenario === "cycle") b.duplicateOfId = a.id;
    if (scenario === "candidate") a.candidateId = "candidate";
    if (scenario === "multiple-candidates") candidates = [{ id: "one", listingId: b.id }, { id: "two", listingId: b.id }];
    if (scenario === "unknown-candidates") candidates = null;
    if (scenario === "hold") b.hasLegalHold = true;
    if (scenario === "unknown-hold") b.hasLegalHold = undefined;
    if (scenario === "no-version") a.version = null;
    if (scenario === "bool-version") a.version = true as unknown as number;
    if (scenario === "other-tenant") b.tenantId = "different-tenant";
    if (scenario === "unknown-hardrule") a.hardRuleFailures = null;
    expect(resolveListingAction(a.id, rows, "expansion-manager", { ...context, candidates })).toBeNull();
  });
  it("retains explicit fixture positive without manufacturing revision", () => {
    const action = resolveListingAction(source.id, [{ ...source, version: null }, { ...target, version: null }],
      "expansion-manager", { ...context, fixturesAllowed: true });
    expect(action?.kind).toBe("merge"); expect(action?.ifMatch).toBeUndefined(); expect(action?.targetIfMatch).toBeUndefined();
    expect(resolveListingAction(source.id, [source, target], "expansion-staff", context)).toBeNull();
  });
  it("forwards actual versions, encodes ID, and retains logical key across attempts with fresh correlations", async () => {
    const calls = vi.fn(async () => new Response(JSON.stringify({ source, target }), { status: 200 }));
    const client = createOdpApiClient({ baseUrl: "https://synthetic.example.invalid", fetchImpl: calls });
    if (!client) throw new Error("Synthetic SDK client unavailable");
    const input = { targetListingId: target.id, actorRoleId: "expansion-manager", reason: "Actual operator reason",
      riskSummary: "Actual rendered disclosure", riskAcknowledged: true, idempotencyKey: "logical-request",
      ifMatch: 'W/"7"', targetIfMatch: 'W/"9"', ifActorMatch: "actual-synthetic-subject" };
    await listingsApi.merge(client, source.id, input); await listingsApi.merge(client, source.id, input);
    const [first, second] = calls.mock.calls as unknown as [string, RequestInit][];
    expect(first[0]).toContain("source%2Fuuid/merge");
    expect(first[1].headers).toMatchObject({ "If-Match": 'W/"7"', "Idempotency-Key": "logical-request", "If-Actor-Match": "actual-synthetic-subject" });
    expect(JSON.parse(String(first[1].body))).toMatchObject({ targetListingId: target.id, targetIfMatch: 'W/"9"', expectedActorSubjectId: "actual-synthetic-subject" });
    expect(new Headers(first[1].headers).get("x-correlation-id"))
      .not.toBe(new Headers(second[1].headers).get("x-correlation-id"));
    await listingsApi.archive(client, source.id, { actorRoleId: "expansion-manager", reason: "Entered archive reason",
      idempotencyKey: "archive-request", ifMatch: 'W/"7"', ifActorMatch: "actual-synthetic-subject" });
    const last = calls.mock.calls[2] as unknown as [string, RequestInit];
    expect(last[0]).toContain("source%2Fuuid/archive"); expect(last[1].headers).toMatchObject({ "If-Match": 'W/"7"', "If-Actor-Match": "actual-synthetic-subject" });
    expect(JSON.parse(String(last[1].body)).reason).toBe("Entered archive reason");
    expect(JSON.parse(String(last[1].body)).expectedActorSubjectId).toBe("actual-synthetic-subject");
  });
  it("does not claim a timed-out mutation was not written", () => {
    expect(toListingApiError(new DOMException("Lost response", "AbortError")).summary).toContain("尚無法確認");
  });
  it("production BFF preserves the actor constraint body when switching to the actual session bearer", async () => {
    const actorA = "00000000-0000-0000-0000-000000000101";
    const actorB = "00000000-0000-0000-0000-000000000103";
    const secret = "synthetic-nav02-session-secret-at-least-32-bytes";
    vi.stubEnv("ODP_WEB_SESSION_SECRET", secret);
    const store = new MockSessionStore(); setSessionStoreForTests(store);
    await store.createSession({ sessionId: "synthetic-actor-b-session", accountId: "synthetic-actor-b-account", provider: "oidc",
      accessToken: "synthetic-actor-b-bearer", subject: actorB, idleTimeoutMs: 1800000, absoluteLifetimeMs: 28800000 });
    const cookie = await sealWebSessionReference({ kind: "web-session", sid: "synthetic-actor-b-session", provider: "oidc",
      issuedAt: Math.floor(Date.now() / 1000), expiresAt: Math.floor(Date.now() / 1000) + 600 }, secret);
    const mock = vi.fn(async (_url: URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("authorization")).toBe("Bearer synthetic-actor-b-bearer");
      expect(headers.get("if-actor-match")).toBeNull();
      expect(JSON.parse(new TextDecoder().decode(init?.body as ArrayBuffer)).expectedActorSubjectId).toBe(actorA);
      return new Response(JSON.stringify({ detail: "ACTOR_CONTEXT_CONFLICT" }), { status: 409 });
    });
    vi.stubGlobal("fetch", mock);
    const request = new NextRequest("https://web.example.invalid/api/v1/operator/network-listings/listings/source-uuid/merge", {
      method: "POST", headers: { "content-type": "application/json", "If-Actor-Match": actorA },
      body: JSON.stringify({ expectedActorSubjectId: actorA }),
    });
    request.cookies.set(webSessionCookieName, cookie);
    const response = await proxyApiRequest(request, "/api/v1/operator/network-listings/listings/source-uuid/merge", {
      NODE_ENV: "production", ODP_API_BASE_URL: "https://api.example.invalid", ODP_API_SERVICE_AUDIENCE: "https://api.example.invalid",
      ODP_WEB_SESSION_SECRET: secret,
    }, { resolveServiceIdentityToken: async () => "synthetic-service-identity-token" });
    expect(response.status).toBe(409); expect(mock).toHaveBeenCalledOnce();
  });
  it("requires the exact successful merge receipt and recorded reason", () => {
    const action = resolveListingAction(source.id, [source, target], "expansion-manager", context)!;
    const reason = "Actual reason";
    const expected = { actorSubjectId: "actual-synthetic-subject", idempotencyKey: "actual-logical-key", fixturesAllowed: false, riskSummary: "Actual risk", riskAcknowledged: true };
    const receipt = { source: { ...source, mergedIntoId: target.id, mergeReason: reason }, target,
      actorSubjectId: expected.actorSubjectId, actorRoleId: "site_reviewer", idempotencyKey: expected.idempotencyKey,
      auditEvent: { id: "persisted-audit", action: "listing.merge", targetId: target.id, actorName: expected.actorSubjectId, actorRoleId: "site_reviewer",
        metadata: { reason, sourceListingId: source.id, targetListingId: target.id, riskSummary: expected.riskSummary, riskAcknowledged: true,
          before: { sourceVersion: 7, targetVersion: 9 }, after: { sourceVersion: 7, targetVersion: 9 } } } };
    expect(listingWriteReceiptMatches(action, receipt, reason, expected)).toBe(true);
    expect(listingWriteReceiptMatches(action, { ...receipt, source: { ...receipt.source, id: "different-source" } }, reason, expected)).toBe(false);
    expect(listingWriteReceiptMatches(action, receipt, "Different reason", expected)).toBe(false);
    expect(listingWriteReceiptMatches(action, { source: receipt.source, target }, reason, expected)).toBe(false);
  });
  it.each(["wrong-actor", "consistent-wrong-actor", "wrong-source-version", "wrong-target-version", "wrong-source-association",
    "wrong-source-tenant", "wrong-target-tenant", "wrong-logical-key", "wrong-audit-role",
    "wrong-target-association", "wrong-disclosure", "unacknowledged", "missing-actor", "missing-disclosure"])
    ("refuses a successful receipt with %s", (control) => {
      const action = resolveListingAction(source.id, [source, target], "expansion-manager", context)!;
      const reason = "Actual reason"; const actor = "00000000-0000-0000-0000-000000000101";
      const expected = { actorSubjectId: actor, idempotencyKey: "actual-logical-key", fixturesAllowed: false, riskSummary: "Actually rendered risk", riskAcknowledged: true };
      const receipt = { source: { ...source, mergedIntoId: target.id, mergeReason: reason }, target: { ...target },
        actorSubjectId: actor, actorRoleId: "site_reviewer", idempotencyKey: expected.idempotencyKey,
        auditEvent: { id: "persisted-audit", action: "listing.merge", targetId: target.id, actorName: actor, actorRoleId: "site_reviewer",
          metadata: { reason, sourceListingId: source.id, targetListingId: target.id,
            before: { sourceVersion: 7, targetVersion: 9 }, after: { sourceVersion: 7, targetVersion: 9 },
            riskSummary: expected.riskSummary, riskAcknowledged: true } } };
      if (control === "wrong-actor") receipt.auditEvent.actorName = "other-real-actor";
      if (control === "consistent-wrong-actor") receipt.actorSubjectId = receipt.auditEvent.actorName = "other-real-actor";
      if (control === "wrong-source-version") receipt.source.version = 8;
      if (control === "wrong-target-version") receipt.target.version = 99;
      if (control === "wrong-source-association") receipt.auditEvent.metadata.sourceListingId = "other-source";
      if (control === "wrong-target-association") receipt.auditEvent.metadata.targetListingId = "other-target";
      if (control === "wrong-source-tenant") receipt.source.tenantId = "foreign-tenant";
      if (control === "wrong-target-tenant") receipt.target.tenantId = "foreign-tenant";
      if (control === "wrong-logical-key") receipt.idempotencyKey = "other-logical-key";
      if (control === "wrong-audit-role") receipt.auditEvent.actorRoleId = "executive";
      if (control === "wrong-disclosure") receipt.auditEvent.metadata.riskSummary = "Different risk";
      if (control === "unacknowledged") receipt.auditEvent.metadata.riskAcknowledged = false;
      if (control === "missing-actor") { delete (receipt as Partial<typeof receipt>).actorSubjectId; delete (receipt.auditEvent as Partial<typeof receipt.auditEvent>).actorName; }
      if (control === "missing-disclosure") delete (receipt.auditEvent.metadata as Partial<typeof receipt.auditEvent.metadata>).riskSummary;
      expect(listingWriteReceiptMatches(action, receipt, reason, expected)).toBe(false);
    });
});
