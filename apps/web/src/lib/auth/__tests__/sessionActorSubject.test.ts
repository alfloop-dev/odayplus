import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST as loginPOST } from "../../../app/login/route";
import { GET as sessionGET } from "../../../app/auth/session/route";
import { base64UrlDecode, openJson } from "../crypto";
import { MockIdentityStore, setIdentityStoreForTests } from "../identityStore";
import { DEFAULT_LOGIN_THROTTLE_CONFIG, LoginThrottle, MockLoginThrottleStore, setLoginThrottleForTests } from "../loginThrottle";
import { localJwtExpiresAt, mintLocalJwt } from "../localJwt";
import { readWebSession, sealLegacyWebSession, sealWebSessionReference, webSessionCookieName } from "../session";
import { MockSessionStore, setSessionStoreForTests } from "../sessionStore";

const SECRET = "test-actor-session-secret-at-least-32-bytes";
const SIGNING_KEY = "test-actor-local-signing-key-at-least-32-bytes";
const ORIGIN = "https://ops.oday.plus";
const ACCOUNT_ID = "00000000-0000-0000-0000-000000000102";
const TENANT_ID = "00000000-0000-0000-0000-000000000001";
const USERNAME = "operator-display-name";
const RAW_OIDC_SUB = "external-provider-subject";
let store: MockSessionStore;

beforeEach(() => {
  store = new MockSessionStore();
  setSessionStoreForTests(store);
  setIdentityStoreForTests(new MockIdentityStore([{
    accountId: ACCOUNT_ID, tenantId: TENANT_ID, username: USERNAME,
    email: "operator@example.invalid", status: "active", password: "LocalPassword123!",
  }]));
  setLoginThrottleForTests(new LoginThrottle(new MockLoginThrottleStore(), DEFAULT_LOGIN_THROTTLE_CONFIG, "test-actor-login-throttle-pepper"));
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("ODP_PRODUCT_MODE", "production");
  vi.stubEnv("ODP_AUTH_MODE", "local");
  vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
  vi.stubEnv("ODP_WEB_BASE_URL", ORIGIN);
  vi.stubEnv("ODP_IDENTITY_TOKEN_SIGNING_KEY", SIGNING_KEY);
  vi.stubEnv("ODP_AUTH_LOCAL_ISSUER", "urn:odp:identity:local");
  vi.stubEnv("ODP_AUTH_AUDIENCES", "https://api.internal.example");
});

afterEach(() => {
  setSessionStoreForTests(undefined);
  setIdentityStoreForTests(undefined);
  setLoginThrottleForTests(undefined);
  vi.unstubAllEnvs();
});

function probe(cookie?: string): NextRequest {
  const request = new NextRequest(`${ORIGIN}/auth/session`);
  if (cookie) request.cookies.set(webSessionCookieName, cookie);
  return request;
}

async function localLogin() {
  const request = new NextRequest(`${ORIGIN}/login`, {
    method: "POST",
    headers: { origin: ORIGIN, "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ username: USERNAME, password: "LocalPassword123!" }),
  });
  request.headers.set("origin", ORIGIN);
  const response = await loginPOST(request);
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ ok: true, subject: USERNAME, returnTo: "/operator" });
  const cookie = response.cookies.get(webSessionCookieName);
  expect(cookie).toMatchObject({ httpOnly: true, secure: true, sameSite: "lax", path: "/" });
  expect(cookie?.value).toBeTruthy();
  const resolved = await readWebSession(cookie?.value);
  expect(resolved).toMatchObject({ accountId: ACCOUNT_ID, subject: USERNAME, provider: "local_password" });
  return { cookie: cookie!.value, session: resolved! };
}

async function oidcCookie() {
  const now = Math.floor(Date.now() / 1000);
  await store.createSession({
    sessionId: "linked-oidc-session", accountId: ACCOUNT_ID, provider: "oidc",
    subject: RAW_OIDC_SUB, accessToken: "test-opaque-upstream-token", tenantId: TENANT_ID,
    idleTimeoutMs: 30 * 60 * 1000, absoluteLifetimeMs: 8 * 60 * 60 * 1000,
  });
  return sealWebSessionReference({
    kind: "web-session", sid: "linked-oidc-session", provider: "oidc", issuedAt: now, expiresAt: now + 600,
  }, SECRET);
}

