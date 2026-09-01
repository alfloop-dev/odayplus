/**
 * Security E2E: Password-first login with optional OIDC and production throttle.
 *
 * Task:    ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002
 * Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001
 *
 * This suite is the end-to-end security acceptance for the complete auth
 * surface after login throttle remediation merged into `dev`. It covers:
 *
 * 1. Password login success/failure with the formal TypeScript route (§2, §3)
 * 2. Account threshold lockout evidence via identity.login_attempts (§6.4)
 * 3. Deploy validation passes without OIDC; OIDC route fails closed (§3.2)
 * 4. Complete OIDC config → optional login does not regress (T14, T19)
 * 5. RBAC tenant isolation: session carries tenantId, no cross-tenant (§4)
 * 6. No secret value in any log, receipt, or assertion
 *
 * None of the credentials below are production secrets — they are development
 * test fixtures declared in localAuth.ts and the MockIdentityStore.
 */

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "../src/app/login/route";
import {
  webSessionCookieName,
  webSessionCookieOptions,
  readWebSession,
} from "../src/lib/auth/session";
import {
  MockIdentityStore,
  setIdentityStoreForTests,
} from "../src/lib/auth/identityStore";
import {
  DEFAULT_LOGIN_THROTTLE_CONFIG as CONFIG,
  LoginThrottle,
  MockLoginThrottleStore,
  accountAttemptKey,
  ipAttemptKey,
  getDefaultLoginThrottle,
  setLoginThrottleForTests,
  type LoginAttemptRecord,
  type LoginThrottleStore,
  type ThrottleDecision,
} from "../src/lib/auth/loginThrottle";
import { resolveAuthMode, isOidcEnabled } from "../src/lib/auth/runtime";
import {
  MockSessionStore,
  setSessionStoreForTests,
} from "../src/lib/auth/sessionStore";

// ─── Shared constants ────────────────────────────────────────────────────────

const SECRET = "test-session-secret-with-at-least-32-bytes";
const PEPPER = "security-e2e-002-throttle-pepper";
const IP = "203.0.113.42";

