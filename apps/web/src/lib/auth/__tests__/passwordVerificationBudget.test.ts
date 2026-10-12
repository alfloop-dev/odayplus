import { NextRequest } from "next/server";
import { afterEach, expect, it, vi } from "vitest";
import { POST } from "../../../app/auth/password/route";
import { POST as LOGIN } from "../../../app/login/route";
import { MockIdentityStore, PostgresIdentityStore, setIdentityStoreForTests } from "../identityStore";
import { LoginThrottle, MockLoginThrottleStore, setLoginThrottleForTests } from "../loginThrottle";
import { MockSessionStore, setSessionStoreForTests } from "../sessionStore";
import { sealWebSessionReference, webSessionCookieName } from "../session";

const SECRET = "OFFLINE-budget-session-seal-key-at-least-thirty-two-bytes";
afterEach(() => {
  setIdentityStoreForTests(undefined); setSessionStoreForTests(undefined); setLoginThrottleForTests(undefined);
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});

it("concurrent wrong current-password guesses share the existing upfront account budget", async () => {
  vi.stubEnv("NODE_ENV", "test"); vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
  const identity = new MockIdentityStore([{ accountId:"offline",tenantId:"offline-tenant",username:"offline-user",
    email:"offline@example.invalid",status:"active",password:"Old-Offline-Password-742!" }]);
  const sessions = new MockSessionStore(); const attempts = new MockLoginThrottleStore();
  setIdentityStoreForTests(identity); setSessionStoreForTests(sessions);
  setLoginThrottleForTests(new LoginThrottle(attempts,undefined,"offline-pepper"));
  await sessions.createSession({ sessionId:"offline-sid",accountId:"offline",provider:"local_password",accessToken:"offline-token",
    subject:"offline-user",tenantId:"offline-tenant",idleTimeoutMs:1800000,absoluteLifetimeMs:3600000 });
  const now = Math.floor(Date.now()/1000);
  const cookie = await sealWebSessionReference({kind:"web-session",sid:"offline-sid",issuedAt:now,expiresAt:now+3600});
  const verify = vi.spyOn(identity,"verifyPassword");
  const responses = await Promise.all(Array.from({length:8}, (_, index) => {
    const req = new NextRequest("https://ops.oday.plus/auth/password", {method:"POST",body:JSON.stringify({
      currentPassword:`Wrong-offline-guess-${index}!`,newPassword:"New-Offline-Password-983!"})});
    req.headers.set("content-type","application/json"); req.headers.set("origin","https://ops.oday.plus");
    req.cookies.set(webSessionCookieName,cookie); return POST(req);
  }));
  expect(verify).toHaveBeenCalled(); // actual current-password handler, not cookie-less refusal
  expect(responses.every(r=>r.status===401 || r.status===429)).toBe(true);
  expect(responses.filter(r=>r.status===429).length).toBeGreaterThanOrEqual(3);
  expect(verify.mock.calls.length).toBeLessThanOrEqual(5);
  expect(responses.every(r=>r.headers.get("set-cookie")===null)).toBe(true);
  expect((await identity.getPasswordCredential("offline"))!.phcHash).toBe("Old-Offline-Password-742!");
  expect(await sessions.validateSession("offline-sid")).not.toBeNull();
});

it.each(["missing", "lost-reservation"])("budget %s refuses before password work, without cookie success/clearance", async (failure) => {
  vi.stubEnv("NODE_ENV", "test"); vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
  const identity = new MockIdentityStore([{accountId:"offline",tenantId:"offline-tenant",username:"offline-user",
    email:"offline@example.invalid",status:"active",password:"Old-Offline-Password-742!"}]);
  const sessions = new MockSessionStore(); const throttle = new LoginThrottle(new MockLoginThrottleStore());
  if (failure === "lost-reservation") vi.spyOn(throttle,"beginAttempt").mockRejectedValue(new Error("PRIVATE-BUDGET-PROVIDER"));
  setIdentityStoreForTests(identity); setSessionStoreForTests(sessions);
  setLoginThrottleForTests(failure === "missing" ? null : throttle);
  await sessions.createSession({sessionId:"offline-sid",accountId:"offline",provider:"local_password",accessToken:"offline-token",
    subject:"offline-user",tenantId:"offline-tenant",idleTimeoutMs:1800000,absoluteLifetimeMs:3600000});
  const now = Math.floor(Date.now()/1000);
  const cookie = await sealWebSessionReference({kind:"web-session",sid:"offline-sid",issuedAt:now,expiresAt:now+3600});
  const req = new NextRequest("https://ops.oday.plus/auth/password",{method:"POST",body:JSON.stringify({
    currentPassword:"Old-Offline-Password-742!",newPassword:"New-Offline-Password-983!"})});
  req.headers.set("origin","https://ops.oday.plus"); req.headers.set("content-type","application/json");
  req.cookies.set(webSessionCookieName,cookie);
  const verify = vi.spyOn(identity,"verifyPassword"); const response = await POST(req);
  expect(response.status).toBe(503); expect(response.headers.get("set-cookie")).toBeNull();
  expect(await response.text()).not.toContain("PRIVATE-BUDGET-PROVIDER"); expect(verify).not.toHaveBeenCalled();
  expect(await sessions.validateSession("offline-sid")).not.toBeNull();
});

