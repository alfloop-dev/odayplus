import { describe, expect, it } from "vitest";
import {
  authenticateLocalCredentials,
  mintLocalJwt,
} from "../localAuth";
import { base64UrlDecode } from "../crypto";
import { MockIdentityStore } from "../identityStore";

describe("local identity authentication", () => {
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
});
