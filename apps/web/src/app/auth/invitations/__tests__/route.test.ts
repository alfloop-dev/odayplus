import { runInNewContext } from "node:vm";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "../route";
import { config, middleware } from "../../../../middleware";
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
  const req = new NextRequest(`${ORIGIN}/auth/invitations${suffix}`, {
    method: "POST", headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  // happy-dom removes forbidden Origin during Request construction. Set it
  // after construction, as the existing password/login route suites do.
  req.headers.set("origin", headers.origin ?? ORIGIN);
  return req;
}

beforeEach(() => {
  vi.stubEnv("ODP_PRODUCT_MODE", "production");
  vi.stubEnv("ODP_WEB_BASE_URL", ORIGIN);
  vi.stubEnv("ODP_API_BASE_URL", "https://private-api.example.com");
  vi.stubEnv("ODP_API_SERVICE_AUDIENCE", "https://private-api.example.com");
  vi.mocked(resolveGoogleMetadataIdentityToken).mockResolvedValue("server-only-transport");
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.clearAllMocks(); });

describe("actual manual acceptance handler: committed-response uncertainty", () => {
  async function mountHandler(fetcher: ReturnType<typeof vi.fn>) {
    const response = await GET(new NextRequest(`${ORIGIN}/auth/invitations`));
    const html = await response.text();
    const script = /<script>([\s\S]*?)<\/script>/.exec(html)?.[1];
    expect(script).toBeTruthy();
    const button = { disabled: false };
    const result = { textContent: "" };
    let submit!: (event: { preventDefault(): void }) => Promise<void>;
    const form = { hidden: false, reset: vi.fn(), querySelector: () => button,
      addEventListener: (_name: string, handler: typeof submit) => { submit = handler; } };
    runInNewContext(script!, {
      document: { getElementById: (id: string) => id === "accept-invitation" ? form : result },
      FormData: class { entries() { return Object.entries(BODY)[Symbol.iterator](); } },
      fetch: fetcher,
    }, { timeout: 1000 });
    return { form, button, result, submit: () => submit({ preventDefault() {} }) };
  }

  it.each(["lost-reply", "503", "malformed-json", "malformed-201", "wrong-invitation", "unknown-422"])(
    "holds %s as unknown and rejects duplicate events without another POST", async (outcome) => {
      const fetcher = vi.fn().mockImplementation(async () => {
        if (outcome === "lost-reply") throw new Error(BODY.password);
        if (outcome === "503") return new Response(JSON.stringify({ error: { code: "WEB_AUTH_UNAVAILABLE" } }), { status: 503 });
        if (outcome === "malformed-json") return new Response("not-json", { status: 201 });
        if (outcome === "malformed-201") return new Response(JSON.stringify({ status: "accepted" }), { status: 201 });
        if (outcome === "wrong-invitation") return new Response(JSON.stringify({ ...RECEIPT, invitation_id: RECEIPT.account_id }), { status: 201 });
        return new Response(JSON.stringify({ error: { code: "unrecognized" } }), { status: 422 });
      });
      const page = await mountHandler(fetcher);
      await page.submit();
      expect(page.result.textContent).toContain("結果尚未確認");
      expect(page.result.textContent).not.toContain(BODY.password);
      expect(page.result.textContent).not.toContain(BODY.token);
      expect(page.button.disabled).toBe(true);
      expect(page.form.reset).not.toHaveBeenCalled();
      await page.submit();
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );

  it("fences duplicate submissions while awaiting a response and after confirmed success", async () => {
    let resolve!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((done) => { resolve = done; }));
    const page = await mountHandler(fetcher);
    const pending = page.submit();
    await page.submit();
    expect(fetcher).toHaveBeenCalledTimes(1);
    resolve(new Response(JSON.stringify(RECEIPT), { status: 201 }));
    await pending;
    expect(page.form.hidden).toBe(true);
    expect(page.form.reset).toHaveBeenCalledTimes(1);
    expect(page.result.textContent).toContain("帳號已建立");
    await page.submit();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("permits explicit correction only after a recognized noncommitting validation refusal", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
      error: { code: "INVITATION_PASSWORD_REJECTED" },
    }), { status: 422 })).mockResolvedValueOnce(new Response(JSON.stringify(RECEIPT), { status: 201 }));
    const page = await mountHandler(fetcher);
    await page.submit();
    expect(page.button.disabled).toBe(false);
    expect(page.result.textContent).toContain("本次輸入未通過驗證，未執行建立帳號");
    await page.submit();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(page.form.hidden).toBe(true);
  });

  it("holds a definite unavailable invitation without falsely claiming it was not accepted", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: "INVITATION_UNAVAILABLE" },
    }), { status: 409 }));
    const page = await mountHandler(fetcher);
    await page.submit();
    expect(page.button.disabled).toBe(true);
    expect(page.result.textContent).toContain("可能已使用");
    expect(page.result.textContent).not.toContain("尚未建立帳號");
    await page.submit();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("bounded invitation capability acceptance BFF (no login or credential output)", () => {
  it("serves private manual entry with no-store, CSP, no cookies and no URL prefill", async () => {
    const response = await GET(new NextRequest(`${ORIGIN}/auth/invitations`));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.get("content-security-policy")).toContain("script-src 'sha256-");
    expect(response.headers.has("set-cookie")).toBe(false);
    const html = await response.text();
    for (const field of ["invitation_id", "token", "username", "password"]) expect(html).toContain(`name="${field}"`);
    expect(html).toContain("form.reset()");
    expect(html).not.toContain("localStorage");
    expect(html).not.toContain("sessionStorage");
    expect(html).not.toContain(BODY.token);
  });

  it("refuses GET capability query strings without reflecting secrets", async () => {
    const response = await GET(new NextRequest(`${ORIGIN}/auth/invitations?token=private-secret`));
    expect(response.status).toBe(422);
    expect(await response.text()).not.toContain("private-secret");
  });
  const middlewareApplies = (path: string) => config.matcher.some(
    (pattern) => new RegExp(`^${pattern}$`).test(path),
  );

  it("exempts only the capability endpoint, not nearby auth paths or protected pages", () => {
    expect(middlewareApplies("/auth/invitations")).toBe(false);
    for (const path of ["/operator", "/auth/password", "/auth/invitations-extra", "/auth/invitations/accept"]) {
      expect(middlewareApplies(path)).toBe(true);
    }
  });

  it.each([undefined, "existing-issuer-cookie"])(
    "composes production matcher/handler without redirecting or clearing cookie %s", async (cookie) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(RECEIPT), { status: 201 })));
      const req = request();
      if (cookie) req.cookies.set("__Host-oday_web_session", cookie);
      let response;
      if (middlewareApplies(req.nextUrl.pathname)) {
        const gated = await middleware(req);
        response = gated.headers.get("x-middleware-next") === "1" ? await POST(req) : gated;
      } else {
        response = await POST(req);
      }
      expect(response.status).toBe(201);
      expect(response.headers.has("set-cookie")).toBe(false);
      expect(response.headers.has("location")).toBe(false);
      expect(await response.json()).toEqual(RECEIPT);
    },
  );

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

  it("binds canonical UUID receipts to case-insensitive input identifiers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(RECEIPT), { status: 201 })));
    expect((await POST(request({ ...BODY, invitation_id: INVITATION.toUpperCase() }))).status).toBe(201);
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
      const req = new NextRequest(`${ORIGIN}/auth/invitations`, {
        method: "POST", headers: { "content-type": "application/json" }, body,
      });
      req.headers.set("origin", ORIGIN);
      const response = await POST(req);
      expect(response.status).toBe(422);
      expect(await response.text()).not.toContain("private-secret");
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    [409, "INVITATION_UNAVAILABLE"], [409, "INVITATION_ACCOUNT_EXISTS"], [409, "INVITATION_PENDING_EXISTS"],
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
