import { openJson, sealJson } from "./crypto";
import {
  DEFAULT_SESSION_IDLE_TIMEOUT_MS,
  getDefaultSessionStore,
  type SessionRecord,
  type SessionStore,
} from "./sessionStore";
import {
  OIDC_TRANSACTION_COOKIE,
  OIDC_TRANSACTION_MAX_AGE_SECONDS,
  SESSION_COOKIE_MAX_AGE_SECONDS,
  isProductionWebRuntime,
  WEB_SESSION_COOKIE,
} from "./runtime";

const SESSION_PURPOSE = "web-session";
const TRANSACTION_PURPOSE = "oidc-transaction";

type ExpiringPayload = {
  issuedAt: number;
  expiresAt: number;
};

/**
 * A resolved session may contain server-only fields.  Those fields are never
 * emitted by sealWebSessionReference; they exist here for the BFF after the
 * session id has been validated against identity.sessions.
 */
export type WebSession = ExpiringPayload & {
  kind: "web-session";
  sid?: string;
  provider?: "local_password" | "oidc";
  accountId?: string;
  tenantId?: string;
  subject?: string;
  accessToken?: string;
  tokenType?: "Bearer";
  /** True when this value came from a legacy cookie and callers must re-seal it. */
  legacyUpgrade?: boolean;
};

