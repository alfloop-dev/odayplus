import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { resolveEndSessionEndpoint } from "../../../lib/auth/oidc";
import {
  readWebSession,
  webSessionCookieName,
  webSessionCookieOptions,
} from "../../../lib/auth/session";
import { isOidcEnabled, resolveWebBaseUrl } from "../../../lib/auth/runtime";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function logout(request: NextRequest): Promise<NextResponse> {
  const baseUrl = resolveWebBaseUrl(request.nextUrl.origin);
  const session = await readWebSession(
    request.cookies.get(webSessionCookieName)?.value,
  ).catch(() => null);

  let endpoint: string | null = null;
  if (session?.provider === "oidc") {
    try {
      if (isOidcEnabled(process.env)) {
        endpoint = await resolveEndSessionEndpoint().catch(() => null);
      }
    } catch {
      endpoint = null;
    }
  }

  const destination = endpoint ? new URL(endpoint) : new URL("/login", baseUrl);
  if (endpoint) {
    destination.searchParams.set("post_logout_redirect_uri", `${baseUrl}/login`);
  }

  const accept = request.headers.get("accept") || "";
  let response: NextResponse;
  if (accept.includes("application/json")) {
    response = NextResponse.json(
      { ok: true, summary: "Logged out successfully." },
      { status: 200, headers: { "cache-control": "no-store" } },
    );
  } else {
    response = NextResponse.redirect(destination);
    response.headers.set("cache-control", "no-store");
  }

  response.cookies.set(webSessionCookieName, "", {
    ...webSessionCookieOptions,
    maxAge: 0,
  });
  return response;
}

export const GET = logout;
export const POST = logout;