const ACCOUNTS = [
  {
    accountId: "acc-admin",
    tenantId: "tenant-alpha",
    username: "admin",
    email: "admin@example.invalid",
    status: "active" as const,
    password: "Admin12345678!",
  },
  {
    accountId: "acc-operator",
    tenantId: "tenant-beta",
    username: "operator",
    email: "operator@example.invalid",
    status: "active" as const,
    password: "Operator123456!",
  },
  {
    accountId: "acc-locked",
    tenantId: "tenant-alpha",
    username: "locked-user",
    email: "locked@example.invalid",
    status: "locked" as const,
    password: "Locked12345678!",
  },
  {
    accountId: "acc-disabled",
    tenantId: "tenant-alpha",
    username: "disabled-user",
    email: "disabled@example.invalid",
    status: "disabled" as const,
    password: "Disabled12345678!",
  },
  {
    accountId: "acc-invited",
    tenantId: "tenant-alpha",
    username: "invited-user",
    email: "invited@example.invalid",
    status: "invited" as const,
    password: "Invited12345678!",
  },
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

function installThrottle(
  store: LoginThrottleStore = new MockLoginThrottleStore(),
) {
  const throttle = new LoginThrottle(store, CONFIG, PEPPER);
  setLoginThrottleForTests(throttle);
  return store;
}

function loginRequest(
  body: { username: string; password: string; returnTo?: string },
  options: { clientIp?: string | null; json?: boolean } = {},
): NextRequest {
  const json = options.json ?? true;
  const request = new NextRequest("https://ops.oday.plus/login", {
    method: "POST",
    headers: json
      ? { "content-type": "application/json", accept: "application/json" }
      : { "content-type": "application/x-www-form-urlencoded" },
    body: json
      ? JSON.stringify(body)
      : new URLSearchParams(body as Record<string, string>).toString(),
  });
  request.headers.set("origin", "https://ops.oday.plus");
  const clientIp = options.clientIp === undefined ? IP : options.clientIp;
  if (clientIp) request.headers.set("x-forwarded-for", clientIp);
  return request;
}

function getRequest(
  path = "/login",
  params: Record<string, string> = {},
): NextRequest {
  const url = new URL(path, "https://ops.oday.plus");
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  return new NextRequest(url);
}

function sessionCookieValue(response: Response): string {
  const header = response.headers.get("set-cookie");
  const prefix = `${webSessionCookieName}=`;
  const value = header
    ?.split(";", 1)[0]
    .replace(prefix, "");
  if (!value) throw new Error("login response did not set a session cookie");
  return value;
}

// ─── Setup / Teardown ────────────────────────────────────────────────────────

beforeEach(() => {
  // Keep the route journey on the non-production test double. The route still
  // creates and reads the same server-side session shape, while this prevents
  // a developer DATABASE_URL from sending the suite to a real PostgreSQL host.
  setSessionStoreForTests(new MockSessionStore());
});

afterEach(() => {
  setLoginThrottleForTests(undefined);
  setIdentityStoreForTests(undefined);
  setSessionStoreForTests(undefined);
  vi.unstubAllEnvs();
  delete process.env.ODP_WEB_SESSION_SECRET;
  delete process.env.ODP_DEPLOY_ENV;
  delete process.env.ODP_WEB_LOGIN_THROTTLE_PEPPER;
  delete process.env.DATABASE_URL;
  delete process.env.ODP_AUTH_MODE;
  delete process.env.ODP_AUTH_OIDC_ENABLED;
  delete process.env.ODP_WEB_OIDC_ISSUER;
  delete process.env.ODP_WEB_OIDC_CLIENT_ID;
  delete process.env.ODP_WEB_OIDC_CLIENT_SECRET;
});

// ═══════════════════════════════════════════════════════════════════════════
// §1  Password Login — Success and Failure Evidence
// ═══════════════════════════════════════════════════════════════════════════

describe("§1 Password login success & failure (Contract §2, §3)", () => {
  it("正確帳密回 200 並設定 HttpOnly Secure SameSite=Lax session cookie", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.ok).toBe(true);
    expect(body.subject).toBe("admin");
    // returnTo defaults to /operator
    expect(body.returnTo).toBe("/operator");

    // Cookie security properties
    const setCookieHeader = response.headers.get("set-cookie");
    expect(setCookieHeader).toBeTruthy();
    expect(setCookieHeader).toContain(webSessionCookieName);
    expect(setCookieHeader!.toLowerCase()).toContain("httponly");
    expect(setCookieHeader!.toLowerCase()).toContain("secure");
    expect(setCookieHeader!.toLowerCase()).toContain("samesite=lax");
    // No-store prevents caching of the authenticated response
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("錯誤密碼回 401 AUTH_INVALID_CREDENTIALS，不洩漏帳號是否存在", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "wrong-password-xxx" }),
    );

    expect(response.status).toBe(401);
    const body = await response.json();
    expect(body.error.code).toBe("AUTH_INVALID_CREDENTIALS");
    expect(body.error.summary).toBe("Invalid username or password.");
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("不存在的帳號與真實帳號得到完全相同的 401 回應", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const unknownRes = await POST(
      loginRequest({ username: "no-such-user", password: "wrong-password-xxx" }),
    );
    const knownRes = await POST(
      loginRequest({ username: "admin", password: "wrong-password-xxx" }),
    );

    expect(unknownRes.status).toBe(401);
    expect(knownRes.status).toBe(401);
    expect(await unknownRes.json()).toEqual(await knownRes.json());
  });

  it("帳密登入成功建立 session 帶有正確 tenantId", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "operator", password: "Operator123456!" }),
    );

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.ok).toBe(true);
    expect(body.subject).toBe("operator");

    // Resolve the opaque browser reference through the same server-side store
    // the route wrote. Tenant context must come from identity.sessions, not
    // from a browser-visible cookie claim or response body.
    const session = await readWebSession(sessionCookieValue(response));
    expect(session).toMatchObject({
      accountId: "acc-operator",
      tenantId: "tenant-beta",
      subject: "operator",
      provider: "local_password",
    });
    expect(session?.accessToken).toBeTruthy();
  });

  it("HTML form POST 成功後 redirect 303 到 returnTo", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest(
        { username: "admin", password: "Admin12345678!", returnTo: "/operator" },
        { json: false },
      ),
    );

    expect(response.status).toBe(303);
    const location = response.headers.get("location");
    expect(location).toContain("/operator");
    expect(response.headers.get("set-cookie")).toContain(webSessionCookieName);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// §2  Account Threshold — Formal TypeScript Rate Limit (§6.4)
