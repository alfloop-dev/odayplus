import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  resolveGoogleMetadataIdentityToken,
} from "../cloudRunIdentity";
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
      "x-serverless-authorization": "Bearer attacker-service-token",
      "idempotency-key": "idem-1",
      "content-type": "application/json",
    });
    const result = buildUpstreamHeaders({
      requestHeaders: browserHeaders,
      accessToken: "real-access-token",
      allowLegacyIdentity: false,
      serviceIdentityToken: "real-service-token",
    });

    expect(result.get("authorization")).toBe("Bearer real-access-token");
    expect(result.get("x-serverless-authorization")).toBe(
      "Bearer real-service-token",
    );
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
      expect(headers.get("x-serverless-authorization")).toBe(
        "Bearer service-identity-token",
      );
      expect(headers.get("x-subject-id")).toBeNull();
      expect(headers.get("x-tenant-id")).toBeNull();
      expect(headers.get("x-roles")).toBeNull();
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: {
          "content-type": "application/json",
          "x-serverless-authorization": "Bearer must-not-leak",
        },
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
        ODP_API_SERVICE_AUDIENCE: "https://api.internal.example",
      },
      {
        resolveServiceIdentityToken: async (audience) => {
          expect(audience).toBe("https://api.internal.example");
          return "service-identity-token";
        },
      },
    );

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(response.headers.get("x-serverless-authorization")).toBeNull();
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

  it("fails closed when the production API base URL is missing", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const cookie = await productionSessionCookie();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "https://web.example/api/v1/operator/bootstrap",
    );
    request.cookies.set(webSessionCookieName, cookie);

    const response = await proxyApiRequest(
      request,
      "/api/v1/operator/bootstrap",
      {
        NODE_ENV: "production",
        ODP_API_SERVICE_AUDIENCE: "https://api.internal.example",
      },
      { resolveServiceIdentityToken: async () => "a.b.c" },
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "WEB_API_NOT_CONFIGURED" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed when the Cloud Run service audience is missing", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const cookie = await productionSessionCookie();
    const resolveToken = vi.fn();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "https://web.example/api/v1/operator/bootstrap",
    );
    request.cookies.set(webSessionCookieName, cookie);

    const response = await proxyApiRequest(
      request,
      "/api/v1/operator/bootstrap",
      {
        NODE_ENV: "production",
        ODP_API_BASE_URL: "https://api.internal.example",
      },
      { resolveServiceIdentityToken: resolveToken },
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "WEB_SERVICE_IDENTITY_NOT_CONFIGURED" },
    });
    expect(resolveToken).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed when metadata cannot issue a service identity", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const cookie = await productionSessionCookie();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "https://web.example/api/v1/operator/bootstrap",
    );
    request.cookies.set(webSessionCookieName, cookie);

    const response = await proxyApiRequest(
      request,
      "/api/v1/operator/bootstrap",
      {
        NODE_ENV: "production",
        ODP_API_BASE_URL: "https://api.internal.example",
        ODP_API_SERVICE_AUDIENCE: "https://api.internal.example",
      },
      {
        resolveServiceIdentityToken: async () => {
          throw new Error("metadata unavailable");
        },
      },
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error: {
        code: "WEB_SERVICE_IDENTITY_UNAVAILABLE",
        retryable: true,
      },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes an upstream 404 through without substituting data", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const cookie = await productionSessionCookie();
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ error: { code: "NOT_FOUND" } }), {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "https://web.example/api/v1/operator/missing",
    );
    request.cookies.set(webSessionCookieName, cookie);

    const response = await proxyApiRequest(
      request,
      "/api/v1/operator/missing",
      {
        NODE_ENV: "production",
        ODP_API_BASE_URL: "https://api.internal.example",
        ODP_API_SERVICE_AUDIENCE: "https://api.internal.example",
      },
      { resolveServiceIdentityToken: async () => "a.b.c" },
    );

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({
      error: { code: "NOT_FOUND" },
    });
  });

  it("obtains a metadata identity token without exposing it to the browser", async () => {
    const fetchMock = vi.fn(async (url: URL, init?: RequestInit) => {
      expect(url.hostname).toBe("metadata.google.internal");
      expect(url.searchParams.get("audience")).toBe(
        "https://api.internal.example",
      );
      expect(url.searchParams.get("format")).toBe("full");
      expect(new Headers(init?.headers).get("metadata-flavor")).toBe("Google");
      return new Response("header.payload.signature", { status: 200 });
    });

    await expect(
      resolveGoogleMetadataIdentityToken(
        "https://api.internal.example",
        fetchMock as typeof fetch,
      ),
    ).resolves.toBe("header.payload.signature");
  });
});

async function productionSessionCookie(): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  return sealWebSession(
    {
      kind: "web-session",
      accessToken: "real-access-token",
      tokenType: "Bearer",
      subject: "user-123",
      issuedAt: now,
      expiresAt: now + 600,
    },
    SECRET,
  );
}
