import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { middleware, runtime } from "../../../middleware";
import {
  sealWebSessionReference,
  webSessionCookieName,
} from "../session";
import {
  MockSessionStore,
  setSessionStoreForTests,
} from "../sessionStore";

const SECRET = "test-session-secret-with-at-least-32-bytes";
let testSessionStore: MockSessionStore;

afterEach(() => {
  setSessionStoreForTests(undefined);
  vi.unstubAllEnvs();
});

beforeEach(() => {
  testSessionStore = new MockSessionStore();
  setSessionStoreForTests(testSessionStore);
});

describe("production protected-route middleware", () => {
  it("redirects to login and preserves the requested relative route", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const response = await middleware(
      new NextRequest(
        "https://ops.oday.plus/operator?workspace=network&view=list",
      ),
    );

    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location") as string);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("returnTo")).toBe(
      "/operator?workspace=network&view=list",
    );
  });

  it("allows a request carrying a live encrypted session", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const now = Math.floor(Date.now() / 1000);
    const session = {
      kind: "web-session" as const,
      sid: "session-123",
      issuedAt: now,
      expiresAt: now + 600,
      provider: "oidc" as const,
    };
    await testSessionStore.createSession({
      sessionId: "session-123",
      accountId: "account-123",
      provider: "oidc",
      accessToken: "real-access-token",
      subject: "user-123",
      idleTimeoutMs: 30 * 60 * 1000,
      absoluteLifetimeMs: 8 * 60 * 60 * 1000,
    });
    const request = new NextRequest("https://ops.oday.plus/operator");
    request.cookies.set(
      webSessionCookieName,
      await sealWebSessionReference(session, SECRET),
    );

    const response = await middleware(request);
    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("middleware runtime pin", () => {
  // The session lookup goes through PostgresSessionStore, which dynamically
  // imports `pg`. `pg` cannot load in the Edge Runtime, so an Edge middleware
  // would reject every readWebSession call, swallow the error, and redirect
  // every authenticated request to /login. It is also the source of the 28
  // "Edge Runtime" build warnings that Gate 0 C1 (zero build warnings) tracks.
  it("runs on the Node.js runtime so the durable session lookup can load pg", () => {
    expect(runtime).toBe("nodejs");
  });
});
