import { describe, expect, it } from "vitest";
import {
  readOidcTransaction,
  readWebSession,
  sealOidcTransaction,
  sealWebSession,
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
      issuedAt: 1_000,
      expiresAt: 2_000,
    };
    const sealed = await sealWebSession(session, SECRET);

    await expect(
      readWebSession(sealed, { secret: SECRET, nowSeconds: 1_500 }),
    ).resolves.toEqual(session);
    await expect(
      readWebSession(`${sealed.slice(0, -1)}x`, {
        secret: SECRET,
        nowSeconds: 1_500,
      }),
    ).resolves.toBeNull();
    await expect(
      readWebSession(sealed, { secret: SECRET, nowSeconds: 2_000 }),
    ).resolves.toBeNull();
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

