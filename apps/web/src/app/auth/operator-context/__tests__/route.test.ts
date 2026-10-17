import { NextRequest } from "next/server";
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { GET } from "../route";
import { GET as sessionGET } from "../../session/route";
import { config } from "../../../../middleware";
import { sealWebSessionReference, webSessionCookieName } from "../../../../lib/auth/session";
import { MockSessionStore, setSessionStoreForTests } from "../../../../lib/auth/sessionStore";
const SECRET = "synthetic-operator-context-secret-at-least-32-bytes";
let store: MockSessionStore;
const request = (cookie?: string) => { const r = new NextRequest("https://ops.example.invalid/auth/operator-context"); if (cookie) r.cookies.set(webSessionCookieName, cookie); return r; };
async function cookie(tenantId: string | undefined = "tenant-a", accountId = "actor-a") {
  await store.createSession({ sessionId: "synthetic-session", accountId, tenantId, provider: "oidc", accessToken: "server-only-synthetic-bearer", subject: "display-subject", idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000 });
  return sealWebSessionReference({ kind: "web-session", sid: "synthetic-session", issuedAt: Math.floor(Date.now() / 1000), expiresAt: Math.floor(Date.now() / 1000) + 600, provider: "oidc" }, SECRET);
}
beforeEach(() => { store = new MockSessionStore(); setSessionStoreForTests(store); vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("ODP_PRODUCT_MODE", "production"); vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET); });
afterEach(() => { setSessionStoreForTests(undefined); vi.unstubAllEnvs(); });
describe("canonical actor and tenant context", () => {
  it("reads only canonical account/tenant/expiry and preserves the existing session response", async () => {
    const c = await cookie(); const r = await GET(request(c)); const body = await r.json();
    expect(r.status).toBe(200); expect(r.headers.get("cache-control")).toBe("no-store");
    expect(Object.keys(body).sort()).toEqual(["actorSubjectId", "expiresAt", "tenantId"]);
    expect(body).toMatchObject({ actorSubjectId: "actor-a", tenantId: "tenant-a" });
    expect(JSON.stringify(body)).not.toMatch(/synthetic-session|server-only|display-subject/);
    const old = await sessionGET(request(c)); expect(Object.keys(await old.json()).sort()).toEqual(["expiresAt", "subject"]);
  });
  it.each(["missing", "revoked", "tenant_missing", "store_unavailable"])("fails closed for %s", async (kind) => {
    const c = kind === "missing" ? undefined : await cookie(kind === "tenant_missing" ? undefined : "tenant-a");
    // Explicit undefined uses the function default; clear the actual canonical
    // record to represent a session without verified tenant authority.
    if (kind === "tenant_missing") (await store.validateSession("synthetic-session"))!.tenantId = undefined;
    if (kind === "revoked") await store.revokeSession("synthetic-session", "test");
    if (kind === "store_unavailable") vi.spyOn(store, "validateSession").mockRejectedValue(new Error("synthetic store unavailable"));
    const r = await GET(request(c)); expect(r.status).toBe(401); expect((await r.json()).error.code).toBe("OPERATOR_CONTEXT_REQUIRED");
  });
  it("observes a changed canonical tenant with the same actor instead of display hints", async () => {
    const c = await cookie(); expect((await (await GET(request(c))).json()).tenantId).toBe("tenant-a");
    (await store.validateSession("synthetic-session"))!.tenantId = "tenant-b";
    expect(await (await GET(request(c))).json()).toMatchObject({ actorSubjectId: "actor-a", tenantId: "tenant-b" });
  });
  it("lets the context handler return its own 401 and still gates similar protected paths", () => {
    const patterns = config.matcher.map(p => new RegExp(`^${p}$`));
    expect(patterns.some(p => p.test("/auth/operator-context"))).toBe(false);
    expect(patterns.some(p => p.test("/auth/operator-contextual"))).toBe(true);
    expect(patterns.some(p => p.test("/w/operations/interventions/case-a"))).toBe(true);
  });
});
