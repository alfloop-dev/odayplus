import { NextRequest } from "next/server";
import { afterEach, expect, it, vi } from "vitest";
import { base64UrlDecode } from "../crypto";
import { proxyApiRequest } from "../proxy";
import { MockSessionStore, setSessionStoreForTests } from "../sessionStore";
import { sealWebSessionReference, webSessionCookieName } from "../session";

const secret = "offline-session-sealing-key-at-least-thirty-two-bytes";
const signing = "offline-api-bridge-signing-key-at-least-thirty-two-bytes";
afterEach(()=>{setSessionStoreForTests(undefined);vi.unstubAllGlobals();vi.unstubAllEnvs();vi.restoreAllMocks();});
it.each(["healthy","missing-key"])("OIDC BFF %s proves server SID/token binding, never forwards a browser assertion", async (kind) => {
  vi.stubEnv("ODP_WEB_SESSION_SECRET",secret);vi.stubEnv("NODE_ENV","test");
  const sessions = new MockSessionStore();setSessionStoreForTests(sessions);
  const sid = crypto.randomUUID();const account = crypto.randomUUID();const tenant = crypto.randomUUID();
  const originalToken = "offline-original-oidc-token-not-a-live-credential";
  await sessions.createSession({sessionId:sid,accountId:account,provider:"oidc",accessToken:originalToken,
    subject:"offline-linked-subject",tenantId:tenant,idleTimeoutMs:1800000,absoluteLifetimeMs:3600000});
  const now = Math.floor(Date.now()/1000);
  const cookie = await sealWebSessionReference({kind:"web-session",sid,provider:"oidc",issuedAt:now,expiresAt:now+3600});
  const req = new NextRequest("https://ops.oday.plus/api/v1/operator/users",{method:"POST",body:"{}",
    headers:{"x-odp-oidc-session":"attacker-selected-assertion","authorization":"Bearer attacker","x-session-id":"attacker-sid"}});
  req.cookies.set(webSessionCookieName,cookie);
  req.headers.set("origin", req.nextUrl.origin);  // Actual same-origin mutation fixture.
  const fetchMock = vi.fn(async (_url: URL, init?: RequestInit)=>{
    const headers = new Headers(init?.headers);
    expect(headers.get("authorization")).toBe(`Bearer ${originalToken}`);
    expect(headers.get("x-session-id")).toBeNull();
    const reference = headers.get("x-odp-oidc-session")!;expect(reference).not.toBe("attacker-selected-assertion");
    const [head,body,sig] = reference.split(".");
    const claims = JSON.parse(new TextDecoder().decode(base64UrlDecode(body)));
    expect(claims).toMatchObject({sub:account,sid,tenant_id:tenant,purpose:"oidc-web-session.v1",iss:"urn:odp:identity:local",aud:"oday-api"});
    const hash = await crypto.subtle.digest("SHA-256",new TextEncoder().encode(originalToken));
    expect(claims.oidc_token_digest).toBe(Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join(""));
    const key = await crypto.subtle.importKey("raw",new TextEncoder().encode(signing),{name:"HMAC",hash:"SHA-256"},false,["verify"]);
    expect(await crypto.subtle.verify("HMAC",key,base64UrlDecode(sig),new TextEncoder().encode(`${head}.${body}`))).toBe(true);
    expect(claims.exp-now).toBeLessThanOrEqual(120);
    return Response.json({ok:true});
  });
  vi.stubGlobal("fetch",fetchMock);
  const response = await proxyApiRequest(req,"/api/v1/operator/users",{NODE_ENV:"production",ODP_API_BASE_URL:"https://offline.invalid",
    ODP_API_SERVICE_AUDIENCE:"https://offline.invalid",ODP_AUTH_LOCAL_AUDIENCES:"oday-api",
    ...(kind==="healthy"?{ODP_IDENTITY_TOKEN_SIGNING_KEY:signing}:{})}, {resolveServiceIdentityToken:async()=>"offline-service-token"});
  expect(response.status).toBe(kind==="healthy"?200:503);
  expect(fetchMock.mock.calls.length).toBe(kind==="healthy"?1:0);
  expect(response.headers.get("x-odp-oidc-session")).toBeNull();expect(response.headers.get("set-cookie")).toBeNull();
  expect(await sessions.validateSession(sid)).not.toBeNull();
});
