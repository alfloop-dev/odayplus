import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "../../../app/login/route";
import {
  readWebSession,
  sealWebSession,
  webSessionCookieName,
  type WebSession,
} from "../session";

const SECRET = "test-session-secret-with-at-least-32-bytes";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("password-first login route handler", () => {
  it("T09: renders password-first login HTML form by default and does not redirect to OAuth", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const request = new NextRequest("https://ops.oday.plus/login?returnTo=%2Foperator%3Ftab%3Dnetwork");
    const response = await GET(request);

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/html");
    expect(response.headers.get("location")).toBeNull();

    const html = await response.text();
    // Must contain password-first form fields
    expect(html).toContain('name="username"');
    expect(html).toContain('name="password"');
    expect(html).toContain('type="password"');
    expect(html).toContain('action="/login"');
    expect(html).toContain('name="returnTo" value="/operator?tab=network"');

    // T14: In default local mode, OIDC entry point is not rendered
    expect(html).not.toContain("使用 OIDC 登入");
    expect(html).not.toContain("accounts.google.com");
  });

  it("T09 & T14: shows optional OIDC entry only when OIDC is fully configured", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    vi.stubEnv("ODP_AUTH_MODE", "oidc");
    vi.stubEnv("ODP_WEB_OIDC_ISSUER", "https://accounts.google.com");
    vi.stubEnv("ODP_WEB_OIDC_CLIENT_ID", "web-client-id.apps.googleusercontent.com");
    vi.stubEnv("ODP_WEB_OIDC_CLIENT_SECRET", "GOCSPX-test-secret-value");

    const request = new NextRequest("https://ops.oday.plus/login?returnTo=%2Foperator");
    const response = await GET(request);

    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).toContain('name="username"');
    expect(html).toContain('name="password"');
    expect(html).toContain("使用 OIDC 登入");
    expect(html).toContain("provider=oidc");
  });

  it("redirects authenticated users away from /login to safe returnTo", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const now = Math.floor(Date.now() / 1000);
    const session: WebSession = {
      kind: "web-session",
      accessToken: "valid-token",
      tokenType: "Bearer",
      subject: "existing-user",
      sid: "session-1",
      issuedAt: now,
      expiresAt: now + 3600,
    };
    const cookie = await sealWebSession(session, SECRET);
    const request = new NextRequest("https://ops.oday.plus/login?returnTo=%2Foperator%3Fws%3Dgrowth");
    request.cookies.set(webSessionCookieName, cookie);

    const response = await GET(request);
    expect(response.status).toBe(307);
    const location = response.headers.get("location");
    expect(location).toContain("/operator?ws=growth");
  });

  it("fails closed when explicit OIDC flow is requested but OIDC is disabled", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    vi.stubEnv("ODP_AUTH_MODE", "local");

    const request = new NextRequest("https://ops.oday.plus/login?provider=oidc");
    const response = await GET(request);

    expect(response.status).toBe(503);
    const data = await response.json();
    expect(data).toMatchObject({
      error: { code: "WEB_AUTH_PROVIDER_DISABLED" },
    });
  });

  it("T11: rejects POST /login without valid CSRF origin", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const request = new NextRequest("https://ops.oday.plus/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        username: "admin",
        password: "Admin12345678!",
      }),
    });
    request.headers.set("origin", "https://attacker.example");

    const response = await POST(request);
    expect(response.status).toBe(403);
    const data = await response.json();
    expect(data).toMatchObject({
      error: { code: "CSRF_VERIFICATION_FAILED" },
    });
  });

  it("T10: successful POST /login sets secure HttpOnly session cookie and returns 200/303", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const request = new NextRequest("https://ops.oday.plus/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify({
        username: "admin",
        password: "Admin12345678!",
        returnTo: "/operator?ws=network",
      }),
    });
    request.headers.set("origin", "https://ops.oday.plus");

    const response = await POST(request);
    expect(response.status).toBe(200);

    const setCookie = response.headers.get("set-cookie");
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("__Host-oday_web_session=");
    expect(setCookie).toMatch(/httponly/i);
    expect(setCookie).toMatch(/secure/i);
    expect(setCookie).toMatch(/samesite=lax/i);
    expect(setCookie).toContain("Path=/");

    const json = await response.json();
    expect(json.ok).toBe(true);
    expect(json.subject).toBe("admin");
    expect(json.returnTo).toBe("/operator?ws=network");

    // T13: Security posture check: role, tenant, and tokens must NOT be placed in accessible client response payload for authorization
    expect(json.roles).toBeUndefined();
    expect(json.tenantId).toBeUndefined();
    expect(json.accessToken).toBeUndefined();

    // Verify the cookie contains an unforgeable server session with sid
    const cookieMatch = setCookie?.match(/__Host-oday_web_session=([^;]+)/);
    const cookieValue = cookieMatch?.[1];
    const decrypted = await readWebSession(cookieValue, { secret: SECRET });
    expect(decrypted).not.toBeNull();
    expect(decrypted?.subject).toBe("admin");
    expect(decrypted?.provider).toBe("local_password");
    expect(decrypted?.sid).toBeTruthy();
  });

  it("T12: redirects to safe target and sanitizes hostile returnTo on login", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const request = new NextRequest("https://ops.oday.plus/login", {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        username: "admin",
        password: "Admin12345678!",
        returnTo: "https://hostile.example/phish",
      }).toString(),
    });
    request.headers.set("origin", "https://ops.oday.plus");

    const response = await POST(request);
    expect(response.status).toBe(303);
    const location = response.headers.get("location");
    // Hostile returnTo must be sanitized to /operator
    expect(location).toContain("/operator");
    expect(location).not.toContain("hostile.example");
  });

  it("returns 401 AUTH_INVALID_CREDENTIALS on wrong password or unknown user without enumeration", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const request = new NextRequest("https://ops.oday.plus/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify({
        username: "nonexistent-user",
        password: "wrongpassword123!",
      }),
    });
    request.headers.set("origin", "https://ops.oday.plus");

    const response = await POST(request);
    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data).toMatchObject({
      error: {
        code: "AUTH_INVALID_CREDENTIALS",
      },
    });
  });
});