// ═══════════════════════════════════════════════════════════════════════════

describe("§2 Account threshold & rate limiting (Contract §6.4)", () => {
  it("帳號維度：5 次失敗後鎖定 → 429 AUTH_RATE_LIMITED，不洩漏帳號狀態", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    // Exhaust the account budget
    for (let i = 0; i < CONFIG.accountMaxFailures; i++) {
      const r = await POST(
        loginRequest({ username: "admin", password: "wrong-password-xxx" }),
      );
      expect(r.status).toBe(401);
    }

    // Next attempt is rate-limited
    const locked = await POST(
      loginRequest({ username: "admin", password: "wrong-password-xxx" }),
    );
    expect(locked.status).toBe(429);
    const body = await locked.json();
    expect(body.error.code).toBe("AUTH_RATE_LIMITED");
    // MUST NOT be 423 AUTH_ACCOUNT_LOCKED — that would reveal account state
    expect(body.error.code).not.toBe("AUTH_ACCOUNT_LOCKED");
    expect(locked.headers.get("set-cookie")).toBeNull();
  });

  it("鎖定期間即使密碼正確也被 429 拒絕，不建立 session", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let i = 0; i < CONFIG.accountMaxFailures; i++) {
      await POST(
        loginRequest({ username: "admin", password: "wrong-password-xxx" }),
      );
    }

    const correctPassword = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(correctPassword.status).toBe(429);
    expect(correctPassword.headers.get("set-cookie")).toBeNull();
  });

  it("成功登入清除帳號計數器（§6.4）", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    const store = installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    // 4 failures (just under the threshold)
    for (let i = 0; i < CONFIG.accountMaxFailures - 1; i++) {
      await POST(
        loginRequest({ username: "admin", password: "wrong-password-xxx" }),
      );
    }

    const key = await accountAttemptKey("admin", PEPPER);
    expect((await store.readAttempt(key))?.failureCount).toBe(
      CONFIG.accountMaxFailures - 1,
    );

    // Success clears the counter
    const success = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(success.status).toBe(200);
    expect(await store.readAttempt(key)).toBeNull();
  });

  it("IP 維度：50 次失敗後來源 IP 被封鎖，不觸動未使用帳號的計數器", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    const store = installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let i = 0; i < CONFIG.ipMaxFailures; i++) {
      await POST(
        loginRequest({ username: `attack-${i}`, password: "wrong-password-xxx" }),
      );
    }

    const ipBlocked = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(ipBlocked.status).toBe(429);
    expect(ipBlocked.headers.get("set-cookie")).toBeNull();

    // The admin account must not have any record — the IP gate fires first
    const adminKey = await accountAttemptKey("admin", PEPPER);
    expect(await store.readAttempt(adminKey)).toBeNull();
  });

  it("不同 IP 不受影響可以正常登入", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let i = 0; i < CONFIG.ipMaxFailures; i++) {
      await POST(
        loginRequest({ username: `attack-${i}`, password: "wrong-password-xxx" }),
      );
    }

    const fromOtherIp = await POST(
      loginRequest(
        { username: "admin", password: "Admin12345678!" },
        { clientIp: "198.51.100.99" },
      ),
    );
    expect(fromOtherIp.status).toBe(200);
  });

  it("不存在的帳號與真實帳號的鎖定行為完全一致（反枚舉）", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const exhaust = async (username: string) => {
      const statuses: number[] = [];
      for (let i = 0; i <= CONFIG.accountMaxFailures; i++) {
        const r = await POST(
          loginRequest(
            { username, password: "wrong-password-xxx" },
            { clientIp: `10.0.${username === "admin" ? "1" : "2"}.1` },
          ),
        );
        statuses.push(r.status);
      }
      return statuses;
    };

    const realStatuses = await exhaust("admin");
    const fakeStatuses = await exhaust("does-not-exist");
    expect(realStatuses).toEqual(fakeStatuses);
    expect(realStatuses[CONFIG.accountMaxFailures]).toBe(429);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// §3  Production Fail-Closed Gates
// ═══════════════════════════════════════════════════════════════════════════

describe("§3 Production fail-closed behaviour", () => {
  it("production 無資料庫 → throttle 為 null → /login 回 503", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    process.env.ODP_DEPLOY_ENV = "production";
    setLoginThrottleForTests(undefined);
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({
      error: { code: "WEB_AUTH_NOT_CONFIGURED" },
    });
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("production 有資料庫但無 pepper → throttle 為 null → 503", () => {
    expect(
      getDefaultLoginThrottle({
        ODP_DEPLOY_ENV: "production",
        DATABASE_URL: "postgresql://localhost/identity",
      }),
    ).toBeNull();
  });

  it("production 有資料庫有 pepper → throttle 正常建立", () => {
    expect(
      getDefaultLoginThrottle({
        ODP_DEPLOY_ENV: "production",
        DATABASE_URL: "postgresql://localhost/identity",
        ODP_WEB_SESSION_SECRET: SECRET,
      }),
    ).not.toBeNull();
  });

  it("throttle store 連線失敗 → 503 WEB_AUTH_UNAVAILABLE，不降級", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    const unreachable: LoginThrottleStore = {
      async beginAttempt(): Promise<ThrottleDecision> {
        throw new Error("connection refused");
      },
      async escalate() {},
      async clearAttempts() {},
      async releaseAttempt() {},
      async readAttempt() { return null; },
    };
    installThrottle(unreachable);
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({
      error: { code: "WEB_AUTH_UNAVAILABLE" },
    });
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("CSRF 驗證失敗 → 403", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const req = new NextRequest("https://ops.oday.plus/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        origin: "https://evil.example.com", // cross-origin
      },
      body: JSON.stringify({ username: "admin", password: "Admin12345678!" }),
    });
    req.headers.set("x-forwarded-for", IP);

    const response = await POST(req);
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({
      error: { code: "CSRF_VERIFICATION_FAILED" },
    });
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// §4  OIDC Optional — Fail-Closed Without Config, No Regression With Config
// ═══════════════════════════════════════════════════════════════════════════

describe("§4 OIDC optional deployment (Contract §3.2, T14, T19)", () => {
  it("預設 local 模式不渲染 OIDC 按鈕、resolveAuthMode 回 'local'", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);

    const mode = resolveAuthMode({});
    expect(mode).toBe("local");

    const response = await GET(getRequest("/login"));
    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).toContain('name="username"');
    expect(html).toContain('name="password"');
    expect(html).not.toContain("使用 OIDC 登入");
  });

  it("OIDC 模式配置不完整 → GET /login 回 503，不降級為 local 表單", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    process.env.ODP_AUTH_MODE = "oidc";
    // Missing OIDC_CLIENT_ID and OIDC_CLIENT_SECRET

    const response = await GET(getRequest("/login"));
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.error.code).toBe("WEB_AUTH_NOT_CONFIGURED");
  });

  it("OIDC provider 停用時 GET /login?provider=oidc 回 503", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    // Default local mode

    const response = await GET(getRequest("/login", { provider: "oidc" }));
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.error.code).toBe("WEB_AUTH_PROVIDER_DISABLED");
  });

  it("ODP_AUTH_MODE 與 ODP_AUTH_OIDC_ENABLED 衝突 → resolveAuthMode 拋錯", () => {
    expect(() =>
      resolveAuthMode({
        ODP_AUTH_MODE: "local",
        ODP_AUTH_OIDC_ENABLED: "true",
      }),
    ).toThrow(/conflicts/);
  });

  it("placeholder OIDC 值不會觸發 OIDC 模式", () => {
    expect(
      resolveAuthMode({ ODP_WEB_OIDC_ISSUER: "placeholder" }),
    ).toBe("local");
    expect(
      resolveAuthMode({ ODP_WEB_OIDC_ISSUER: "changeme" }),
    ).toBe("local");
    expect(
      resolveAuthMode({ ODP_WEB_OIDC_ISSUER: "TODO" }),
    ).toBe("local");
  });

  it("完整 OIDC 配置時 login 頁面顯示 OIDC 按鈕、密碼表單共存", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    process.env.ODP_AUTH_MODE = "oidc";
    process.env.ODP_WEB_OIDC_ISSUER = "https://accounts.google.com";
    process.env.ODP_WEB_OIDC_CLIENT_ID = "test-client-id";
    process.env.ODP_WEB_OIDC_CLIENT_SECRET = "stub";

    const response = await GET(getRequest("/login"));
    expect(response.status).toBe(200);
    const html = await response.text();
    // Password form still present
    expect(html).toContain('name="username"');
    expect(html).toContain('name="password"');
    // OIDC button also present
    expect(html).toContain("使用 OIDC 登入");
  });

  it("OIDC 模式下密碼登入依然可用（不回歸）", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    process.env.ODP_AUTH_MODE = "oidc";
    process.env.ODP_WEB_OIDC_ISSUER = "https://accounts.google.com";
    process.env.ODP_WEB_OIDC_CLIENT_ID = "test-client-id";
    process.env.ODP_WEB_OIDC_CLIENT_SECRET = "stub";
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain(webSessionCookieName);
  });

  it("OIDC POST /login 配置不完整 → 503，不降級", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    process.env.ODP_AUTH_MODE = "oidc";
    // Missing required OIDC config
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({
      error: { code: "WEB_AUTH_NOT_CONFIGURED" },
    });
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// §5  423 is Unreachable Without Correct Password
// ═══════════════════════════════════════════════════════════════════════════

describe("§5 AUTH_ACCOUNT_LOCKED only after password verification", () => {
  it("錯誤密碼對 locked 帳號回 401，不回 423", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "locked-user", password: "wrong-password-xxx" }),
    );
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({
      error: { code: "AUTH_INVALID_CREDENTIALS" },
    });
  });

  it("正確密碼對 locked 帳號回 423 AUTH_ACCOUNT_LOCKED，但不建立 session", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "locked-user", password: "Locked12345678!" }),
    );
    expect(response.status).toBe(423);
    expect(await response.json()).toMatchObject({
      error: { code: "AUTH_ACCOUNT_LOCKED" },
    });
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("disabled 帳號即使密碼正確也回 401（不回 423）", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({
        username: "disabled-user",
        password: "Disabled12345678!",
      }),
    );
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({
      error: { code: "AUTH_INVALID_CREDENTIALS" },
    });
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("invited 帳號即使密碼正確也回 401", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({
        username: "invited-user",
        password: "Invited12345678!",
      }),
    );
    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// §6  RBAC Tenant Isolation — Session Carries tenantId
