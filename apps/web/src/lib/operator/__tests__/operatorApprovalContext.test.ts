import { NextRequest } from "next/server";
import { Pool } from "pg";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "../../../app/auth/session/route";
import { MockIdentityStore, PostgresIdentityStore, setIdentityStoreForTests } from "../../auth/identityStore";
import { sealLegacyWebSession, sealWebSessionReference, webSessionCookieName } from "../../auth/session";
import { MockSessionStore, PostgresSessionStore, setSessionStoreForTests } from "../../auth/sessionStore";

// Separate batch018 cohort, outside original auth/Operator selectors. No skip:
// missing owned real PG is an infrastructure failure, never a green unit pass.
const url = process.env.SOURCE_OWNED_PG_DSN;
const secret = "offline-approval-context-sealing-key-at-least-32-bytes";
const accountId = "aa7b49ef-4f4d-4b5e-9672-350cd86fc01d";
const tenantId = "42eed25f-9b65-4830-8ee0-ad825ce0930a";
let pool: Pool;
let sessions: PostgresSessionStore;
let identities: PostgresIdentityStore;
let sid: string;
let cookie: string;

function request(value = cookie, view = "operator-approval-context") {
  const req = new NextRequest(`https://ops.example.invalid/auth/session?view=${view}&accountId=attacker&tenantId=foreign`);
  if (value) req.cookies.set(webSessionCookieName, value);
  req.headers.set("x-subject-id", "attacker");
  req.headers.set("x-tenant-id", "foreign");
  return req;
}

beforeAll(async () => {
  if (!url || !process.env.SOURCE_OWNED_PG_CONTAINER) throw new Error("Owned real PG fixture required");
  pool = new Pool({ connectionString: url, max: 2 });
  // Minimal isolated SQL interface fixture, NOT full migration/schema acceptance.
  // These are the actual production stores/methods, not canned query responses.
  await pool.query(`CREATE SCHEMA identity;
    CREATE TABLE identity.accounts(account_id uuid PRIMARY KEY, tenant_id uuid, username text, email text, status text);
    CREATE TABLE identity.sessions(session_id uuid PRIMARY KEY, account_id uuid, provider text, access_token text,
      session_subject text, tenant_id uuid, created_at timestamptz, last_seen_at timestamptz,
      idle_expires_at timestamptz, absolute_expires_at timestamptz, revoked_at timestamptz, revoked_reason text,
      rotated_from uuid, verified_credential_revision bigint);
    CREATE TABLE identity.federated_identities(account_id uuid, subject text, issuer text);
    CREATE TABLE identity.legacy_oidc_session_migrations(migration_key text PRIMARY KEY, session_id uuid,
      account_id uuid, expires_at timestamptz);`);
});
afterAll(async () => { if (pool) await pool.end(); });
beforeEach(async () => {
  vi.stubEnv("NODE_ENV", "test");
  vi.stubEnv("ODP_WEB_SESSION_SECRET", secret);
  await pool.query("TRUNCATE identity.sessions, identity.accounts, identity.federated_identities, identity.legacy_oidc_session_migrations");
  await pool.query("INSERT INTO identity.accounts VALUES($1,$2,'operator.offline','offline@example.invalid','active')", [accountId, tenantId]);
  identities = new PostgresIdentityStore(); sessions = new PostgresSessionStore();
  Object.assign(identities, { _pool: pool }); Object.assign(sessions, { _pool: pool });
  setIdentityStoreForTests(identities); setSessionStoreForTests(sessions);
  sid = crypto.randomUUID();
  await sessions.createSession({ sessionId: sid, accountId, tenantId, provider: "oidc", subject: "operator.offline",
    accessToken: "offline-server-only-bearer", idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000 });
  const now = Math.floor(Date.now() / 1000);
  cookie = await sealWebSessionReference({ kind: "web-session", sid, issuedAt: now, expiresAt: now + 3600, provider: "oidc" });
});
afterEach(() => {
  setIdentityStoreForTests(undefined); setSessionStoreForTests(undefined);
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});

