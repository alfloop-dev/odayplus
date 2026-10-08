import type { NextRequest } from "next/server";
import { proxyApiRequest } from "../../../../lib/auth/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

async function handler(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  const { path } = await context.params;
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

