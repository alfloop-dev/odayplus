import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { middleware } from "../../../middleware";
import {
  sealWebSession,
  webSessionCookieName,
  type WebSession,
} from "../session";

const SECRET = "test-session-secret-with-at-least-32-bytes";

afterEach(() => {
  vi.unstubAllEnvs();
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
    const session: WebSession = {
      kind: "web-session",
      accessToken: "real-access-token",
      tokenType: "Bearer",
      subject: "user-123",
      issuedAt: now,
      expiresAt: now + 600,
    };
    const request = new NextRequest("https://ops.oday.plus/operator");
    request.cookies.set(
      webSessionCookieName,
      await sealWebSession(session, SECRET),
    );

    const response = await middleware(request);
    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });
});

