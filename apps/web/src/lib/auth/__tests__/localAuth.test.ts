import { afterEach, describe, expect, it, vi } from "vitest";
import {
  authenticateLocalCredentials,
  mintLocalJwt,
} from "../localAuth";
import { base64UrlDecode } from "../crypto";
import { MockIdentityStore } from "../identityStore";

describe("local identity authentication", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("does not reveal a locked account until its password is correct", async () => {
    const store = new MockIdentityStore([
      {
        accountId: "account-locked",
        tenantId: "tenant-1",
        username: "locked-user",
        email: "locked@example.com",
        status: "locked",
        password: "CorrectPassword123!",
      },
    ]);

    await expect(
      authenticateLocalCredentials("locked-user", "wrong-password", {
        identityStore: store,
      }),
    ).resolves.toMatchObject({
      ok: false,
      code: "AUTH_INVALID_CREDENTIALS",
    });
    await expect(
      authenticateLocalCredentials("locked-user", "CorrectPassword123!", {
        identityStore: store,
      }),
    ).resolves.toMatchObject({
      ok: false,
      code: "AUTH_ACCOUNT_LOCKED",
    });
  });

  it("mints a short-lived local token for account_id and session id", async () => {
    const token = await mintLocalJwt({
      subject: "account-1",
      sid: "session-1",
      tenantId: "tenant-1",
      nowSeconds: 1000,
      expiresInSeconds: 9999,
      signingSecret: "test-signing-secret-with-at-least-32-bytes",
    });
    const claims = JSON.parse(
      new TextDecoder().decode(
        base64UrlDecode(token.split(".")[1] || ""),
      ),
    ) as Record<string, unknown>;
    expect(claims.sub).toBe("account-1");
    expect(claims.sid).toBe("session-1");
    expect(claims.tenant_id).toBe("tenant-1");
    expect(claims.exp).toBe(1300);
    expect(claims).not.toHaveProperty("roles");
  });

  it("mints the production Web-to-API local token contract", async () => {
    const signingSecret = "production-local-signing-key-at-least-32-bytes";
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("ODP_IDENTITY_TOKEN_SIGNING_KEY", signingSecret);
    vi.stubEnv("ODP_AUTH_LOCAL_ISSUER", "urn:odp:identity:local");
    vi.stubEnv("ODP_AUTH_AUDIENCES", "https://api.example.run.app");

    const token = await mintLocalJwt({
      subject: "account-1",
      sid: "session-1",
      tenantId: "tenant-1",
      nowSeconds: 1000,
    });
    const [encodedHeader, encodedClaims] = token.split(".");
    const header = JSON.parse(
      new TextDecoder().decode(base64UrlDecode(encodedHeader || "")),
    ) as Record<string, unknown>;
    const claims = JSON.parse(
      new TextDecoder().decode(base64UrlDecode(encodedClaims || "")),
    ) as Record<string, unknown>;

    // This is the exact key id and issuer/audience tuple consumed by the API
    // multi-issuer resolver for a plain ODP_IDENTITY_TOKEN_SIGNING_KEY.
    expect(header).toMatchObject({ alg: "HS256", kid: "local-default" });
    expect(claims).toMatchObject({
      iss: "urn:odp:identity:local",
      aud: "https://api.example.run.app",
      sub: "account-1",
      sid: "session-1",
      tenant_id: "tenant-1",
    });
    expect(claims.roles).toBeUndefined();
  });

  it("fails closed in production when the local signing key is absent", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("ODP_IDENTITY_TOKEN_SIGNING_KEY", "");

    await expect(
      mintLocalJwt({ subject: "account-1", sid: "session-1" }),
    ).rejects.toThrow("ODP_IDENTITY_TOKEN_SIGNING_KEY is required in production");
  });
});
