import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST as logout } from "../../../app/auth/logout/route";
import { readWebSession, sealLegacyWebSession, webSessionCookieName } from "../session";
import { PostgresSessionStore, setSessionStoreForTests } from "../sessionStore";

const ACCOUNT = "13faae19-21c6-4663-8e89-b93ea7f1107d";
const SECRET = "OFFLINE-legacy-migration-secret-with-at-least32bytes";
const stores: PostgresSessionStore[] = [];
let pool: any;
afterEach(async () => {
  setSessionStoreForTests(undefined);
  for (const store of stores.splice(0)) await (store as any)._pool?.end();
  if (pool) await pool.end();
  pool = undefined;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});
function store() { const value = new PostgresSessionStore(); stores.push(value); return value; }
async function setup(ttlSeconds = 3600) {
  const url = process.env.ODP_TEST_SESSION_BOUNDARY_DATABASE_URL!;
  vi.stubEnv("ODP_IDENTITY_DATABASE_URL", url); vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("ODP_PRODUCT_MODE", "production"); vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
  vi.stubEnv("ODP_WEB_OIDC_ISSUER", "https://offline-issuer.invalid");
  vi.stubEnv("ODP_WEB_BASE_URL", "https://offline.example");
  const { Pool } = await import("pg"); pool = new Pool({ connectionString: url, max: 2 });
  await pool.query(`INSERT INTO identity.federated_identities(issuer,subject,account_id,linked_by)
    VALUES ('https://offline-issuer.invalid','offline-legacy',$1,'offline-fixture') ON CONFLICT DO NOTHING`, [ACCOUNT]);
  const now = Math.floor(Date.now() / 1000);
  return sealLegacyWebSession({ kind: "web-session", subject: "offline-legacy", provider: "oidc",
    accessToken: "OFFLINE-ONLY-NOT-A-REAL-BEARER", issuedAt: now, expiresAt: now + ttlSeconds });
}
function logoutRequest(cookie: string) {
  const request = new NextRequest("https://offline.example/auth/logout", { method: "POST" });
  request.headers.set("origin", "https://offline.example"); request.headers.set("accept", "application/json");
  request.cookies.set(webSessionCookieName, cookie); return request;
}

describe.skipIf(!process.env.ODP_TEST_SESSION_BOUNDARY_DATABASE_URL)("actual isolated PG legacy migration/revocation", () => {
  it("concurrent pools/restart reuse one SID; logout and session deletion cannot resurrect original legacy cookie", async () => {
    const cookie = await setup(); const a = store(); const b = store();
    const [one, two] = await Promise.all([readWebSession(cookie, { sessionStore: a }), readWebSession(cookie, { sessionStore: b })]);
    expect(one?.sid).toBeTruthy(); expect(two?.sid).toBe(one?.sid);
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.legacy_oidc_session_migrations")).rows[0].n).toBe(1);
    setSessionStoreForTests(a);
    expect((await logout(logoutRequest(cookie))).status).toBe(200);
    expect(await readWebSession(cookie, { sessionStore: b })).toBeNull();
    await pool.query("DELETE FROM identity.sessions WHERE session_id = $1", [one!.sid]);
    expect(await readWebSession(cookie, { sessionStore: store() })).toBeNull();
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.legacy_oidc_session_migrations")).rows[0].n).toBe(1);
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.sessions WHERE session_id = $1", [one!.sid])).rows[0].n).toBe(0);
  });

  it("equivalent cookie encoding after logout/deletion cannot create a second migration or SID", async () => {
    // Fixture-only IV guarantees a noncanonical equivalent alphabet variant.
    vi.spyOn(crypto, "getRandomValues").mockImplementationOnce(<T extends ArrayBufferView | null>(value: T): T => {
      if (value) new Uint8Array(value.buffer, value.byteOffset, value.byteLength).fill(255); return value;
    });
    const cookie = await setup(); const a = store(); const first = await readWebSession(cookie, { sessionStore: a });
    const alias = cookie.replace(/-/g, "+").replace(/_/g, "/"); expect(alias === cookie).toBe(false);
    setSessionStoreForTests(a); expect((await logout(logoutRequest(cookie))).status).toBe(200);
    await pool.query("DELETE FROM identity.sessions WHERE session_id=$1", [first!.sid]);
    expect(await readWebSession(alias, { sessionStore: store() })).toBeNull();
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.legacy_oidc_session_migrations")).rows[0].n).toBe(1);
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.sessions WHERE account_id=$1 AND provider='oidc'", [ACCOUNT])).rows[0].n).toBe(0);
  });

  it("bounded absolute lifetime and natural idle expiry cannot renew legacy authority", async () => {
    const cookie = await setup(172800); const a = store();
    const value = await readWebSession(cookie, { sessionStore: a });
    const record = await a.validateSession(value!.sid!);
    expect(record!.absoluteExpiresAt.getTime() - record!.createdAt.getTime()).toBeLessThanOrEqual(28800001);
    await pool.query("UPDATE identity.sessions SET idle_expires_at = clock_timestamp() - interval '1 second' WHERE session_id=$1", [value!.sid]);
    expect(await readWebSession(cookie, { sessionStore: store() })).toBeNull();
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.legacy_oidc_session_migrations")).rows[0].n).toBe(1);
  });

  it("real deferred commit failure rolls back both migration claim and session, without a false cookie/read success", async () => {
    const cookie = await setup();
    await pool.query(`CREATE FUNCTION identity.offline_refuse_legacy_commit() RETURNS trigger AS $$
      BEGIN IF NEW.provider = 'oidc' AND NEW.account_id = '${ACCOUNT}' THEN
        RAISE EXCEPTION 'PRIVATE-COMMIT-MARKER'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql;
      CREATE CONSTRAINT TRIGGER offline_legacy_commit_failure AFTER INSERT ON identity.sessions
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION identity.offline_refuse_legacy_commit()`);
    setSessionStoreForTests(store());
    const response = await logout(logoutRequest(cookie));
    expect(response.status).toBe(503); expect(response.headers.get("set-cookie")).toBeNull();
    expect(await response.text()).not.toContain("PRIVATE-COMMIT-MARKER");
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.legacy_oidc_session_migrations")).rows[0].n).toBe(0);
    await pool.query("DROP TRIGGER offline_legacy_commit_failure ON identity.sessions");
  });

  it("lost postcommit validation reply does not create another SID on later read", async () => {
    const cookie = await setup(); const a = store();
    const read = vi.spyOn(a, "validateSession").mockRejectedValueOnce(new Error("PRIVATE-LOST-REPLY"));
    await expect(readWebSession(cookie, { sessionStore: a })).rejects.toThrow("PRIVATE-LOST-REPLY");
    read.mockRestore();
    const value = await readWebSession(cookie, { sessionStore: store() });
    expect(value?.sid).toBeTruthy();
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.legacy_oidc_session_migrations")).rows[0].n).toBe(1);
    expect((await pool.query("SELECT count(*)::int AS n FROM identity.sessions WHERE provider='oidc' AND account_id=$1", [ACCOUNT])).rows[0].n).toBe(1);
  });
});
