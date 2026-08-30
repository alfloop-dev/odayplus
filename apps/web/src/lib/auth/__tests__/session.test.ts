import { describe, expect, it } from "vitest";
import {
  readOidcTransaction,
  readWebSession,
  rotateWebSession,
  sealOidcTransaction,
  sealWebSession,
  webSessionCookieOptions,
  type OidcTransaction,
  type WebSession,
} from "../session";

const SECRET = "test-session-secret-with-at-least-32-bytes";

describe("encrypted web session", () => {
  it("round-trips a live session and rejects tampering or expiry", async () => {
    const session: WebSession = {
      kind: "web-session",
      accessToken: "access-token-never-visible-to-the-browser",
      tokenType: "Bearer",
      subject: "user-123",
      sid: "session-uuid-456",
      provider: "local_password",
      issuedAt: 1_000,
      expiresAt: 2_000,
    };
    const sealed = await sealWebSession(session, SECRET);

    await expect(
      readWebSession(sealed, { secret: SECRET, nowSeconds: 1_500 }),
    ).resolves.toEqual(session);
    await expect(
      readWebSession(`${sealed}tampered`, {
        secret: SECRET,
        nowSeconds: 1_500,
      }),
    ).resolves.toBeNull();
    await expect(
      readWebSession(sealed, { secret: SECRET, nowSeconds: 2_000 }),
    ).resolves.toBeNull();
  });

  it("T15: seamlessly upgrades legacy sealed session in-place without logging user out", async () => {
    // Legacy session without sid
    const legacySession = {
      kind: "web-session",
      accessToken: "legacy-access-token",
      tokenType: "Bearer",
      subject: "legacy-user",
      issuedAt: 1_000,
      expiresAt: 2_000,
    };
    const sealed = await sealWebSession(legacySession as unknown as WebSession, SECRET);

    const upgraded = await readWebSession(sealed, {
      secret: SECRET,
      nowSeconds: 1_500,
    });

    expect(upgraded).not.toBeNull();
    expect(upgraded?.subject).toBe("legacy-user");
    expect(upgraded?.accessToken).toBe("legacy-access-token");
    expect(upgraded?.expiresAt).toBe(2_000); // Exact expiresAt not extended
    expect(upgraded?.issuedAt).toBe(1_000);
    expect(upgraded?.provider).toBe("oidc");
    expect(typeof upgraded?.sid).toBe("string");
    expect(upgraded?.sid).toBeTruthy();
  });

  it("T10: ensures cookie options are HttpOnly, Secure, SameSite=Lax, and capped to 8h", () => {
    expect(webSessionCookieOptions.httpOnly).toBe(true);
    expect(webSessionCookieOptions.secure).toBe(true);
    expect(webSessionCookieOptions.sameSite).toBe("lax");
    expect(webSessionCookieOptions.path).toBe("/");
    expect(webSessionCookieOptions.maxAge).toBe(8 * 60 * 60);
  });

  it("rotates a session with a fresh sid and updated timestamps", async () => {
    const original: WebSession = {
      kind: "web-session",
      accessToken: "old-access-token",
      tokenType: "Bearer",
      subject: "user-123",
      sid: "old-sid",
      provider: "local_password",
      issuedAt: 1_000,
      expiresAt: 2_000,
    };

    const rotated = await rotateWebSession(original, {
      newAccessToken: "new-access-token",
      nowSeconds: 1_200,
      ttlSeconds: 800,
    });

    expect(rotated.sid).not.toBe(original.sid);
    expect(rotated.subject).toBe(original.subject);
    expect(rotated.accessToken).toBe("new-access-token");
    expect(rotated.issuedAt).toBe(1_200);
    expect(rotated.expiresAt).toBe(2_000);
    expect(rotated.provider).toBe("local_password");
  });

  it("uses separate encryption purposes for session and OIDC transaction", async () => {
    const transaction: OidcTransaction = {
      kind: "oidc-transaction",
      state: "state",
      nonce: "nonce",
      codeVerifier: "verifier",
      redirectUri: "https://web.example/auth/callback",
      returnTo: "/operator",
      issuedAt: 1_000,
      expiresAt: 2_000,
    };
    const sealed = await sealOidcTransaction(transaction, SECRET);

    await expect(
      readOidcTransaction(sealed, {
        secret: SECRET,
        nowSeconds: 1_500,
      }),
    ).resolves.toEqual(transaction);
    await expect(
      readWebSession(sealed, { secret: SECRET, nowSeconds: 1_500 }),
    ).resolves.toBeNull();
  });
});
