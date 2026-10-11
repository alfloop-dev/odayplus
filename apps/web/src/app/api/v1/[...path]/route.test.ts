import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../../lib/auth/proxy", () => ({ proxyApiRequest: vi.fn() }));
import { proxyApiRequest } from "../../../../lib/auth/proxy";
import { GET, POST, PUT } from "./route";

const origin = "https://ops.oday.plus";
const path = "operator/users/dev-smoke-recovery";
const context = { params: Promise.resolve({ path: path.split("/") }) };
const proxy = vi.mocked(proxyApiRequest);

function request(method = "POST", body = "{}", headers: Record<string, string> = {}) {
  const req = new NextRequest(`${origin}/api/v1/${path}`, {
    method, headers: { "content-type": "application/json", cookie: "__Host-oday_web_session=sealed-reference", ...headers },
    ...(method === "GET" ? {} : { body }),
  });
  // NextRequest normalizes Origin at construction in this test runtime.
  req.headers.set("origin", origin);
  return req;
}

beforeEach(() => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("ODP_WEB_BASE_URL", origin);
  proxy.mockReset();
  proxy.mockResolvedValue(Response.json({ status: "safe" }, { headers: { "cache-control": "no-store" } }));
});
afterEach(() => vi.unstubAllEnvs());

describe("mounted recovery BFF boundary", () => {
  it.each(["https://attacker.example", ""])("refuses invalid CSRF %s before any upstream/provider", async (value) => {
    const req = request("POST", "private-malformed-input");
    req.headers.delete("origin");
    if (value) req.headers.set("origin", value);
    const response = await POST(req, context);
    expect(response.status).toBe(403);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.text()).not.toContain("private-malformed-input");
    expect(proxy).not.toHaveBeenCalled();
  });

  it("bounds the streamed JSON body before forwarding", async () => {
    const response = await POST(request("POST", "x".repeat(8193)), context);
    expect(response.status).toBe(413);
    expect(proxy).not.toHaveBeenCalled();
  });

  it("rejects query transport, media type and unsupported mutations", async () => {
    const query = new NextRequest(`${origin}/api/v1/${path}?token=private-query`);
    const response = await GET(query, context);
    expect(response.status).toBe(422);
    expect(await response.text()).not.toContain("private-query");
    expect((await POST(request("POST", "{}", { "content-type": "text/plain" }), context)).status).toBe(415);
    expect((await PUT(request("PUT"), context)).status).toBe(405);
    expect(proxy).not.toHaveBeenCalled();
  });

  it("preserves memory body and sealed reference for the actual authenticated proxy", async () => {
    const raw = JSON.stringify({ action: "consume", password: "offline-private-marker" });
    const response = await POST(request("POST", raw), context);
    expect(response.status).toBe(200);
    expect(proxy).toHaveBeenCalledOnce();
    const [forwarded, target] = proxy.mock.calls[0];
    expect(target).toBe(`/api/v1/${path}`);
    expect(forwarded.headers.get("cookie")).toBe("__Host-oday_web_session=sealed-reference");
    expect(await forwarded.text()).toBe(raw);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.text()).not.toContain("offline-private-marker");
  });
});
