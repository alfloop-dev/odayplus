import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { resolveEndSessionEndpoint } from "../../../lib/auth/oidc";
import {
  webSessionCookieName,
  webSessionCookieOptions,
} from "../../../lib/auth/session";
import { resolveWebBaseUrl } from "../../../lib/auth/runtime";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function logout(request: NextRequest): Promise<NextResponse> {
  const baseUrl = resolveWebBaseUrl(request.nextUrl.origin);
  const endpoint = await resolveEndSessionEndpoint().catch(() => null);
  const destination = endpoint ? new URL(endpoint) : new URL("/login", baseUrl);
  if (endpoint) {
    destination.searchParams.set("post_logout_redirect_uri", `${baseUrl}/login`);
  }

  const response = NextResponse.redirect(destination);
  response.cookies.set(webSessionCookieName, "", {
    ...webSessionCookieOptions,
    maxAge: 0,
  });
  response.headers.set("cache-control", "no-store");
  return response;
}

export const GET = logout;
export const POST = logout;
