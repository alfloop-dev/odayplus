import { NextRequest } from "next/server";
import { afterEach, describe, expect, it } from "vitest";
import { POST } from "../../../app/login/route";
import {
  MockIdentityStore,
  setIdentityStoreForTests,
} from "../identityStore";
import {
  DEFAULT_LOGIN_THROTTLE_CONFIG as CONFIG,
  LoginThrottle,
  MockLoginThrottleStore,
  accountAttemptKey,
  getDefaultLoginThrottle,
  ipAttemptKey,
  setLoginThrottleForTests,
  type LoginAttemptRecord,
  type LoginThrottleStore,
  type ThrottleDecision,
} from "../loginThrottle";

const SECRET = "test-session-secret-with-at-least-32-bytes";
const PEPPER = "route-test-login-throttle-pepper";
const IP = "203.0.113.42";

const ACCOUNTS = [
  {
    accountId: "acc-admin",
    tenantId: "tenant-a",
    username: "admin",
    email: "admin@example.invalid",
    status: "active" as const,
    password: "Admin12345678!",
  },
];

/** Counts credential verifications so a refusal before them is observable. */
class CountingIdentityStore extends MockIdentityStore {
  verifyCalls = 0;

  async verifyPassword(
    phcHash: string,
    password: string,
  ): Promise<{ valid: boolean; newHash: string | null }> {
    this.verifyCalls += 1;
    return super.verifyPassword(phcHash, password);
  }
}

