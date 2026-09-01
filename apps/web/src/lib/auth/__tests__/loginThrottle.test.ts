import { describe, expect, it } from "vitest";
import {
  DEFAULT_LOGIN_THROTTLE_CONFIG as CONFIG,
  LoginThrottle,
  MockLoginThrottleStore,
  accountAttemptKey,
  ipAttemptKey,
  resolveClientIp,
  resolveThrottlePepper,
  type LoginThrottleStore,
} from "../loginThrottle";

const PEPPER = "deployment-scoped-login-throttle-pepper";
const IP = "203.0.113.42";
const T0 = new Date("2026-08-31T12:00:00.000Z");

function at(base: Date, minutes: number): Date {
  return new Date(base.getTime() + minutes * 60 * 1000);
}

function newThrottle(store: LoginThrottleStore = new MockLoginThrottleStore()) {
  return { store, throttle: new LoginThrottle(store, CONFIG, PEPPER) };
}

/** One full failed login as the /login route drives it. */
async function failedLogin(
  throttle: LoginThrottle,
  username: string,
  clientIp: string | null,
  now: Date,
): Promise<boolean> {
  const gate = await throttle.beginAttempt(username, clientIp, now);
  if (!gate.allowed) return false;
  await throttle.recordFailure(username, clientIp, now);
  return true;
}

describe("login throttle attempt keys", () => {
  it("stores no plaintext client IP or username (Contract §2.2)", async () => {
    const ipKey = await ipAttemptKey(IP, PEPPER);
    const accountKey = await accountAttemptKey("admin", PEPPER);

    expect(ipKey).toMatch(/^ip:[0-9a-f]{64}$/);
    expect(ipKey).not.toContain(IP);
    expect(ipKey).not.toContain("203");
    expect(accountKey).toMatch(/^account:[0-9a-f]{64}$/);
    expect(accountKey).not.toContain("admin");
  });

  it("maps equivalent address and username spellings onto one key", async () => {
    expect(await ipAttemptKey("2001:DB8::1", PEPPER)).toBe(
      await ipAttemptKey("2001:db8:0:0:0:0:0:1", PEPPER),
    );
    expect(await ipAttemptKey(" 203.0.113.42 ", PEPPER)).toBe(
      await ipAttemptKey(IP, PEPPER),
    );
    expect(await ipAttemptKey("[2001:db8::1]:443", PEPPER)).toBe(
      await ipAttemptKey("2001:db8::1", PEPPER),
    );
    expect(await accountAttemptKey("  Admin ", PEPPER)).toBe(
      await accountAttemptKey("admin", PEPPER),
    );

    expect(await ipAttemptKey(IP, PEPPER)).not.toBe(
      await ipAttemptKey("203.0.113.43", PEPPER),
    );
    // The dimension is part of the signed message, so the two key spaces
    // cannot collide.
    expect((await ipAttemptKey(IP, PEPPER)).slice(3)).not.toBe(
      (await accountAttemptKey(IP, PEPPER)).slice(8),
    );
  });

  it("changes the digest with the pepper so the IPv4 space is not enumerable", async () => {
    const plain = await ipAttemptKey(IP, null);
    const peppered = await ipAttemptKey(IP, PEPPER);

    expect(peppered).not.toBe(plain);
    expect(peppered).toBe(await ipAttemptKey(IP, PEPPER));
    expect(peppered).not.toBe(await ipAttemptKey(IP, "another-secret"));
  });

  it("derives the pepper without making a new deployment variable mandatory", () => {
    expect(resolveThrottlePepper({ ODP_WEB_SESSION_SECRET: "s".repeat(32) })).toBe(
      "s".repeat(32),
    );
    expect(
      resolveThrottlePepper({
        ODP_WEB_SESSION_SECRET: "s".repeat(32),
        ODP_WEB_LOGIN_THROTTLE_PEPPER: "explicit",
      }),
    ).toBe("explicit");
    expect(resolveThrottlePepper({})).toBeNull();
  });
});

describe("client IP resolution", () => {
  it("uses the platform-appended entry, not one the client supplied", () => {
    const headers = new Headers({
      "x-forwarded-for": "10.0.0.1, 198.51.100.7, 203.0.113.42",
    });
    expect(resolveClientIp(headers, {})).toBe(IP);
  });

  it("skips additional trusted proxy hops when the deployment declares them", () => {
    const headers = new Headers({
      "x-forwarded-for": "203.0.113.42, 198.51.100.7",
    });
    expect(resolveClientIp(headers, { ODP_WEB_TRUSTED_PROXY_HOPS: "2" })).toBe(IP);
  });

  it("falls back to x-real-ip and reports nothing when no address is available", () => {
    expect(resolveClientIp(new Headers({ "x-real-ip": IP }), {})).toBe(IP);
    expect(resolveClientIp(new Headers(), {})).toBeNull();
  });
});

