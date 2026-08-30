import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { exchangeAuthorizationCode } from "../../../lib/auth/oidc";
import {
  oidcTransactionCookieName,
  oidcTransactionCookieOptions,
  readOidcTransaction,
  sealWebSessionReference,
  webSessionCookieName,
  webSessionCookieOptions,
} from "../../../lib/auth/session";
import { isOidcEnabled, resolveWebBaseUrl } from "../../../lib/auth/runtime";
import { getDefaultIdentityStore } from "../../../lib/auth/identityStore";
import {
  DEFAULT_SESSION_IDLE_TIMEOUT_MS,
  getDefaultSessionStore,
} from "../../../lib/auth/sessionStore";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function clearTransaction(response: NextResponse): void {
  response.cookies.set(oidcTransactionCookieName, "", {
    ...oidcTransactionCookieOptions,
    maxAge: 0,
  });
}

function callbackFailure(code: string, status = 401): NextResponse {
  const response = NextResponse.json(
    {
      error: {
        code,
        summary: "OIDC authentication could not be completed.",
      },
    },
    { status, headers: { "cache-control": "no-store" } },
  );
  clearTransaction(response);
  return response;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  // 1. Fail closed if OIDC is disabled / not configured (Contract §3.2, T14)
  let oidcActive = false;
  try {
    oidcActive = isOidcEnabled(process.env);
  } catch {
    oidcActive = false;
  }

  if (!oidcActive) {
    return NextResponse.json(
      {
        error: {
          code: "WEB_AUTH_PROVIDER_DISABLED",
          summary: "OIDC authentication provider is disabled.",
        },
      },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }

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
    const identityStore = getDefaultIdentityStore();
    const sessionStore = getDefaultSessionStore(process.env);
    if (
      !identityStore ||
      !sessionStore ||
      !identityStore.findAccountByFederatedIdentity ||
      !session.subject ||
      !session.accessToken
    ) {
      return callbackFailure("OIDC_CALLBACK_REJECTED");
    }
    const account = await identityStore.findAccountByFederatedIdentity(
      process.env.ODP_WEB_OIDC_ISSUER?.trim() || "",
      session.subject,
    );
    if (!account || account.status !== "active") {
      // OIDC never provisions an account. A missing federation link is
      // intentionally indistinguishable from other callback failures here.
      return callbackFailure("OIDC_CALLBACK_REJECTED");
    }
    const nowSeconds = Math.floor(Date.now() / 1000);
    const sid = crypto.randomUUID();
    await sessionStore.createSession({
      sessionId: sid,
      accountId: account.accountId,
      provider: "oidc",
      accessToken: session.accessToken,
      subject: account.username,
      tenantId: account.tenantId,
      idleTimeoutMs: DEFAULT_SESSION_IDLE_TIMEOUT_MS,
      absoluteLifetimeMs: Math.max(1, (session.expiresAt - nowSeconds) * 1000),
    });
    const serverSession = {
      ...session,
      sid,
      accountId: account.accountId,
      tenantId: account.tenantId,
      subject: account.username,
      provider: "oidc" as const,
    };
    const returnUrl = new URL(
      transaction.returnTo,
      resolveWebBaseUrl(request.nextUrl.origin),
    );
    const response = NextResponse.redirect(returnUrl);
    response.cookies.set(
      webSessionCookieName,
      await sealWebSessionReference(serverSession),
      {
        ...webSessionCookieOptions,
        maxAge: Math.max(
          1,
          serverSession.expiresAt - nowSeconds,
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
