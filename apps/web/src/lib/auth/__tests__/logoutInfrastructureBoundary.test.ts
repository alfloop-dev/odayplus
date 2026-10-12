import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST as logout } from "../../../app/auth/logout/route";
import { GET as inspect } from "../../../app/auth/session/route";
import { readWebSession, sealLegacyWebSession, sealWebSessionReference, webSessionCookieName } from "../session";
import { MockSessionStore, setSessionStoreForTests } from "../sessionStore";

const SECRET = "OFFLINE-infrastructure-session-secret-at-least-32-bytes";
afterEach(() => { setSessionStoreForTests(undefined); vi.restoreAllMocks(); vi.unstubAllEnvs(); });
function request(cookie: string, path: string) {
  const req = new NextRequest("https://offline.example" + path, { method: path.endsWith("logout") ? "POST" : "GET" });
  req.headers.set("origin", "https://offline.example");
  req.headers.set("accept", "application/json");
  req.cookies.set(webSessionCookieName, cookie);
  return req;
}
function runtime() {
  vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("ODP_PRODUCT_MODE", "production");
  vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
  vi.stubEnv("ODP_WEB_BASE_URL", "https://offline.example");
}

describe("session infrastructure is not absence/revocation", () => {
  it.each(["missing-key", "short-key", "missing-store"])("%s refuses logout success; restoring configuration is not revocation", async (failure) => {
    runtime();
    const store = new MockSessionStore(); // explicit memory fixture, NOT PG proof
    setSessionStoreForTests(store);
    const sid = "offline-infrastructure-" + failure;
    await store.createSession({ sessionId: sid, accountId: "offline", provider: "oidc", accessToken: "offline-only",
      idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000 });
    const now = Math.floor(Date.now() / 1000);
    const cookie = await sealWebSessionReference({ kind: "web-session", sid, provider: "oidc", issuedAt: now, expiresAt: now + 3600 });
    expect(await readWebSession(cookie)).not.toBeNull();
    if (failure === "missing-store") setSessionStoreForTests(null);
    else vi.stubEnv("ODP_WEB_SESSION_SECRET", failure === "short-key" ? "short" : undefined);
    const result = await logout(request(cookie, "/auth/logout"));
    expect(result.status).toBe(503);
    expect(result.headers.get("set-cookie")).toBeNull();
    expect(await store.validateSession(sid)).not.toBeNull();
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET); setSessionStoreForTests(store);
    expect(await readWebSession(cookie)).not.toBeNull(); // truthful uncertainty, never false cleanup
    expect((await logout(request(cookie, "/auth/logout"))).status).toBe(200);
    expect(await readWebSession(cookie)).toBeNull();
  });

  it("key derivation failure returns static503, never success or private provider error", async () => {
    runtime(); const store = new MockSessionStore(); setSessionStoreForTests(store);
    const sid = "offline-derive-key"; const now = Math.floor(Date.now() / 1000);
    await store.createSession({ sessionId: sid, accountId: "offline", provider: "oidc", accessToken: "offline",
      idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000 });
    const cookie = await sealWebSessionReference({ kind: "web-session", sid, issuedAt: now, expiresAt: now + 3600 });
    vi.spyOn(crypto.subtle, "deriveKey").mockRejectedValue(new Error("PRIVATE-PROVIDER-MARKER"));
    const response = await logout(request(cookie, "/auth/logout"));
    expect(response.status).toBe(503); expect(response.headers.get("set-cookie")).toBeNull();
    expect(await response.text()).not.toContain("PRIVATE-PROVIDER-MARKER");
    expect(await store.validateSession(sid)).not.toBeNull();
  });

  it("unresolvable cookie under a different valid-length sealing key never acknowledges cleanup", async () => {
    runtime(); const store = new MockSessionStore(); setSessionStoreForTests(store);
    const sid = "offline-wrong-key"; const now = Math.floor(Date.now() / 1000);
    await store.createSession({ sessionId: sid, accountId: "offline", provider: "oidc", accessToken: "offline",
      idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000 });
    const cookie = await sealWebSessionReference({ kind: "web-session", sid, issuedAt: now, expiresAt: now + 3600 });
    vi.stubEnv("ODP_WEB_SESSION_SECRET", "DIFFERENT-OFFLINE-key-also-at-least-thirty-two-bytes");
    expect(await readWebSession(cookie)).toBeNull(); // AEAD cannot distinguish invalid cookie vs wrong configured key
    const response = await logout(request(cookie, "/auth/logout"));
    expect(response.status).toBe(401); expect(response.headers.get("set-cookie")).toBeNull();
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    expect(await readWebSession(cookie)).not.toBeNull();
    expect((await logout(request(cookie, "/auth/logout"))).status).toBe(200);
    expect(await readWebSession(cookie)).toBeNull();
  });

  it("accepted production legacy OIDC cookie cannot remigrate after successful logout", async () => {
    runtime();
    const store = new MockSessionStore();
    store.oidcAccounts.set("offline-federated", { accountId: "offline", subject: "offline" });
    setSessionStoreForTests(store);
    const now = Math.floor(Date.now() / 1000);
    const cookie = await sealLegacyWebSession({ kind: "web-session", subject: "offline-federated",
      provider: "oidc", accessToken: "offline-only", issuedAt: now, expiresAt: now + 3600 });
    const first = await readWebSession(cookie);
    expect(first?.legacyUpgrade).toBe(true);
    expect((await logout(request(cookie, "/auth/logout"))).status).toBe(200);
    expect(await readWebSession(cookie)).toBeNull();
    expect((await inspect(request(cookie, "/auth/session"))).status).toBe(401);
    expect(store.sessions.size).toBe(1);
  });
});
