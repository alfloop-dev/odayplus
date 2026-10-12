import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST as logout } from "../../../app/auth/logout/route";
import { GET as inspect } from "../../../app/auth/session/route";
import { readWebSession, sealWebSessionReference, webSessionCookieName } from "../session";
import { MockSessionStore, setSessionStoreForTests } from "../sessionStore";
import { mintLocalJwt } from "../localJwt";
import { openJson } from "../crypto";
import { isProductionWebRuntime } from "../runtime";

vi.mock("../localJwt", async (original) => ({
  ...await original<typeof import("../localJwt")>(),
  mintLocalJwt: vi.fn(),
}));

afterEach(() => {
  setSessionStoreForTests(undefined);
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

function request(cookie: string, path: string): NextRequest {
  const req = new NextRequest("https://offline.example" + path, {
    method: path.endsWith("logout") ? "POST" : "GET",
  });
  // Maintained NextRequest/Happy DOM harness requires post-constructor headers.
  req.headers.set("origin", "https://offline.example");
  req.headers.set("accept", "application/json");
  req.cookies.set(webSessionCookieName, cookie);
  return req;
}

describe("logout refresh availability is not durable revocation", () => {
  it.each(["mint", "update"])("%s failure refuses cleanup success; restored availability cannot resurrect successful logout", async (failure) => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("ODP_PRODUCT_MODE", "production");
    vi.stubEnv("ODP_WEB_SESSION_SECRET", "OFFLINE-session-secret-at-least-thirty-two-bytes");
    vi.stubEnv("ODP_WEB_BASE_URL", "https://offline.example");
    const store = new MockSessionStore(); // explicit fixture, NOT PG proof
    setSessionStoreForTests(store);
    const sid = "offline-session-" + failure;
    await store.createSession({ sessionId: sid, accountId: "offline-account", provider: "local_password",
      accessToken: "expired-needs-refresh", idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000 });
    const now = Math.floor(Date.now() / 1000);
    const cookie = await sealWebSessionReference({ kind: "web-session", sid, provider: "local_password",
      issuedAt: now, expiresAt: now + 3600 });
    const healthyBearer = "e30." + Buffer.from(JSON.stringify({ exp: now + 3600 })).toString("base64url") + ".offline";
    vi.mocked(mintLocalJwt).mockResolvedValue(healthyBearer);
    expect(isProductionWebRuntime()).toBe(true);
    expect(Boolean(await openJson(cookie, "web-session"))).toBe(true);
    expect(Boolean(request(cookie, "/auth/logout").cookies.get(webSessionCookieName)?.value)).toBe(true);
    expect(await store.validateSession(sid)).not.toBeNull();
    const update = vi.spyOn(store, "updateAccessToken");
    if (failure === "mint") vi.mocked(mintLocalJwt).mockRejectedValue(new Error("PRIVATE-REFRESH-MARKER"));
    else update.mockRejectedValue(new Error("PRIVATE-REFRESH-MARKER"));
    const first = await logout(request(cookie, "/auth/logout"));
    expect(first.status).toBe(503);
    expect(first.headers.get("set-cookie")).toBeNull();
    expect(await first.text()).not.toContain("PRIVATE-REFRESH-MARKER");
    expect(await store.validateSession(sid)).not.toBeNull();
    expect((await inspect(request(cookie, "/auth/session"))).status).toBe(401);
    // Restored refresh shows transient refusal was NOT proof of revocation.
    vi.mocked(mintLocalJwt).mockResolvedValue(healthyBearer);
    update.mockRestore();
    expect(await readWebSession(cookie)).not.toBeNull();
    const second = await logout(request(cookie, "/auth/logout"));
    expect(second.status).toBe(200);
    expect(await store.validateSession(sid)).toBeNull();
    expect(await readWebSession(cookie)).toBeNull();
    expect((await inspect(request(cookie, "/auth/session"))).status).toBe(401);
  });
});
