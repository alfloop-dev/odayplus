import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "../../../app/auth/logout/route";
import {
  sealWebSession,
  webSessionCookieName,
  type WebSession,
} from "../session";

const SECRET = "test-session-secret-with-at-least-32-bytes";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("logout route", () => {
  it("T10: clears web session cookie with maxAge=0 and redirects to /login", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const now = Math.floor(Date.now() / 1000);
    const session: WebSession = {
      kind: "web-session",
      accessToken: "token",
      tokenType: "Bearer",
      subject: "user-1",
      sid: "sid-1",
      issuedAt: now,
      expiresAt: now + 3600,
    };
    const cookie = await sealWebSession(session, SECRET);

    const request = new NextRequest("https://ops.oday.plus/auth/logout");
    request.headers.set("origin", "https://ops.oday.plus");
    request.cookies.set(webSessionCookieName, cookie);

    const response = await GET(request);
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toContain("/login");

    const setCookie = response.headers.get("set-cookie");
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("__Host-oday_web_session=");
    expect(setCookie).toContain("Max-Age=0");
  });

  it("handles POST logout and returns JSON or redirect", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const request = new NextRequest("https://ops.oday.plus/auth/logout", {
      method: "POST",
      headers: {
        accept: "application/json",
      },
    });

    const response = await POST(request);
    expect(response.status).toBe(200);
    const json = await response.json();
    expect(json.ok).toBe(true);

    const setCookie = response.headers.get("set-cookie");
    expect(setCookie).toContain("Max-Age=0");
  });
});
