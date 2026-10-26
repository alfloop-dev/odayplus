import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../../../app/auth/callback/route";

const SECRET = "test-session-secret-with-at-least-32-bytes";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("OIDC callback route", () => {
  it("T14: fails closed with 503 WEB_AUTH_PROVIDER_DISABLED when OIDC is not enabled", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    vi.stubEnv("ODP_AUTH_MODE", "local");

    const request = new NextRequest(
      "https://ops.oday.plus/auth/callback?code=mock-code&state=mock-state",
    );
    const response = await GET(request);

    expect(response.status).toBe(503);
    const json = await response.json();
    expect(json).toMatchObject({
      error: { code: "WEB_AUTH_PROVIDER_DISABLED" },
    });
  });

  it("returns 401 on missing code or state when OIDC is configured", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    vi.stubEnv("ODP_AUTH_MODE", "oidc");
    vi.stubEnv("ODP_WEB_OIDC_ISSUER", "https://accounts.google.com");
    vi.stubEnv("ODP_WEB_OIDC_CLIENT_ID", "client-id.apps.googleusercontent.com");
    vi.stubEnv("ODP_WEB_OIDC_CLIENT_SECRET", "GOCSPX-test-secret-value");

    const request = new NextRequest("https://ops.oday.plus/auth/callback");
    const response = await GET(request);

    expect(response.status).toBe(401);
    const json = await response.json();
    expect(json).toMatchObject({
      error: { code: "OIDC_CALLBACK_INVALID" },
    });
  });
});