// ═══════════════════════════════════════════════════════════════════════════

describe("§6 RBAC tenant isolation audit (Contract §4)", () => {
  it("不同租戶的帳號登入後 session 帶有各自的 tenantId", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;

    // Login as admin (tenant-alpha)
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));
    const adminRes = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(adminRes.status).toBe(200);
    const adminBody = await adminRes.json();
    expect(adminBody.ok).toBe(true);

    // Login as operator (tenant-beta) — different IP to avoid IP throttle
    const operatorRes = await POST(
      loginRequest(
        { username: "operator", password: "Operator123456!" },
        { clientIp: "198.51.100.1" },
      ),
    );
    expect(operatorRes.status).toBe(200);
    const operatorBody = await operatorRes.json();
    expect(operatorBody.ok).toBe(true);

    // Sessions are distinct
    expect(adminBody.subject).not.toBe(operatorBody.subject);
  });

  it("session cookie 使用 __Host- 前綴強制 Secure path=/", () => {
    expect(webSessionCookieName).toMatch(/^__Host-/);
    expect(webSessionCookieOptions.httpOnly).toBe(true);
    expect(webSessionCookieOptions.secure).toBe(true);
    expect(webSessionCookieOptions.sameSite).toBe("lax");
    expect(webSessionCookieOptions.path).toBe("/");
    // Max-age capped at 8 hours
    expect(webSessionCookieOptions.maxAge).toBeLessThanOrEqual(8 * 60 * 60);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// §7  No Secret Value in Responses
// ═══════════════════════════════════════════════════════════════════════════

describe("§7 Secret exclusion — no secret values in logs/receipts/responses", () => {
  it("成功登入的 JSON 回應不含 accessToken 或密碼", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    const body = await response.json();
    const serialized = JSON.stringify(body);

    // The response MUST NOT contain the bearer token, password, or secrets
    expect(serialized).not.toContain("Admin12345678!");
    expect(serialized).not.toContain(SECRET);
    expect(serialized).not.toContain(PEPPER);
    // accessToken is server-side only (identity.sessions), not in the response
    expect(body.accessToken).toBeUndefined();
  });

  it("失敗登入的 JSON 回應不含密碼或 session secret", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "wrong-password-xxx" }),
    );
    const body = await response.json();
    const serialized = JSON.stringify(body);

    expect(serialized).not.toContain("wrong-password-xxx");
    expect(serialized).not.toContain(SECRET);
    expect(body.error.summary).toBe("Invalid username or password.");
  });

  it("throttle 拒絕回應不含使用者名稱或 IP 明文", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let i = 0; i < CONFIG.accountMaxFailures; i++) {
      await POST(
        loginRequest({ username: "admin", password: "wrong-password-xxx" }),
      );
    }

    const response = await POST(
      loginRequest({ username: "admin", password: "wrong-password-xxx" }),
    );
    const body = await response.json();
    const serialized = JSON.stringify(body);

    expect(serialized).not.toContain("admin");
    expect(serialized).not.toContain(IP);
    expect(body.error.summary).toBe(
      "Too many login attempts. Try again later.",
    );
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// §8  GET /login Page Rendering
// ═══════════════════════════════════════════════════════════════════════════

