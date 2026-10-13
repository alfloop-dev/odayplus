import { NextRequest } from "next/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Boundary-order probes: no real SID/provider/upstream authority is used.
vi.mock("../session", async (original) => ({
  ...await original<typeof import("../session")>(), readWebSession: vi.fn(),
}));
vi.mock("../cloudRunIdentity", () => ({ resolveGoogleMetadataIdentityToken: vi.fn() }));
import { readWebSession } from "../session";
import { resolveGoogleMetadataIdentityToken } from "../cloudRunIdentity";
import * as generic from "../../../app/api/v1/[...path]/route";
import * as root from "../../../app/api/v1/route";
import * as avm from "../../../app/avm/[...path]/route";

const origin = "https://ops.oday.plus";
const upstream = vi.fn();
const session = vi.mocked(readWebSession);
const service = vi.mocked(resolveGoogleMetadataIdentityToken);
const methods = ["POST", "PUT", "PATCH", "DELETE"] as const;
const targets = ["generic", "root", "avm"] as const;
const context = { params: Promise.resolve({ path: ["jobs", "offline-job", "retries"] }) };
function request(method: string, suppliedOrigin: string | null, path: string) {
  const req = new NextRequest(`${origin}${path}`, { method }); // Bodyless/simple request.
  req.cookies.set("__Host-oday_web_session", "synthetic-sealed-reference");
  req.headers.delete("origin");
  if (suppliedOrigin !== null) req.headers.set("origin", suppliedOrigin);
  return req;
}
function invoke(target: typeof targets[number], method: typeof methods[number], req: NextRequest) {
  if (target === "root") return root[method](req);
  return (target === "avm" ? avm : generic)[method](req, context);
}
beforeEach(() => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("ODP_WEB_BASE_URL", origin);
  vi.stubEnv("ODP_API_BASE_URL", "https://offline.invalid");
  vi.stubEnv("ODP_API_SERVICE_AUDIENCE", "https://offline.invalid");
  session.mockReset(); service.mockReset(); upstream.mockReset();
  session.mockResolvedValue({ kind: "web-session", provider: "local_password", sid: "offline-sid",
    subject: "offline-user", accountId: "offline-account", accessToken: "offline-token", issuedAt: 1, expiresAt: 2 ** 40 });
  service.mockResolvedValue("offline-service-token");
  upstream.mockResolvedValue(Response.json({ ok: true }));
  vi.stubGlobal("fetch", upstream);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it.each(targets.flatMap(target => methods.flatMap(method =>
  ["https://sibling.oday.plus", "null", null].map(badOrigin => [target, method, badOrigin] as const)
)))("%s %s rejects Origin=%s BEFORE cookie/provider/upstream", async (target, method, badOrigin) => {
  const response = await invoke(target, method, request(method, badOrigin, "/api/v1/jobs/offline-job/retries"));
  expect(response.status).toBe(403);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(await response.text()).not.toContain("synthetic-sealed-reference");
  expect(session).not.toHaveBeenCalled(); expect(service).not.toHaveBeenCalled(); expect(upstream).not.toHaveBeenCalled();
});
it.each(targets.flatMap(target => methods.map(method => [target, method] as const)))(
  "%s %s preserves same-origin authenticated forwarding", async (target, method) => {
    const response = await invoke(target, method, request(method, origin, "/api/v1/jobs/offline-job/retries"));
    expect(response.status).toBe(200);
    expect(session).toHaveBeenCalledOnce(); expect(service).toHaveBeenCalledOnce(); expect(upstream).toHaveBeenCalledOnce();
    const headers = new Headers(upstream.mock.calls[0][1].headers);
    expect(headers.get("authorization")).toBe("Bearer offline-token");
    expect(upstream.mock.calls[0][1].method).toBe(method);
  });