describe("login throttle thresholds (Contract §6.4)", () => {
  it("counts the attempt before any credential verification happens", async () => {
    const { store, throttle } = newThrottle();

    const gate = await throttle.beginAttempt("admin", IP, T0);

    expect(gate.allowed).toBe(true);
    const persisted = await store.readAttempt(await accountAttemptKey("admin", PEPPER));
    expect(persisted?.failureCount).toBe(1);
  });

  it("locks an account after five failures in the window", async () => {
    const { throttle } = newThrottle();

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
      expect(await failedLogin(throttle, "admin", IP, T0)).toBe(true);
    }

    const blocked = await throttle.beginAttempt("admin", IP, T0);
    expect(blocked.allowed).toBe(false);
    expect(blocked.reason).toBe("account_locked");
    expect(blocked.lockedUntil?.getTime()).toBe(T0.getTime() + CONFIG.baseLockoutMs);
  });

  it("allows attempts while the account stays under the threshold", async () => {
    const { throttle } = newThrottle();

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures - 1; attempt += 1) {
      await failedLogin(throttle, "admin", IP, T0);
    }

    expect((await throttle.beginAttempt("admin", IP, T0)).allowed).toBe(true);
  });

  it("refuses on the counter alone when the lockout write never landed", async () => {
    // beginAttempt persists the count; recordFailure only opens the lockout
    // round. A request that dies before recordFailure must still be counted.
    const { throttle } = newThrottle();

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
      expect((await throttle.beginAttempt("admin", IP, T0)).allowed).toBe(true);
    }

    const blocked = await throttle.beginAttempt("admin", IP, T0);
    expect(blocked.allowed).toBe(false);
    expect(blocked.reason).toBe("account_locked");
    expect(blocked.lockedUntil ?? null).toBeNull();
  });

  it("doubles every further lockout round and caps it at 60 minutes", async () => {
    const { throttle } = newThrottle();
    const expectedMinutes = [15, 30, 60, 60];
    let cursor = T0;

    for (const minutes of expectedMinutes) {
      for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
        expect(await failedLogin(throttle, "admin", IP, cursor)).toBe(true);
      }

      const blocked = await throttle.beginAttempt("admin", IP, cursor);
      expect(blocked.allowed).toBe(false);
      expect(blocked.lockedUntil?.getTime()).toBe(
        cursor.getTime() + minutes * 60 * 1000,
      );

      // Resume one minute after the lockout ends, which also expires the
      // 15 minute counting window.
      cursor = at(cursor, minutes + 1);
    }
  });

  it("blocks a source IP after fifty failures without doubling its lockout", async () => {
    const { throttle } = newThrottle();
    let cursor = T0;

    for (const round of [0, 1]) {
      for (let attempt = 0; attempt < CONFIG.ipMaxFailures; attempt += 1) {
        // Distinct usernames keep the account dimension out of the way.
        const allowed = await failedLogin(
          throttle,
          `victim-${round}-${attempt}`,
          IP,
          cursor,
        );
        expect(allowed).toBe(true);
      }

      const blocked = await throttle.beginAttempt("someone", IP, cursor);
      expect(blocked.allowed).toBe(false);
      expect(blocked.reason).toBe("ip_blocked");
      expect(blocked.lockedUntil?.getTime()).toBe(
        cursor.getTime() + CONFIG.baseLockoutMs,
      );

      cursor = at(cursor, 16);
    }
  });

  it("does not let a blocked source IP drive an untouched account towards lockout", async () => {
    const { store, throttle } = newThrottle();

    for (let attempt = 0; attempt < CONFIG.ipMaxFailures; attempt += 1) {
      await failedLogin(throttle, `noise-${attempt}`, IP, T0);
    }

    const blocked = await throttle.beginAttempt("victim", IP, T0);

    expect(blocked.reason).toBe("ip_blocked");
    expect(
      await store.readAttempt(await accountAttemptKey("victim", PEPPER)),
    ).toBeNull();
  });

  it("clears the account counter and returns the IP attempt on success", async () => {
    const { store, throttle } = newThrottle();

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures - 1; attempt += 1) {
      await failedLogin(throttle, "admin", IP, T0);
    }
    await throttle.beginAttempt("admin", IP, T0);
    await throttle.recordSuccess("admin", IP);

    expect(
      await store.readAttempt(await accountAttemptKey("admin", PEPPER)),
    ).toBeNull();
    // A successful login must not spend the source IP failure budget.
    expect((await store.readAttempt(await ipAttemptKey(IP, PEPPER)))?.failureCount).toBe(
      CONFIG.accountMaxFailures - 1,
    );
    expect((await throttle.beginAttempt("admin", IP, T0)).allowed).toBe(true);
  });

  it("resets the counter when the window expires but keeps the backoff round", async () => {
    const { store, throttle } = newThrottle();

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
      await failedLogin(throttle, "admin", IP, T0);
    }

    const later = at(T0, 16);
    expect((await throttle.beginAttempt("admin", IP, later)).allowed).toBe(true);

    const record = await store.readAttempt(await accountAttemptKey("admin", PEPPER));
    expect(record?.failureCount).toBe(1);
    // The escalation survives the window, otherwise "doubling" would never
    // happen: the window is 15 minutes and a lockout runs up to 60.
    expect(record?.lockoutCount).toBe(1);
  });

  it("shares state across instances that use the same store", async () => {
    // Two Cloud Run instances resolve separate service objects over one
    // identity.login_attempts table.
    const store = new MockLoginThrottleStore();
    const instanceA = new LoginThrottle(store, CONFIG, PEPPER);
    const instanceB = new LoginThrottle(store, CONFIG, PEPPER);

    for (let attempt = 0; attempt < CONFIG.accountMaxFailures; attempt += 1) {
      const target = attempt % 2 === 0 ? instanceA : instanceB;
      expect(await failedLogin(target, "admin", IP, T0)).toBe(true);
    }

    expect((await instanceA.beginAttempt("admin", IP, T0)).allowed).toBe(false);
    expect((await instanceB.beginAttempt("admin", IP, T0)).allowed).toBe(false);

    await instanceA.recordSuccess("admin", IP);
    expect((await instanceB.beginAttempt("admin", IP, T0)).allowed).toBe(true);
  });
});
