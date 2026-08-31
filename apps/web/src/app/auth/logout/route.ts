import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { resolveEndSessionEndpoint } from "../../../lib/auth/oidc";
import {
  readWebSession,
  webSessionCookieName,
  webSessionCookieOptions,
} from "../../../lib/auth/session";
import {
  isOidcEnabled,
  isProductionWebRuntime,
  resolveWebBaseUrl,
  verifyCsrfOrigin,
} from "../../../lib/auth/runtime";
import { getDefaultSessionStore } from "../../../lib/auth/sessionStore";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function logout(request: NextRequest): Promise<NextResponse> {
  const hasSessionCookie = Boolean(
    request.cookies.get(webSessionCookieName)?.value,
  );
  // Logout mutates server-side session state. Requiring Origin/Referer even on
  // GET prevents an attacker from revoking a session with an image/navigation.
  // A request with no cookie is already a no-op and remains harmless/idempotent.
  if (hasSessionCookie && !verifyCsrfOrigin(request)) {
    return NextResponse.json(
      {
        error: {
          code: "CSRF_VERIFICATION_FAILED",
          summary: "CSRF verification failed.",
        },
      },
      { status: 403, headers: { "cache-control": "no-store" } },
    );
  }

  const baseUrl = resolveWebBaseUrl(request.nextUrl.origin);
  let session = null;
  try {
    session = await readWebSession(
      request.cookies.get(webSessionCookieName)?.value,
    );
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "WEB_AUTH_UNAVAILABLE",
          summary: "Web authentication is temporarily unavailable.",
        },
      },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }

  if (session && session.sid) {
    const store = getDefaultSessionStore(process.env);
    if (!store && isProductionWebRuntime(process.env)) {
      return NextResponse.json(
        {
          error: {
            code: "WEB_AUTH_UNAVAILABLE",
            summary: "Web authentication is temporarily unavailable.",
          },
        },
        { status: 503, headers: { "cache-control": "no-store" } },
      );
    }
    if (store) {
      try {
        await store.revokeSession(session.sid, "user_logout");
      } catch {
        // A production logout must not claim success if revocation was not
        // durably recorded. Non-production stores are in-memory and should not
        // reach this branch unless explicitly replaced by a failing test store.
        if (isProductionWebRuntime(process.env)) {
          return NextResponse.json(
            {
              error: {
                code: "WEB_AUTH_UNAVAILABLE",
                summary: "Web authentication is temporarily unavailable.",
              },
            },
            { status: 503, headers: { "cache-control": "no-store" } },
          );
        }
      }
    }
  }

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
