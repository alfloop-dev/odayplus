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
  sid?: string;
  provider?: "local_password" | "oidc";
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
  value: Partial<WebSession> | null,
  nowSeconds?: number,
): value is WebSession {
  return Boolean(
    value &&
      value.kind === "web-session" &&
      value.tokenType === "Bearer" &&
      value.accessToken &&
      value.subject &&
      validExpiry(value as ExpiringPayload, nowSeconds),
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
  const value = await openJson<Partial<WebSession>>(
    cookieValue,
    SESSION_PURPOSE,
    options.secret,
  );
  if (!isWebSession(value, options.nowSeconds)) {
    return null;
  }

  // Contract §5.5: Legacy payload compatibility and in-place upgrade.
  // Legacy payloads do not have `sid`. We assign a synthetic `sid` and
  // `provider: "oidc"` while strictly retaining the original `expiresAt`.
  if (!value.sid) {
    // Generate a stable synthetic sid from session content so repeated reads
    // return the same sid without requiring a cookie rewrite.
    const sidSource = `legacy-sid:${value.subject}:${value.issuedAt}`;
    const sidHash = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(sidSource),
    );
    const sidBytes = new Uint8Array(sidHash).slice(0, 16);
    // Format as UUID-like hex string
    const hex = Array.from(sidBytes, (b) => b.toString(16).padStart(2, "0")).join("");
    const stableSid = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
    return {
      ...value,
      sid: stableSid,
      provider: value.provider || "oidc",
    } as WebSession;
  }

  return value as WebSession;
}

export async function rotateWebSession(
  currentSession: WebSession,
  options: {
    newAccessToken?: string;
    nowSeconds?: number;
    ttlSeconds?: number;
  } = {},
): Promise<WebSession> {
  const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1000);
  const ttl = options.ttlSeconds ?? (currentSession.expiresAt - nowSeconds);
  const cappedTtl = Math.min(
    Math.max(ttl, 1),
    SESSION_COOKIE_MAX_AGE_SECONDS,
  );

  return {
    kind: "web-session",
    accessToken: options.newAccessToken ?? currentSession.accessToken,
    tokenType: "Bearer",
    subject: currentSession.subject,
    sid: crypto.randomUUID(),
    provider: currentSession.provider ?? "local_password",
    issuedAt: nowSeconds,
    expiresAt: Math.min(
      nowSeconds + cappedTtl,
      nowSeconds + SESSION_COOKIE_MAX_AGE_SECONDS,
    ),
  };
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
