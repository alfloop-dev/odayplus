import { NextRequest, type NextResponse } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { config, middleware } from "../../../middleware";
import { GET as sessionGET } from "../../../app/auth/session/route";
import { POST as logoutPOST } from "../../../app/auth/logout/route";
import { sealWebSessionReference, webSessionCookieName } from "../session";
import { MockSessionStore, setSessionStoreForTests } from "../sessionStore";

// Composes the production matcher, the middleware and the route handlers the
// way Next.js does for a deployed request, so the live dev-admin gate contract
// (anonymous and revoked GET /auth/session answer 401, never a followed /login
// redirect) is exercised against the real middleware instead of a fixture.

const SECRET = "test-session-secret-with-at-least-32-bytes";
const ORIGIN = "https://ops.oday.plus";
let store: MockSessionStore;

const matchers = config.matcher.map((pattern) => new RegExp(`^${pattern}$`));

function middlewareApplies(pathname: string): boolean {
  return matchers.some((matcher) => matcher.test(pathname));
}

type Handler = (request: NextRequest) => Promise<NextResponse>;
const routes: Record<string, Handler> = {
  "/auth/session": sessionGET,
  "/auth/logout": logoutPOST,
};

async function dispatch(request: NextRequest): Promise<Response> {
  if (middlewareApplies(request.nextUrl.pathname)) {
    const gated = await middleware(request);
    if (gated.headers.get("x-middleware-next") !== "1") return gated;
  }
  const route = routes[request.nextUrl.pathname];
  // A protected page that passed middleware renders; model that as a plain 200.
  return route ? route(request) : new Response("page", { status: 200 });
}

function withCookie(path: string, cookie: string | undefined, method = "GET") {
  const request = new NextRequest(`${ORIGIN}${path}`, { method });
  if (cookie) request.cookies.set(webSessionCookieName, cookie);
  return request;
}

async function liveSessionCookie(): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  await store.createSession({
    sessionId: "session-admin",
    accountId: "account-admin",
    provider: "oidc",
    accessToken: "real-access-token",
    subject: "platform-admin",
    idleTimeoutMs: 30 * 60 * 1000,
    absoluteLifetimeMs: 8 * 60 * 60 * 1000,
  });
  return sealWebSessionReference(
    {
      kind: "web-session",
      sid: "session-admin",
      issuedAt: now,
      expiresAt: now + 600,
      provider: "oidc",
    },
    SECRET,
  );
}

beforeEach(() => {
  store = new MockSessionStore();
  setSessionStoreForTests(store);
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("ODP_PRODUCT_MODE", "production");
  vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
  vi.stubEnv("ODP_WEB_BASE_URL", ORIGIN);
});

afterEach(() => {
  setSessionStoreForTests(undefined);
  vi.unstubAllEnvs();
});

describe("production session probe through middleware", () => {
  it("leaves /auth/session to its own handler but still gates protected pages", () => {
    expect(middlewareApplies("/auth/session")).toBe(false);
    expect(middlewareApplies("/operator")).toBe(true);
    expect(middlewareApplies("/auth/password")).toBe(true);
    expect(middlewareApplies("/auth/sessionless")).toBe(true);
  });

  it("answers an anonymous probe with 401 rather than a login redirect", async () => {
    const response = await dispatch(withCookie("/auth/session", undefined));
    expect(response.status).toBe(401);
    expect(response.headers.get("location")).toBeNull();
    expect((await response.json()).error.code).toBe("WEB_SESSION_REQUIRED");

    const page = await dispatch(withCookie("/operator", undefined));
    expect(page.status).toBe(307);
    expect(new URL(page.headers.get("location") as string).pathname).toBe("/login");
  });

  it("refuses a replayed cookie after logout revokes the durable session", async () => {
    const cookie = await liveSessionCookie();

    const live = await dispatch(withCookie("/auth/session", cookie));
    expect(live.status).toBe(200);
    expect((await live.json()).subject).toBe("platform-admin");
    expect((await dispatch(withCookie("/operator", cookie))).status).toBe(200);

    const logoutRequest = withCookie("/auth/logout", cookie, "POST");
    logoutRequest.headers.set("origin", ORIGIN);
    logoutRequest.headers.set("accept", "application/json");
    const logout = await dispatch(logoutRequest);
    expect(logout.status).toBe(200);
    expect(store.sessions.get("session-admin")?.revokedAt).toBeInstanceOf(Date);

    const replay = await dispatch(withCookie("/auth/session", cookie));
    expect(replay.status).toBe(401);
    expect(replay.headers.get("location")).toBeNull();
    expect((await replay.json()).error.code).toBe("WEB_SESSION_REQUIRED");

    const page = await dispatch(withCookie("/operator", cookie));
    expect(page.status).toBe(307);
    expect(new URL(page.headers.get("location") as string).pathname).toBe("/login");
  });
});