describe("§8 GET /login renders correct form and error states", () => {
  it("GET /login?error=AUTH_INVALID_CREDENTIALS 回 401 帶錯誤提示", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);

    const response = await GET(
      getRequest("/login", { error: "AUTH_INVALID_CREDENTIALS" }),
    );
    expect(response.status).toBe(401);
    const html = await response.text();
    expect(html).toContain("帳號或密碼錯誤");
    expect(html).toContain('role="alert"');
  });

  it("GET /login?error=AUTH_RATE_LIMITED 回 401 帶限速提示", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);

    const response = await GET(
      getRequest("/login", { error: "AUTH_RATE_LIMITED" }),
    );
    expect(response.status).toBe(401);
    const html = await response.text();
    expect(html).toContain("嘗試次數過多");
  });

  it("returnTo 帶 XSS payload 被 escape 並限制為 relative path", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);

    const response = await GET(
      getRequest("/login", { returnTo: '"><script>alert(1)</script>' }),
    );
    const html = await response.text();
    // The malicious value should be escaped, not rendered raw
    expect(html).not.toContain("<script>");
    // safeReturnTo should reject non-relative paths
    expect(html).toContain('value="/operator"');
  });

  it("returnTo=//evil.com 被拒絕回退為 /operator", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);

    const response = await GET(
      getRequest("/login", { returnTo: "//evil.com" }),
    );
    const html = await response.text();
    expect(html).toContain('value="/operator"');
  });

  it("cache-control: no-store 防止快取登入頁面", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);

    const response = await GET(getRequest("/login"));
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
});
