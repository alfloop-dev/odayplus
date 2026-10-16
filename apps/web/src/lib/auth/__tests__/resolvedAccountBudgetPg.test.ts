import { NextRequest } from "next/server";
import { Pool } from "pg";
import { describe, expect, it, vi } from "vitest";
import { POST as changePassword } from "../../../app/auth/password/route";
import { POST as login } from "../../../app/login/route";
import { PostgresIdentityStore, setIdentityStoreForTests } from "../identityStore";
import { LoginThrottle, PostgresLoginThrottleStore, setLoginThrottleForTests } from "../loginThrottle";
import { PostgresSessionStore, setSessionStoreForTests } from "../sessionStore";
import { sealWebSessionReference, webSessionCookieName } from "../session";

const url = process.env.ODP_TEST_SESSION_BOUNDARY_DATABASE_URL;
describe.skipIf(!url)("actual PG resolved account verification budget", () => {
  it.each(["password-change", "login"])("same-name cross-tenant %s never clears victim budget", async (kind) => {
    const pool = new Pool({connectionString:url, max:2});
    const identity = new PostgresIdentityStore(); const sessions = new PostgresSessionStore();
    const attempts = new PostgresLoginThrottleStore();
    // Existing fixture dependency injection, real production SQL/store methods.
    Object.assign(identity,{_pool:pool}); Object.assign(sessions,{_pool:pool}); Object.assign(attempts,{_pool:pool});
    const secret = "offline-account-budget-sealing-and-pepper-at-least32bytes";
    vi.stubEnv("NODE_ENV", "test"); vi.stubEnv("ODP_WEB_SESSION_SECRET",secret);
    setIdentityStoreForTests(identity); setSessionStoreForTests(sessions);
    setLoginThrottleForTests(new LoginThrottle(attempts,undefined,secret));
    const ids = ["347e1877-a09a-46d6-9022-683234bd2a71","89b39827-761c-4fbd-93fe-d75d4fffa649"];
    const tenants = ["f1905f13-a59d-4315-999f-e4f3ec629171","d4ad4a60-7d72-4b1f-aa27-587dd15e1e48"];
    const passwords = ["Old-Offline-Alpha-742!","Old-Offline-Beta-641!"];
    const sids = ids.map(()=>crypto.randomUUID()); const cookies: string[] = [];
    try {
      for (let i=0;i<2;i++) {
        await pool.query("INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) VALUES($1,$2,'shared.offline',$3,'active','offline-fixture')",[ids[i],tenants[i],`offline-${i}@example.invalid`]);
        const hash = await identity.hashPassword(passwords[i]);
        await pool.query("INSERT INTO identity.password_credentials(account_id,phc_hash,must_change) VALUES($1,$2,false)",[ids[i],hash]);
        const credential = await identity.getPasswordCredential(ids[i]);
        await sessions.createSession({sessionId:sids[i],accountId:ids[i],provider:"local_password",accessToken:"offline-token",
          subject:"shared.offline",tenantId:tenants[i],idleTimeoutMs:1800000,absoluteLifetimeMs:3600000,
          verifiedCredentialRevision:credential!.credentialRevision});
        const now = Math.floor(Date.now()/1000);
        cookies[i] = await sealWebSessionReference({kind:"web-session",sid:sids[i],issuedAt:now,expiresAt:now+3600});
      }
      // Use ACTUAL tenant-qualified SQL resolution, never an arbitrary global
      // row. The other tenant is the victim whose session is held.
      const resolved = await identity.findAccountByUsername("shared.offline", tenants[0]);
      const other = ids.indexOf(resolved!.accountId); expect(other).toBeGreaterThanOrEqual(0);
      const victim = 1-other; const before = await identity.getPasswordCredential(ids[victim]);
      const change = (i:number, password:string) => {
        const req = new NextRequest("https://ops.oday.plus/auth/password",{method:"POST",body:JSON.stringify({
          currentPassword:password,newPassword:"Next-Offline-Password-843!"})});
        req.headers.set("origin","https://ops.oday.plus");req.headers.set("content-type","application/json");
        req.cookies.set(webSessionCookieName,cookies[i]); return changePassword(req);
      };
      const verify = vi.spyOn(identity,"verifyPassword");
      for(let i=0;i<4;i++) expect((await change(victim,`Wrong-Offline-${i}!`)).status).toBe(401);
      expect(verify).toHaveBeenCalledTimes(4);
      if(kind==="password-change") expect((await change(other,passwords[other])).status).toBe(200);
      else {
        const req = new NextRequest("https://ops.oday.plus/login",{method:"POST",body:JSON.stringify({username:"shared.offline",password:passwords[other],tenantId:tenants[other]})});
        req.headers.set("origin","https://ops.oday.plus");req.headers.set("content-type","application/json");
        expect((await login(req)).status).toBe(200);
      }
      verify.mockClear();
      const results = await Promise.all(Array.from({length:5},(_,i)=>change(victim,`More-Offline-${i}!`)));
      expect(results.filter(r=>r.status===429).length).toBeGreaterThanOrEqual(4);
      expect(verify.mock.calls.length).toBeLessThanOrEqual(1);
      expect(await identity.getPasswordCredential(ids[victim])).toEqual(before);
      expect(await sessions.validateSession(sids[victim])).not.toBeNull();
    } finally {
      vi.restoreAllMocks();vi.unstubAllEnvs();setIdentityStoreForTests(undefined);setSessionStoreForTests(undefined);setLoginThrottleForTests(undefined);
      await pool.end();
    }
  });
});
