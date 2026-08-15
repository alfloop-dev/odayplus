import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { createAuthorizationRequest } from "../../lib/auth/oidc";
import {
  oidcTransactionCookieName,
  oidcTransactionCookieOptions,
  sealOidcTransaction,
} from "../../lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<NextResponse> {
  try {
    const { url, transaction } = await createAuthorizationRequest({
      requestOrigin: request.nextUrl.origin,
      returnTo: request.nextUrl.searchParams.get("returnTo"),
    });
    const response = NextResponse.redirect(url);
    response.cookies.set(
      oidcTransactionCookieName,
      await sealOidcTransaction(transaction),
      oidcTransactionCookieOptions,
    );
    response.headers.set("cache-control", "no-store");
    return response;
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "WEB_AUTH_NOT_CONFIGURED",
          summary: "Web authentication is not configured.",
        },
      },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
}

