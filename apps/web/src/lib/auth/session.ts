import { openJson, sealJson } from "./crypto";
import {
  OIDC_TRANSACTION_COOKIE,
  OIDC_TRANSACTION_MAX_AGE_SECONDS,
  SESSION_COOKIE_MAX_AGE_SECONDS,
  WEB_SESSION_COOKIE,
} from "./runtime";

const SESSION_PURPOSE = "web-session";
const TRANSACTION_PURPOSE = "oidc-transaction";

type ExpiringPayload = {
  issuedAt: number;
  expiresAt: number;
};

export type WebSession = ExpiringPayload & {
  kind: "web-session";
  accessToken: string;
  tokenType: "Bearer";
  subject: string;
};

export type OidcTransaction = ExpiringPayload & {
  kind: "oidc-transaction";
  state: string;
  codeVerifier: string;
  nonce: string;
  redirectUri: string;
  returnTo: string;
};

function validExpiry(
  payload: ExpiringPayload,
  nowSeconds = Math.floor(Date.now() / 1000),
): boolean {
  return (
    Number.isSafeInteger(payload.issuedAt) &&
    Number.isSafeInteger(payload.expiresAt) &&
    payload.issuedAt <= nowSeconds + 60 &&
    payload.expiresAt > nowSeconds
  );
}

function isWebSession(
  value: WebSession | null,
  nowSeconds?: number,
): value is WebSession {
  return Boolean(
    value &&
      value.kind === "web-session" &&
      value.tokenType === "Bearer" &&
      value.accessToken &&
      value.subject &&
      validExpiry(value, nowSeconds),
  );
}

function isOidcTransaction(
  value: OidcTransaction | null,
  nowSeconds?: number,
): value is OidcTransaction {
  return Boolean(
    value &&
      value.kind === "oidc-transaction" &&
      value.state &&
      value.codeVerifier &&
      value.nonce &&
      value.redirectUri &&
      value.returnTo &&
      validExpiry(value, nowSeconds),
  );
}

export async function sealWebSession(
  session: WebSession,
  explicitSecret?: string,
): Promise<string> {
  return sealJson(session, SESSION_PURPOSE, explicitSecret);
}

export async function readWebSession(
  cookieValue: string | null | undefined,
  options: { secret?: string; nowSeconds?: number } = {},
): Promise<WebSession | null> {
  const value = await openJson<WebSession>(
    cookieValue,
    SESSION_PURPOSE,
    options.secret,
  );
  return isWebSession(value, options.nowSeconds) ? value : null;
}

export async function sealOidcTransaction(
  transaction: OidcTransaction,
  explicitSecret?: string,
): Promise<string> {
  return sealJson(transaction, TRANSACTION_PURPOSE, explicitSecret);
}

export async function readOidcTransaction(
  cookieValue: string | null | undefined,
  options: { secret?: string; nowSeconds?: number } = {},
): Promise<OidcTransaction | null> {
  const value = await openJson<OidcTransaction>(
    cookieValue,
    TRANSACTION_PURPOSE,
    options.secret,
  );
  return isOidcTransaction(value, options.nowSeconds) ? value : null;
}

export const webSessionCookieOptions = {
  httpOnly: true,
  secure: true,
  sameSite: "lax" as const,
  path: "/",
  maxAge: SESSION_COOKIE_MAX_AGE_SECONDS,
};

export const oidcTransactionCookieOptions = {
  httpOnly: true,
  secure: true,
  sameSite: "lax" as const,
  path: "/",
  maxAge: OIDC_TRANSACTION_MAX_AGE_SECONDS,
};

export const webSessionCookieName = WEB_SESSION_COOKIE;
export const oidcTransactionCookieName = OIDC_TRANSACTION_COOKIE;
