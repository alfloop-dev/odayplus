import { NextRequest } from "next/server";
import { proxyApiRequest } from "../../../../lib/auth/proxy";
import { verifyCsrfOrigin } from "../../../../lib/auth/runtime";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

async function handler(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  const { path } = await context.params;
  if (path.join("/") === "operator/users/dev-smoke-recovery") {
    const refuse = (status: number, code: string) => Response.json(
      { error: { code } }, { status, headers: { "cache-control": "no-store" } },
    );
    if (request.nextUrl.search) return refuse(422, "RECOVERY_INPUT_INVALID");
    if (!["GET", "POST"].includes(request.method)) return refuse(405, "RECOVERY_METHOD_REFUSED");
    if (request.method === "POST") {
      // Cookie-authenticated recovery never relies on SameSite alone. No body,
      // identity token provider or upstream request runs on a CSRF refusal.
      if (!verifyCsrfOrigin(request)) return refuse(403, "RECOVERY_CSRF_REFUSED");
      if (request.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/json") {
        return refuse(415, "RECOVERY_JSON_REQUIRED");
      }
      const chunks: Uint8Array[] = [];
      let size = 0;
      const reader = request.body?.getReader();
      if (!reader) return refuse(422, "RECOVERY_INPUT_INVALID");
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 8192) {
            void reader.cancel().catch(() => undefined);
            return refuse(413, "RECOVERY_BODY_TOO_LARGE");
          }
          chunks.push(value);
        }
      } catch {
        return refuse(422, "RECOVERY_INPUT_INVALID");
      } finally {
        reader.releaseLock();
      }
      const body = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
      request = new NextRequest(request.url, { method: "POST", headers: request.headers, body });
    }
  }
  const response = await proxyApiRequest(
    request,
    `/api/v1/${path.map(encodeURIComponent).join("/")}`,
  );
  if (request.method === "GET" && path.join("/") === "platform/release-identity" && response.ok) {
    // Use the SAME authenticated upstream resolver as business writes. Report
    // the Web revision separately; never trust the CLI's independent API URL.
    const payload = await response.json();
    const headers = new Headers(response.headers);
    headers.set("cache-control", "no-store");
    headers.delete("content-length");
    headers.delete("content-encoding");
    return Response.json({
      ...payload,
      web_release_sha: process.env.ODAY_RELEASE_SHA ?? process.env.ODP_RELEASE_COMMIT_SHA ?? "",
      web_release_profile: process.env.ODP_RELEASE_PROFILE?.trim() || "full",
      web_manifest_digest: process.env.ODP_RELEASE_MANIFEST_DIGEST ?? "",
    }, { status: response.status, headers });
  }
  return response;
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
export const HEAD = handler;
export const OPTIONS = handler;