describe("authenticated approval SELF projection", () => {
  it("returns only stable current account/tenant and bounded expiry, ignoring identity selectors/headers", async () => {
    const response = await GET(request());
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toEqual({ subject: accountId, accountId, tenantId, expiresAt: expect.any(Number) });
    expect(body.expiresAt).toBeLessThanOrEqual(Math.floor(Date.now() / 1000) + 1800);
    const text = JSON.stringify(body);
    for (const privateValue of [sid, cookie, "offline-server-only-bearer", "operator.offline", "offline@example.invalid", "permission", "roles"]) {
      expect(text).not.toContain(privateValue);
    }
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("vary")).toBe("Cookie");
  });

  it("keeps the default GET representation and unknown selectors unchanged", async () => {
    for (const view of ["", "unsupported"]) {
      const response = await GET(request(cookie, view));
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({ subject: "operator.offline", expiresAt: expect.any(Number) });
      expect(response.headers.get("cache-control")).toBe("no-store");
    }
  });

  it.each(["disabled", "locked", "invited", "missing", "foreign-tenant", "missing-tenant", "changed-subject"])(
    "withholds context for current account state %s", async (state) => {
      if (state === "missing") await pool.query("DELETE FROM identity.accounts");
      else if (state === "foreign-tenant") await pool.query("UPDATE identity.accounts SET tenant_id=$1", [crypto.randomUUID()]);
      else if (state === "missing-tenant") await pool.query("UPDATE identity.accounts SET tenant_id=NULL");
      else if (state === "changed-subject") await pool.query("UPDATE identity.accounts SET username='other.offline'");
      else await pool.query("UPDATE identity.accounts SET status=$1", [state]);
      const response = await GET(request());
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: { code: "OPERATOR_CONTEXT_UNAVAILABLE", summary: expect.any(String) } });
    },
  );

  it.each(["revoked", "idle-expired", "absolute-expired", "missing", "empty-bearer"])("refuses %s sessions", async (state) => {
    if (state === "revoked") await sessions.revokeSession(sid, "offline-test");
    else if (state === "idle-expired") await pool.query("UPDATE identity.sessions SET idle_expires_at=now()-interval '1 second'");
    else if (state === "absolute-expired") await pool.query("UPDATE identity.sessions SET absolute_expires_at=now()-interval '1 second'");
    else if (state === "empty-bearer") await pool.query("UPDATE identity.sessions SET access_token=''");
    else await pool.query("DELETE FROM identity.sessions");
    expect((await GET(request())).status).toBe(401);
  });

  it.each(["missing-tenant", "wrong-subject", "wrong-provider", "blank-subject"])("withholds malformed durable %s", async (state) => {
    if (state === "missing-tenant") await pool.query("UPDATE identity.sessions SET tenant_id=NULL");
    else if (state === "wrong-provider") await pool.query("UPDATE identity.sessions SET provider='unsupported'");
    else await pool.query("UPDATE identity.sessions SET session_subject=$1", [state === "blank-subject" ? " " : "another.offline"]);
    expect((await GET(request())).status).toBe(503);
  });

  it("does not treat a signed legacy payload or memory account as durable authority", async () => {
    setSessionStoreForTests(null);
    setIdentityStoreForTests(new MockIdentityStore());
    const now = Math.floor(Date.now() / 1000);
    const legacy = await sealLegacyWebSession({ kind: "web-session", subject: accountId, accountId, tenantId,
      accessToken: "offline-cookie-derived-bearer", issuedAt: now, expiresAt: now + 3600 });
    expect((await GET(request(legacy))).status).toBe(503);
    expect((await GET(request(legacy, ""))).status).toBe(200);
  });

  it("does not elevate an otherwise valid dev memory session", async () => {
    const memory = new MockSessionStore();
    const now = Math.floor(Date.now() / 1000);
    await memory.createSession({ sessionId: sid, accountId, tenantId, provider: "oidc", subject: "operator.offline",
      accessToken: "offline", idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000 });
    setSessionStoreForTests(memory);
    expect((await GET(request(await sealWebSessionReference({ kind: "web-session", sid, issuedAt: now, expiresAt: now + 3600 })))).status).toBe(503);
  });

  it("upgrades a genuinely linked durable legacy cookie while keeping default migration behavior", async () => {
    await pool.query("INSERT INTO identity.federated_identities VALUES($1,'federated.offline','https://issuer.invalid')", [accountId]);
    vi.stubEnv("ODP_WEB_OIDC_ISSUER", "https://issuer.invalid");
    const now = Math.floor(Date.now() / 1000);
    const legacy = await sealLegacyWebSession({ kind: "web-session", subject: "federated.offline", accessToken: "offline-legacy-bearer",
      issuedAt: now, expiresAt: now + 3600 });
    const response = await GET(request(legacy));
    expect(response.status).toBe(200);
    expect((await response.json()).accountId).toBe(accountId);
    expect(response.cookies.get(webSessionCookieName)?.value).toBeTruthy();
    expect(response.headers.get("set-cookie")).not.toContain("offline-legacy-bearer");
  });

  it("reports storage failures honestly without exposing their content or changing default 401", async () => {
    vi.spyOn(sessions, "validateSession").mockRejectedValue(new Error("offline-private-database-detail"));
    const response = await GET(request());
    expect(response.status).toBe(503);
    expect(JSON.stringify(await response.json())).not.toContain("offline-private-database-detail");
    expect((await GET(request(cookie, ""))).status).toBe(401);
  });

  it("rechecks persisted revocation and ownership after initial resolution", async () => {
    const validate = sessions.validateSession.bind(sessions);
    let calls = 0;
    vi.spyOn(sessions, "validateSession").mockImplementation(async (id) => {
      if (++calls === 2) await sessions.revokeSession(id, "offline-concurrent-revocation");
      return validate(id);
    });
    expect((await GET(request())).status).toBe(401);
  });

  it.each(["", "malformed-cookie"])("refuses absent/forged session %j", async (value) => {
    expect((await GET(request(value))).status).toBe(401);
  });
});
