import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { exchangeAuthorizationCode } from "../../../lib/auth/oidc";
import {
  oidcTransactionCookieName,
  oidcTransactionCookieOptions,
  readOidcTransaction,
  sealWebSession,
  webSessionCookieName,
  webSessionCookieOptions,
} from "../../../lib/auth/session";
import { resolveWebBaseUrl } from "../../../lib/auth/runtime";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function clearTransaction(response: NextResponse): void {
  response.cookies.set(oidcTransactionCookieName, "", {
    ...oidcTransactionCookieOptions,
    maxAge: 0,
  });
}

function callbackFailure(code: string): NextResponse {
  const response = NextResponse.json(
    {
      error: {
        code,
        summary: "OIDC authentication could not be completed.",
      },
    },
    { status: 401, headers: { "cache-control": "no-store" } },
  );
  clearTransaction(response);
  return response;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  if (request.nextUrl.searchParams.has("error")) {
    return callbackFailure("OIDC_PROVIDER_ERROR");
  }

  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  if (!code || !state) return callbackFailure("OIDC_CALLBACK_INVALID");

  try {
    const transaction = await readOidcTransaction(
      request.cookies.get(oidcTransactionCookieName)?.value,
    );
    if (!transaction) return callbackFailure("OIDC_TRANSACTION_INVALID");

    const session = await exchangeAuthorizationCode({
      code,
      returnedState: state,
      transaction,
    });
    const returnUrl = new URL(
      transaction.returnTo,
      resolveWebBaseUrl(request.nextUrl.origin),
    );
    const response = NextResponse.redirect(returnUrl);
    response.cookies.set(
      webSessionCookieName,
      await sealWebSession(session),
      {
        ...webSessionCookieOptions,
        maxAge: Math.max(
          1,
          session.expiresAt - Math.floor(Date.now() / 1000),
        ),
      },
    );
    clearTransaction(response);
    response.headers.set("cache-control", "no-store");
    return response;
  } catch {
    return callbackFailure("OIDC_CALLBACK_REJECTED");
  }
}

