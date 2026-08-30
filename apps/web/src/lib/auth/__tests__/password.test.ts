import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "../../../app/auth/password/route";
import {
  readWebSession,
  sealLegacyWebSession,
  sealWebSessionReference,
  webSessionCookieName,
  type WebSession,
} from "../session";
import { validatePasswordPolicy } from "../localAuth";
import { MockIdentityStore, setIdentityStoreForTests } from "../identityStore";
import { MockSessionStore, setSessionStoreForTests } from "../sessionStore";

const SECRET = "test-session-secret-with-at-least-32-bytes";

afterEach(() => {
  setIdentityStoreForTests(undefined);
  setSessionStoreForTests(undefined);
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("password policy & change route", () => {
  it("enforces NIST 800-63B password policy constraints", () => {
    // Too short (< 12 chars)
    expect(validatePasswordPolicy("Short1!")).toMatchObject({
      valid: false,
      code: "AUTH_PASSWORD_POLICY_VIOLATION",
    });

    // Common weak password
    expect(validatePasswordPolicy("password123456")).toMatchObject({
      valid: false,
      code: "AUTH_PASSWORD_POLICY_VIOLATION",
    });

    // Contains username
    expect(validatePasswordPolicy("admin_secret_pass", "admin")).toMatchObject({
      valid: false,
      code: "AUTH_PASSWORD_POLICY_VIOLATION",
    });

    // Contains email prefix
    expect(validatePasswordPolicy("john_doe_pass123", undefined, "john_doe@example.com")).toMatchObject({
      valid: false,
      code: "AUTH_PASSWORD_POLICY_VIOLATION",
    });

    // Valid strong password
    expect(validatePasswordPolicy("Correct-Horse-Battery-Staple-2026!")).toEqual({
      valid: true,
    });
  });

  it("rejects password change when unauthenticated", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const request = new NextRequest("https://ops.oday.plus/auth/password", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        currentPassword: "OldPassword123!",
        newPassword: "NewStrongPassword2026!",
      }),
    });
    request.headers.set("origin", "https://ops.oday.plus");

    const response = await POST(request);
    expect(response.status).toBe(401);
  });

  it("T11: rejects password change with mismatched CSRF origin", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const now = Math.floor(Date.now() / 1000);
    const session: WebSession = {
      kind: "web-session",
      accessToken: "token",
      tokenType: "Bearer",
      subject: "user-1",
      sid: "sid-1",
      issuedAt: now,
      expiresAt: now + 3600,
    };
    const cookie = await sealLegacyWebSession(session, SECRET);
    const request = new NextRequest("https://ops.oday.plus/auth/password", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        currentPassword: "OldPassword123!",
        newPassword: "NewStrongPassword2026!",
      }),
    });
    request.headers.set("origin", "https://attacker.example");
    request.cookies.set(webSessionCookieName, cookie);

    const response = await POST(request);
    expect(response.status).toBe(403);
    const data = await response.json();
    expect(data).toMatchObject({
      error: { code: "CSRF_VERIFICATION_FAILED" },
    });
  });

  it("T10: rotates session and sets fresh HttpOnly session cookie on successful password change", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const now = Math.floor(Date.now() / 1000);
    const originalSession: WebSession = {
      kind: "web-session",
      accessToken: "old-access-token",
      tokenType: "Bearer",
      subject: "user-1",
      sid: "original-sid-1",
      issuedAt: now - 300,
      expiresAt: now + 3300,
    };
    const cookie = await sealLegacyWebSession(originalSession, SECRET);

    const request = new NextRequest("https://ops.oday.plus/auth/password", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        currentPassword: "OldPassword123!",
        newPassword: "NewStrongPassword2026!",
      }),
    });
    request.headers.set("origin", "https://ops.oday.plus");
    request.cookies.set(webSessionCookieName, cookie);

    const response = await POST(request);
    expect(response.status).toBe(200);

    const setCookie = response.headers.get("set-cookie");
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("__Host-oday_web_session=");
    expect(setCookie).toMatch(/httponly/i);
    expect(setCookie).toMatch(/secure/i);
    expect(setCookie).toMatch(/samesite=lax/i);

    const cookieMatch = setCookie?.match(/__Host-oday_web_session=([^;]+)/);
    const cookieValue = cookieMatch?.[1];
    const decrypted = await readWebSession(cookieValue, { secret: SECRET });

    expect(decrypted).not.toBeNull();
    expect(decrypted?.subject).toBe("user-1");
    // SID must be rotated
    expect(decrypted?.sid).not.toBe(originalSession.sid);
    expect(decrypted?.issuedAt).toBeGreaterThanOrEqual(now);
  });

  it("persists the new password and revokes the old and other sessions", async () => {
    vi.stubEnv("ODP_WEB_SESSION_SECRET", SECRET);
    const identityStore = new MockIdentityStore([
      {
        accountId: "account-1",
        tenantId: "tenant-1",
        username: "operator",
        email: "operator@example.com",
        status: "active",
        password: "OldPassword123!",
      },
    ]);
    const sessionStore = new MockSessionStore();
    setIdentityStoreForTests(identityStore);
    setSessionStoreForTests(sessionStore);
    const now = Math.floor(Date.now() / 1000);
    await sessionStore.createSession({
      sessionId: "old-session",
      accountId: "account-1",
      provider: "local_password",
      accessToken: "old-token",
      subject: "operator",
      tenantId: "tenant-1",
      idleTimeoutMs: 30 * 60 * 1000,
      absoluteLifetimeMs: 8 * 60 * 60 * 1000,
    });
    await sessionStore.createSession({
      sessionId: "other-session",
      accountId: "account-1",
      provider: "local_password",
      accessToken: "other-token",
      subject: "operator",
      tenantId: "tenant-1",
      idleTimeoutMs: 30 * 60 * 1000,
      absoluteLifetimeMs: 8 * 60 * 60 * 1000,
    });
    const cookie = await sealWebSessionReference(
      {
        kind: "web-session",
        sid: "old-session",
        provider: "local_password",
        issuedAt: now,
        expiresAt: now + 3600,
      },
      SECRET,
    );
    const request = new NextRequest("https://ops.oday.plus/auth/password", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        currentPassword: "OldPassword123!",
        newPassword: "NewStrongPassword2026!",
      }),
    });
    request.headers.set("origin", "https://ops.oday.plus");
    request.cookies.set(webSessionCookieName, cookie);

    const response = await POST(request);
    expect(response.status).toBe(200);
    await expect(sessionStore.validateSession("old-session")).resolves.toBeNull();
    await expect(sessionStore.validateSession("other-session")).resolves.toBeNull();
    const credential = await identityStore.getPasswordCredential("account-1");
    await expect(
      identityStore.verifyPassword(
        credential?.phcHash || "",
        "NewStrongPassword2026!",
      ),
    ).resolves.toMatchObject({ valid: true });
    expect(sessionStore.sessions.size).toBe(3);
  });
});
