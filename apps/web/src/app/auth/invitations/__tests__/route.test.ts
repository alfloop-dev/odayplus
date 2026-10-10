import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "../route";
import { resolveGoogleMetadataIdentityToken } from "../../../../lib/auth/cloudRunIdentity";

vi.mock("../../../../lib/auth/cloudRunIdentity", () => ({
  resolveGoogleMetadataIdentityToken: vi.fn(),
}));

const ORIGIN = "https://console.example.com";
const INVITATION = "ea011bb5-af6d-4e7a-8e84-245c16153a29";
const BODY = { invitation_id: INVITATION, token: "a".repeat(43),
  username: "release.smoke", password: "Private-Unlogged-Password-7319" };
const RECEIPT = { status: "accepted", invitation_id: INVITATION,
  account_id: "32997913-4662-4c90-8d78-16dd6d320b4e",
  tenant_id: "e34f2117-de4b-478c-82fd-13c4ef428d42",
  audit_event_id: "3f2d9a7b-2a79-4887-bdc0-b96c3f1d3a14" };

function request(body: unknown = BODY, headers: Record<string, string> = {}, suffix = ""): NextRequest {
  return new NextRequest(`${ORIGIN}/auth/invitations${suffix}`, {
    method: "POST", headers: { "content-type": "application/json", origin: ORIGIN, ...headers },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  vi.stubEnv("ODP_PRODUCT_MODE", "production");
  vi.stubEnv("ODP_WEB_BASE_URL", ORIGIN);
  vi.stubEnv("ODP_API_BASE_URL", "https://private-api.example.com");
  vi.stubEnv("ODP_API_SERVICE_AUDIENCE", "https://private-api.example.com");
  vi.mocked(resolveGoogleMetadataIdentityToken).mockResolvedValue("server-only-transport");
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.clearAllMocks(); });

describe("bounded invitation capability acceptance BFF (no login or credential output)", () => {
  it("uses canonical service transport, strips browser identity, returns identifiers without a cookie", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...RECEIPT,
      token: BODY.token, password: BODY.password }), { status: 201 }));
    vi.stubGlobal("fetch", fetcher);
    const result = await POST(request(BODY, {
      authorization: "Bearer browser-forgery", "x-serverless-authorization": "Bearer forged",
      cookie: "__Host-oday_web_session=original-cookie", "x-subject-id": "forged-actor",
      "x-tenant-id": "forged-tenant", "x-roles": "operations_manager", "x-operator-role": "ops-lead",
    }));
    expect(result.status).toBe(201);
    expect(result.headers.get("cache-control")).toBe("no-store");
    expect(result.headers.has("set-cookie")).toBe(false);
    expect(await result.json()).toEqual(RECEIPT);
    const [url, options] = fetcher.mock.calls[0];
    expect(String(url)).toBe("https://private-api.example.com/api/v1/auth/invitations/accept");
    expect(options).toMatchObject({ method: "POST", redirect: "manual", cache: "no-store" });
    expect(options.signal).toBeInstanceOf(AbortSignal);
    expect(JSON.parse(options.body)).toEqual(BODY);
    expect(Object.fromEntries(options.headers.entries())).toEqual({
      "content-type": "application/json", "x-serverless-authorization": "Bearer server-only-transport",
    });
  });

  it.each([undefined, "https://attacker.example.com"])("refuses absent/foreign Origin before transport: %s", async (origin) => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const req = request();
    if (origin) req.headers.set("origin", origin); else req.headers.delete("origin");
    const result = await POST(req);
    expect(result.status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
    expect(resolveGoogleMetadataIdentityToken).not.toHaveBeenCalled();
  });

  it.each([
    { ...BODY, actor: "Private-secret" }, { ...BODY, tenant_id: "Private-secret" },
    { ...BODY, roles: ["platform_admin"] }, { ...BODY, password: { secret: "Private-secret" } },
    { ...BODY, token: "Private-secret" }, { ...BODY, display_name: "s".repeat(256) },
    { ...BODY, password: "s".repeat(1025) }, [BODY], null,
  ])("strict validation rejects secret-bearing malformed fields without echo", async (body) => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const response = await POST(request(body));
    expect(response.status).toBe(422);
    const text = await response.text();
    for (const secret of [BODY.password, BODY.token, "Private-secret"]) expect(text).not.toContain(secret);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("refuses query capabilities, non-JSON, malformed JSON and oversize streams", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    expect((await POST(request(BODY, {}, "?token=private-secret"))).status).toBe(422);
    expect((await POST(request(BODY, { "content-type": "text/plain" }))).status).toBe(415);
    for (const body of ['{"private-secret":', '"' + "s".repeat(9000) + '"']) {
      const response = await POST(new NextRequest(`${ORIGIN}/auth/invitations`, {
        method: "POST", headers: { origin: ORIGIN, "content-type": "application/json" }, body,
      }));
      expect(response.status).toBe(422);
      expect(await response.text()).not.toContain("private-secret");
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    [409, "INVITATION_UNAVAILABLE"], [409, "INVITATION_ACCOUNT_EXISTS"],
    [422, "INVITATION_PASSWORD_REJECTED"], [429, "INVITATION_RATE_LIMITED"],
    [503, "IDENTITY_PERSISTENCE_UNAVAILABLE"],
  ])("projects only safe expected upstream error %s %s", async (status, code) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code, input: BODY, summary: BODY.password }, token: BODY.token,
    }), { status: status as number })));
    const response = await POST(request());
    expect(response.status).toBe(status);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ error: { code } });
  });

  it.each([
    [201, { ...RECEIPT, invitation_id: RECEIPT.account_id }],
    [201, { ...RECEIPT, account_id: BODY.password }], [201, { status: "accepted" }],
    [302, { token: BODY.token }], [422, { error: { code: BODY.password } }],
    [409, { error: { code: "INVITATION_RATE_LIMITED" } }], [200, RECEIPT],
  ])("rejects mismatched/malformed/upstream redirect responses %s", async (status, body) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })));
    const response = await POST(request());
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: { code: "WEB_AUTH_UNAVAILABLE" } });
  });

  it("fails closed on transport, upstream and timeout errors without printing secrets", async () => {
    const log = vi.spyOn(console, "error");
    vi.mocked(resolveGoogleMetadataIdentityToken).mockRejectedValueOnce(new Error(BODY.password));
    expect((await POST(request())).status).toBe(503);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error(BODY.token)));
    expect((await POST(request())).status).toBe(503);
    expect(log).not.toHaveBeenCalled(); log.mockRestore();
  });

  it.each(["", "http://private-api.example.com", ORIGIN, "https://user:password@private-api.example.com"])(
    "refuses missing/insecure/self/credential URL config %s", async (url) => {
      vi.stubEnv("ODP_API_BASE_URL", url);
      const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
      expect((await POST(request())).status).toBe(503);
      expect(fetcher).not.toHaveBeenCalled();
    });
});