it.each(["password-change", "login"])("another tenant's same-name %s cannot reset victim's resolved verification budget", async (kind) => {
  vi.stubEnv("NODE_ENV", "test"); vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
  const entries = ["victim", "other"].map(id => ({accountId:id,tenantId:`tenant-${id}`,username:"same-name",
    email:`${id}@example.invalid`,status:"active" as const,password:`Old-Offline-${id}-742!`}));
  // Existing MockIdentityStore indexes usernames globally, unlike PG's tenant
  // uniqueness. Compose separate authoritative account stores to avoid dropping
  // victim silently and accidentally proving only a missing-account 401.
  const stores = new Map(entries.map(e=>[e.accountId,new MockIdentityStore([e])]));
  class PerAccountStore extends MockIdentityStore {
    async findAccountById(id: string) { return stores.get(id)?.findAccountById(id) ?? null; }
    async getPasswordCredential(id: string) { return stores.get(id)?.getPasswordCredential(id) ?? null; }
    async changePassword(id: string, hash: string, revision?: string) { return stores.get(id)!.changePassword(id,hash,revision); }
  }
  const identity = new PerAccountStore(); const sessions = new MockSessionStore();
  setIdentityStoreForTests(identity); setSessionStoreForTests(sessions);
  setLoginThrottleForTests(new LoginThrottle(new MockLoginThrottleStore()));
  const now = Math.floor(Date.now()/1000); const cookies: Record<string,string> = {};
  for (const id of ["victim", "other"]) {
    await sessions.createSession({sessionId:`sid-${id}`,accountId:id,provider:"local_password",accessToken:"offline",
      subject:"same-name",tenantId:`tenant-${id}`,idleTimeoutMs:1800000,absoluteLifetimeMs:3600000});
    cookies[id] = await sealWebSessionReference({kind:"web-session",sid:`sid-${id}`,issuedAt:now,expiresAt:now+3600});
  }
  const change = (id: string, password: string) => {
    const req = new NextRequest("https://ops.oday.plus/auth/password",{method:"POST",body:JSON.stringify({
      currentPassword:password,newPassword:"Next-Offline-Password-843!"})});
    req.headers.set("origin","https://ops.oday.plus"); req.headers.set("content-type","application/json");
    req.cookies.set(webSessionCookieName,cookies[id]); return POST(req);
  };
  const victimBefore = await identity.getPasswordCredential("victim"); expect(victimBefore).not.toBeNull();
  const verify = vi.spyOn(identity,"verifyPassword");
  for (let i=0;i<4;i++) expect((await change("victim",`Wrong-offline-${i}!`)).status).toBe(401);
  expect(verify).toHaveBeenCalledTimes(4);
  if (kind === "password-change") expect((await change("other","Old-Offline-other-742!")).status).toBe(200);
  else {
    vi.spyOn(identity,"findAccountByUsername").mockResolvedValue(entries[1]); // resolved OTHER account, not assumed global username identity
    const req = new NextRequest("https://ops.oday.plus/login",{method:"POST",body:JSON.stringify({username:"same-name",password:"Old-Offline-other-742!"})});
    req.headers.set("origin","https://ops.oday.plus"); req.headers.set("content-type","application/json");
    expect((await LOGIN(req)).status).toBe(200);
  }
  verify.mockClear();
  const replies = await Promise.all(Array.from({length:5},(_,i)=>change("victim",`More-offline-${i}!`)));
  expect(replies.filter(r=>r.status===429).length).toBeGreaterThanOrEqual(4);
  expect(verify.mock.calls.length).toBeLessThanOrEqual(1);
  expect(await identity.getPasswordCredential("victim")).toEqual(victimBefore);
  expect(await sessions.validateSession("sid-victim")).not.toBeNull();
});

it("unknown-account dummy work uses one representative verify, never per-request hash plus verify", async () => {
  const hash = vi.fn().mockResolvedValue("offline-hash"); const verify = vi.fn().mockResolvedValue(false);
  const store = new PostgresIdentityStore(); Object.assign(store,{_argon2:{hash,verify,argon2id:2}});
  await Promise.all([store.dummyVerify(),store.dummyVerify(),store.dummyVerify()]);
  expect(hash).not.toHaveBeenCalled(); expect(verify).toHaveBeenCalledTimes(3);
  const phc = verify.mock.calls[0][0]; expect(phc).toContain("$argon2id$v=19$m=65536,t=3,p=1$");
});
