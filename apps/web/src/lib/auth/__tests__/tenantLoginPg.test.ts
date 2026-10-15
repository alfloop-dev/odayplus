import { NextRequest } from "next/server";
import { Pool } from "pg";
import { expect, it, vi } from "vitest";
import { POST as login } from "../../../app/login/route";
import { PostgresIdentityStore, setIdentityStoreForTests } from "../identityStore";
import { LoginThrottle, PostgresLoginThrottleStore, setLoginThrottleForTests } from "../loginThrottle";
import { PostgresSessionStore, setSessionStoreForTests } from "../sessionStore";

const url = process.env.ODP_TEST_SESSION_BOUNDARY_DATABASE_URL;
it.skipIf(!url)("tenant-login-selection resolves both tenant-local names and refuses ambiguity", async () => {
  const pool = new Pool({ connectionString: url, max: 2 });
  const identity = new PostgresIdentityStore(); const sessions = new PostgresSessionStore(); const attempts = new PostgresLoginThrottleStore();
  Object.assign(identity, { _pool: pool }); Object.assign(sessions, { _pool: pool }); Object.assign(attempts, { _pool: pool });
  const secret = "offline-tenant-login-key-and-pepper-at-least32bytes";
  vi.stubEnv("NODE_ENV", "test"); vi.stubEnv("ODP_WEB_SESSION_SECRET", secret);
  setIdentityStoreForTests(identity); setSessionStoreForTests(sessions); setLoginThrottleForTests(new LoginThrottle(attempts, undefined, secret));
  const ids = [crypto.randomUUID(), crypto.randomUUID()]; const tenants = [crypto.randomUUID(), crypto.randomUUID()];
  const passwords = ["Offline-Tenant-A-Password-742!", "Offline-Tenant-B-Password-641!"];
  const results: number[] = [];
  try {
    for (let i = 0; i < 2; i++) {
      await pool.query("INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) VALUES($1,$2,'duplicate.offline',$3,'active','fixture')", [ids[i], tenants[i], `offline-${i}@example.invalid`]);
      const hash = await identity.hashPassword(passwords[i]);
      await pool.query("INSERT INTO identity.password_credentials(account_id,phc_hash,must_change) VALUES($1,$2,false)", [ids[i], hash]);
    }
    const call = async (tenantId: string | undefined, password: string) => {
      const req = new NextRequest("https://ops.oday.plus/login", { method: "POST", body: JSON.stringify({ username: "duplicate.offline", password, ...(tenantId ? { tenantId } : {}) }) });
      req.headers.set("origin", "https://ops.oday.plus"); req.headers.set("content-type", "application/json");
      return login(req);
    };
    for (let i = 0; i < 2; i++) {
      const response = await call(tenants[i], passwords[i]); results.push(response.status);
      if (response.status === 200) {
        const stored = await pool.query("SELECT account_id,provider FROM identity.sessions WHERE account_id=$1", [ids[i]]);
        expect(stored.rows.length).toBe(1); expect(stored.rows[0].provider).toBe("local_password");
      }
    }
    const before = await pool.query("SELECT session_id,account_id FROM identity.sessions WHERE account_id=ANY($1::uuid[]) ORDER BY session_id", [ids]);
    const ambiguous = await call(undefined, passwords[0]);
    const mismatch = await call(tenants[1], passwords[0]);
    expect(results).toEqual([200, 200]);
    expect(ambiguous.status).toBe(401); expect(mismatch.status).toBe(401);
    expect(ambiguous.headers.get("set-cookie")).toBeNull(); expect(mismatch.headers.get("set-cookie")).toBeNull();
    expect((await pool.query("SELECT session_id,account_id FROM identity.sessions WHERE account_id=ANY($1::uuid[]) ORDER BY session_id", [ids])).rows).toEqual(before.rows);
  } finally {
    vi.unstubAllEnvs(); setIdentityStoreForTests(undefined); setSessionStoreForTests(undefined); setLoginThrottleForTests(undefined);
    await pool.end();
  }
});
