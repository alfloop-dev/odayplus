import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildUpstreamHeaders, proxyApiRequest } from "../proxy";
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

describe("production BFF", () => {
  it("strips browser identity and injects the session bearer", () => {
    const browserHeaders = new Headers({
      authorization: "Bearer attacker-token",
      "x-subject-id": "attacker",
      "x-tenant-id": "attacker-tenant",
      "x-roles": "admin",
      "x-operator-role": "ops-lead",
      "idempotency-key": "idem-1",
      "content-type": "application/json",
    });
    const result = buildUpstreamHeaders({
      requestHeaders: browserHeaders,
      accessToken: "real-access-token",
      allowLegacyIdentity: false,
    });

    expect(result.get("authorization")).toBe("Bearer real-access-token");
    expect(result.get("x-subject-id")).toBeNull();
    expect(result.get("x-tenant-id")).toBeNull();
    expect(result.get("x-roles")).toBeNull();
    expect(result.get("x-operator-role")).toBe("ops-lead");
    expect(result.get("idempotency-key")).toBe("idem-1");
  });

  it("forwards through the configured upstream with only session identity", async () => {
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
    const cookie = await sealWebSession(session, SECRET);
    const fetchMock = vi.fn(async (_url: URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("authorization")).toBe("Bearer real-access-token");
      expect(headers.get("x-subject-id")).toBeNull();
      expect(headers.get("x-tenant-id")).toBeNull();
      expect(headers.get("x-roles")).toBeNull();
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "https://web.example/api/v1/operator/bootstrap?view=today",
      {
        headers: {
          "x-subject-id": "attacker",
          "x-tenant-id": "attacker-tenant",
          "x-roles": "admin",
        },
      },
    );
    request.cookies.set(webSessionCookieName, cookie);
    expect(request.cookies.get(webSessionCookieName)?.value).toBe(cookie);
    await expect(readWebSession(cookie)).resolves.toEqual(session);

    const response = await proxyApiRequest(
      request,
      "/api/v1/operator/bootstrap",
      {
        NODE_ENV: "production",
        ODP_API_BASE_URL: "https://api.internal.example",
      },
    );

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "https://api.internal.example/api/v1/operator/bootstrap?view=today",
    );
  });

  it("fails closed before contacting the upstream without a session", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxyApiRequest(
      new NextRequest("https://web.example/api/v1/operator/bootstrap"),
      "/api/v1/operator/bootstrap",
      {
        NODE_ENV: "production",
        ODP_API_BASE_URL: "https://api.internal.example",
      },
    );

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