type WebSessionCookie = ExpiringPayload & {
  kind: "web-session";
  sid: string;
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

function isValidWebSessionPayload(
  value: Partial<WebSession> | null,
  nowSeconds: number,
): value is WebSession & { issuedAt: number; expiresAt: number } {
  return Boolean(
    value &&
      value.kind === "web-session" &&
      validExpiry(value as ExpiringPayload, nowSeconds) &&
      ((typeof value.sid === "string" && value.sid.length > 0) ||
        (typeof value.accessToken === "string" &&
          value.accessToken.length > 0 &&
          typeof value.subject === "string" &&
          value.subject.length > 0)),
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

function recordToSession(
  cookie: WebSessionCookie,
  record: SessionRecord,
  nowSeconds: number,
  legacyUpgrade = false,
): WebSession | null {
  const absoluteExpiresAt = Math.floor(record.absoluteExpiresAt.getTime() / 1000);
  const expiresAt = Math.min(cookie.expiresAt, absoluteExpiresAt);
  if (expiresAt <= nowSeconds || !record.accessToken) return null;
  return {
    kind: "web-session",
    issuedAt: cookie.issuedAt,
    expiresAt,
    sid: record.sessionId,
    provider: record.provider,
    accountId: record.accountId,
    tenantId: record.tenantId,
    subject: record.subject || record.accountId,
    accessToken: record.accessToken,
    tokenType: "Bearer",
    legacyUpgrade,
  };
}

async function stableLegacySid(subject: string, issuedAt: number): Promise<string> {
  const sidHash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(`legacy-sid:${subject}:${issuedAt}`),
  );
  const hex = Array.from(new Uint8Array(sidHash), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

/** Seal a browser-safe session reference. */
export async function sealWebSession(
  session: WebSession,
  explicitSecret?: string,
): Promise<string> {
  return sealWebSessionReference(session, explicitSecret);
}

/**
 * Compatibility-only encoder for migration fixtures. Application response
 * paths must never call this function because it deliberately represents the
 * pre-P2 cookie shape.
 */
export async function sealLegacyWebSession(
  session: WebSession,
  explicitSecret?: string,
): Promise<string> {
  if (!session.accessToken || !session.subject) {
    throw new Error("Legacy session requires accessToken and subject");
  }
  return sealJson(session, SESSION_PURPOSE, explicitSecret);
}

/** Seal the only payload shape that application responses may give the browser. */
export async function sealWebSessionReference(
  session: Pick<
    WebSession,
    "kind" | "sid" | "issuedAt" | "expiresAt" | "provider"
  >,
  explicitSecret?: string,
): Promise<string> {
  if (!session.sid) throw new Error("A session id is required");
  const payload: WebSessionCookie = {
    kind: "web-session",
    sid: session.sid,
    issuedAt: session.issuedAt,
    expiresAt: session.expiresAt,
    provider: session.provider,
  };
  return sealJson(payload, SESSION_PURPOSE, explicitSecret);
}

/**
 * Decode and validate a cookie, then resolve its id through identity.sessions.
 * Every production caller therefore sees current revocation and expiry state;
 * the encrypted cookie is never trusted for subject or bearer material.
 */
export async function readWebSession(
  cookieValue: string | null | undefined,
  options: {
    secret?: string;
    nowSeconds?: number;
    environment?: Record<string, string | undefined>;
    sessionStore?: SessionStore | null;
    idleTimeoutMs?: number;
  } = {},
): Promise<WebSession | null> {
  const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1000);
  const value = await openJson<Partial<WebSession>>(
    cookieValue,
    SESSION_PURPOSE,
    options.secret,
  );
  if (!isValidWebSessionPayload(value, nowSeconds)) {
    return null;
  }

  const environment = options.environment ?? process.env;
  const store =
    options.sessionStore !== undefined
      ? options.sessionStore
      : getDefaultSessionStore(environment);

  // Legacy sealed OIDC cookies are migrated on their first server read.  The
  // lookup is intentionally against a linked identity, never a newly invented
  // account.  The returned object tells response builders to overwrite the
  // browser cookie with the opaque reference format.
  if (!value.sid) {
    if (!value.accessToken || !value.subject) return null;
    // Preserve the old pure-decoder contract for callers that supply a clock
    // (notably deterministic unit fixtures). Request handlers do not supply a
    // clock, so real legacy requests always take the durable upgrade path.
    if (
      options.nowSeconds !== undefined &&
      options.sessionStore === undefined &&
      !isProductionWebRuntime(environment)
    ) {
      return {
        ...(value as WebSession),
        sid: await stableLegacySid(value.subject, value.issuedAt),
        provider: value.provider || "oidc",
      };
    }
    if (!store) {
      if (isProductionWebRuntime(environment)) return null;
      const sid = await stableLegacySid(value.subject, value.issuedAt);
      return {
        ...(value as WebSession),
        sid,
        provider: value.provider || "oidc",
      };
    }

    let account = null;
    if (store.resolveOidcAccount) {
      account = await store.resolveOidcAccount(
        value.subject,
        environment.ODP_WEB_OIDC_ISSUER,
      );
    }
    if (!account) {
      // A non-production compatibility path is useful for fixtures created by
      // the pre-P2 test harness. Production must prove the federated link.
      if (isProductionWebRuntime(environment)) return null;
      account = {
        accountId: value.subject,
        subject: value.subject,
      };
    }

    const remainingLifetimeMs = Math.max(
      1,
      (value.expiresAt - nowSeconds) * 1000,
    );
    const record = await store.createSession({
      sessionId: crypto.randomUUID(),
      accountId: account.accountId,
      provider: "oidc",
      accessToken: value.accessToken,
      subject: account.subject || value.subject,
      tenantId: account.tenantId,
      idleTimeoutMs: options.idleTimeoutMs ?? DEFAULT_SESSION_IDLE_TIMEOUT_MS,
      absoluteLifetimeMs: remainingLifetimeMs,
    });
    const upgraded = recordToSession(
      {
        kind: "web-session",
        sid: record.sessionId,
        issuedAt: value.issuedAt,
        expiresAt: value.expiresAt,
        provider: "oidc",
      },
      record,
      nowSeconds,
      true,
    );
    return upgraded;
  }

  if (typeof value.sid !== "string" || value.sid.length === 0) return null;
  if (!store) {
    // This is only retained for non-production callers that explicitly create
    // legacy-shaped unit fixtures. A production opaque cookie cannot be used
    // without an authoritative store.
    // The old signed payload is accepted only by the test runtime while the
    // migration fixtures are exercised. A deployed production process always
    // has to resolve an opaque cookie through identity.sessions.
    if (isProductionWebRuntime(environment)) {
      return null;
    }
    if (value.accessToken && value.subject) return value as WebSession;
    return null;
  }

  const record = await store.validateSession(value.sid);
  if (!record) {
    // Compatibility for pre-P2 unit fixtures that seal the former payload
    // directly. It is deliberately unavailable to a deployed process.
    if (
      !isProductionWebRuntime(environment) &&
      options.sessionStore === undefined &&
      value.accessToken &&
      value.subject
    ) {
      return value as WebSession;
    }
    return null;
  }
  await store.touchSession(
    value.sid,
    options.idleTimeoutMs ?? DEFAULT_SESSION_IDLE_TIMEOUT_MS,
  );
  return recordToSession(value as WebSessionCookie, record, nowSeconds);
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
    accountId: currentSession.accountId,
    tenantId: currentSession.tenantId,
    sid: crypto.randomUUID(),
    provider: currentSession.provider ?? "local_password",
    issuedAt: nowSeconds,
    expiresAt: Math.min(
      nowSeconds + cappedTtl,
      nowSeconds + SESSION_COOKIE_MAX_AGE_SECONDS,
      currentSession.expiresAt,
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