function installThrottle(store: LoginThrottleStore = new MockLoginThrottleStore()) {
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

afterEach(() => {
  setLoginThrottleForTests(undefined);
  setIdentityStoreForTests(undefined);
  process.env.ODP_WEB_SESSION_SECRET = undefined;
  delete process.env.ODP_WEB_SESSION_SECRET;
  delete process.env.ODP_DEPLOY_ENV;
});

describe("POST /login is throttled by identity.login_attempts (Contract §6.4)", () => {
  it("counts the attempt before the credential is verified", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    const store = installThrottle();
    const accountKey = await accountAttemptKey("admin", PEPPER);

    let recordAtVerifyTime: LoginAttemptRecord | null = null;
    class ObservingStore extends MockIdentityStore {
      async verifyPassword(phcHash: string, password: string) {
        recordAtVerifyTime = await store.readAttempt(accountKey);
        return super.verifyPassword(phcHash, password);
      }
    }
    setIdentityStoreForTests(new ObservingStore(ACCOUNTS));

    const response = await POST(
      loginRequest({ username: "admin", password: "wrong-password-123" }),
    );

    expect(response.status).toBe(401);
    // The row is already durable at the moment verification runs, so an
    // attempt that never reaches a verdict still counts.
    expect(recordAtVerifyTime).not.toBeNull();
    expect(recordAtVerifyTime!.failureCount).toBe(1);
  });

  it("locks the account after five failures and stops verifying credentials", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    const identity = new CountingIdentityStore(ACCOUNTS);
    setIdentityStoreForTests(identity);

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
      const response = await POST(
        loginRequest({ username: "admin", password: "wrong-password-123" }),
      );
      expect(response.status).toBe(401);
    }
    expect(identity.verifyCalls).toBe(CONFIG.accountMaxFailures);

    const locked = await POST(
      loginRequest({ username: "admin", password: "wrong-password-123" }),
    );

    expect(locked.status).toBe(423);
    expect(await locked.json()).toMatchObject({
      error: { code: "AUTH_ACCOUNT_LOCKED" },
    });
    // The refusal happens before any credential work.
    expect(identity.verifyCalls).toBe(CONFIG.accountMaxFailures);
  });

  it("refuses the correct password too while the account is locked", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
      await POST(loginRequest({ username: "admin", password: "wrong-password-123" }));
    }

    const response = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(response.status).toBe(423);
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("throttles an unknown username exactly like a real one", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    const attemptsFor = async (username: string) => {
      const seen: Array<{ status: number; body: unknown }> = [];
      for (let attempt = 0; attempt <= CONFIG.accountMaxFailures; attempt += 1) {
        const response = await POST(
          loginRequest({ username, password: "wrong-password-123" }),
        );
        seen.push({ status: response.status, body: await response.json() });
      }
      return seen;
    };

    const known = await attemptsFor("admin");
    const unknown = await attemptsFor("no-such-account");

    // Same status sequence and same bodies: the lockout is keyed on the
    // submitted username, so it is not an account existence oracle.
    expect(unknown).toEqual(known);
    expect(known[CONFIG.accountMaxFailures].status).toBe(423);
  });

  it("clears the account counter on a successful login", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    const store = installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures - 1; attempt += 1) {
      await POST(loginRequest({ username: "admin", password: "wrong-password-123" }));
    }
    expect(
      (await store.readAttempt(await accountAttemptKey("admin", PEPPER)))
        ?.failureCount,
    ).toBe(CONFIG.accountMaxFailures - 1);

    const success = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );
    expect(success.status).toBe(200);

    expect(
      await store.readAttempt(await accountAttemptKey("admin", PEPPER)),
    ).toBeNull();
    // A fresh budget: five more failures are needed to lock again.
    for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
      const response = await POST(
        loginRequest({ username: "admin", password: "wrong-password-123" }),
      );
      expect(response.status).toBe(401);
    }
  });

  it("returns 429 without account detail once the source IP is blocked", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    const store = installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let attempt = 0; attempt < CONFIG.ipMaxFailures; attempt += 1) {
      await POST(
        loginRequest({ username: `victim-${attempt}`, password: "wrong-password-123" }),
      );
    }

    const response = await POST(
      loginRequest({ username: "admin", password: "Admin12345678!" }),
    );

    expect(response.status).toBe(429);
    expect(await response.json()).toEqual({
      error: {
        code: "AUTH_RATE_LIMITED",
        summary: "Too many login attempts. Try again later.",
      },
    });
    // A blocked source must not push an untouched account towards its lockout.
    expect(
      await store.readAttempt(await accountAttemptKey("admin", PEPPER)),
    ).toBeNull();
  });

  it("keeps a different source IP working when one IP is blocked", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let attempt = 0; attempt < CONFIG.ipMaxFailures; attempt += 1) {
      await POST(
        loginRequest({ username: `victim-${attempt}`, password: "wrong-password-123" }),
      );
    }

    const response = await POST(
      loginRequest(
        { username: "admin", password: "Admin12345678!" },
        { clientIp: "198.51.100.9" },
      ),
    );
    expect(response.status).toBe(200);
  });

  it("redirects an HTML form submission back to /login with the throttle code", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
      await POST(
        loginRequest(
          { username: "admin", password: "wrong-password-123", returnTo: "/operator" },
          { json: false },
        ),
      );
    }

    const response = await POST(
      loginRequest(
        { username: "admin", password: "wrong-password-123", returnTo: "/operator" },
        { json: false },
      ),
    );

    expect(response.status).toBe(303);
    const location = response.headers.get("location");
    expect(location).toContain("error=AUTH_ACCOUNT_LOCKED");
    expect(location).toContain("returnTo=%2Foperator");
  });

  it("counts the source IP once per attempt and gives it back on success", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    const store = installThrottle();
    setIdentityStoreForTests(new MockIdentityStore(ACCOUNTS));

    await POST(loginRequest({ username: "admin", password: "wrong-password-123" }));
    await POST(loginRequest({ username: "admin", password: "Admin12345678!" }));

    // Only the failure is still charged to the IP budget.
    expect(
      (await store.readAttempt(await ipAttemptKey(IP, PEPPER)))?.failureCount,
    ).toBe(1);
  });
});

describe("POST /login fails closed when the throttle is unavailable", () => {
  it("has no in-memory fallback in production", () => {
    expect(getDefaultLoginThrottle({ ODP_DEPLOY_ENV: "production" })).toBeNull();
    expect(
      getDefaultLoginThrottle({
        ODP_DEPLOY_ENV: "production",
        DATABASE_URL: "postgresql://localhost/identity",
      }),
    ).not.toBeNull();
  });

  it("returns 503 in production when no durable store is configured", async () => {
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

  it("returns 503 rather than logging in when the throttle store is unreachable", async () => {
    process.env.ODP_WEB_SESSION_SECRET = SECRET;
    const unreachable: LoginThrottleStore = {
      async beginAttempt(): Promise<ThrottleDecision> {
        throw new Error("connection refused");
      },
      async escalate() {},
      async clearAttempts() {},
      async releaseAttempt() {},
      async readAttempt() {
        return null;
      },
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
});