describe("actual session actor subject", () => {
  it("returns local account ID after real login while login response and stored display subject stay unchanged", async () => {
    const { cookie, session } = await localLogin();
    const now = Math.floor(Date.now() / 1000);
    const response = await sessionGET(probe(cookie));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const body = await response.json();
    expect(body).toEqual({ subject: ACCOUNT_ID, expiresAt: session.expiresAt });
    expect(Number.isSafeInteger(body.expiresAt)).toBe(true);
    expect(body.expiresAt).toBeGreaterThan(now);
    expect(store.sessions.get(session.sid!)?.subject).toBe(USERNAME);
    const sealed = await openJson<Record<string, unknown>>(cookie, "web-session", SECRET);
    expect(sealed).not.toHaveProperty("subject");
    expect(sealed).not.toHaveProperty("accountId");
    expect(sealed).not.toHaveProperty("accessToken");
  });

  it("keeps the account ID across actual local bearer refresh without changing stored display subject", async () => {
    const { cookie, session } = await localLogin();
    const now = Math.floor(Date.now() / 1000);
    const expired = await mintLocalJwt({ subject: ACCOUNT_ID, sid: session.sid!, tenantId: TENANT_ID, nowSeconds: now - 600, expiresInSeconds: 120 });
    await store.updateAccessToken(session.sid!, expired);
    const response = await sessionGET(probe(cookie));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ subject: ACCOUNT_ID, expiresAt: session.expiresAt });
    const record = await store.validateSession(session.sid!);
    expect(record?.accessToken).not.toBe(expired);
    expect(localJwtExpiresAt(record!.accessToken)).toBeGreaterThan(now);
    expect(record?.subject).toBe(USERNAME);
    // Assert an actual server-minted refresh token; production subject selection never parses JWTs.
    const claims = JSON.parse(new TextDecoder().decode(base64UrlDecode(record!.accessToken.split(".")[1]!)));
    expect(claims.sub).toBe(ACCOUNT_ID);
  });

  it("returns linked server account for OIDC rather than raw external subject", async () => {
    const response = await sessionGET(probe(await oidcCookie()));
    expect(response.status).toBe(200);
    expect((await response.json()).subject).toBe(ACCOUNT_ID);
    expect(store.sessions.get("linked-oidc-session")?.subject).toBe(RAW_OIDC_SUB);
  });

  it("preserves production linked legacy OIDC upgrade and reseals opaque reference with account actor", async () => {
    const now = Math.floor(Date.now() / 1000);
    store.oidcAccounts.set(RAW_OIDC_SUB, { accountId: ACCOUNT_ID, tenantId: TENANT_ID, subject: USERNAME });
    const legacy = await sealLegacyWebSession({ kind: "web-session", subject: RAW_OIDC_SUB, accessToken: "test-legacy-upstream-token", provider: "oidc", issuedAt: now, expiresAt: now + 600 }, SECRET);
    const response = await sessionGET(probe(legacy));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ subject: ACCOUNT_ID, expiresAt: now + 600 });
    expect(response.headers.get("cache-control")).toBe("no-store");
    const upgraded = response.cookies.get(webSessionCookieName);
    expect(upgraded).toMatchObject({ httpOnly: true, secure: true, sameSite: "lax", path: "/" });
    const sealed = await openJson<Record<string, unknown>>(upgraded?.value, "web-session", SECRET);
    expect(sealed?.sid).toBeTruthy();
    expect(sealed).not.toHaveProperty("subject");
    expect(sealed).not.toHaveProperty("accessToken");
    const second = await sessionGET(probe(upgraded?.value));
    expect((await second.json()).subject).toBe(ACCOUNT_ID);
  });

  it.each([undefined, null, "", "   ", 42])("refuses server session missing or malformed account ID %j without display fallback", async (accountId) => {
    const cookie = await oidcCookie();
    Object.assign(store.sessions.get("linked-oidc-session")!, { accountId });
    const response = await sessionGET(probe(cookie));
    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toMatchObject({ error: { code: "WEB_SESSION_REQUIRED" } });
  });

  it("refuses real local login cookie after durable revocation", async () => {
    const { cookie, session } = await localLogin();
    await store.revokeSession(session.sid!, "test logout");
    const response = await sessionGET(probe(cookie));
    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toMatchObject({ error: { code: "WEB_SESSION_REQUIRED" } });
  });

  it("refuses unlinked production legacy external subject", async () => {
    const now = Math.floor(Date.now() / 1000);
    const legacy = await sealLegacyWebSession({ kind: "web-session", subject: RAW_OIDC_SUB, accessToken: "test-legacy-upstream-token", provider: "oidc", issuedAt: now, expiresAt: now + 600 }, SECRET);
    const response = await sessionGET(probe(legacy));
    expect(response.status).toBe(401);
    expect(store.sessions.size).toBe(0);
  });
});
