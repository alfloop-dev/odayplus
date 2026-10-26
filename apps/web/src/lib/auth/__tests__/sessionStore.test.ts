import { describe, expect, it } from "vitest";
import { openJson } from "../crypto";
import { localJwtExpiresAt, mintLocalJwt } from "../localAuth";
import {
  readWebSession,
  sealLegacyWebSession,
  sealWebSessionReference,
} from "../session";
import { MockSessionStore } from "../sessionStore";

const SECRET = "test-session-secret-with-at-least-32-bytes";

describe("authoritative server-side web sessions", () => {
  it("resolves bearer material from identity.sessions and observes revocation", async () => {
    const store = new MockSessionStore();
    const now = Math.floor(Date.now() / 1000);
    await store.createSession({
      sessionId: "session-authoritative",
      accountId: "account-1",
      provider: "oidc",
      accessToken: "server-only-bearer",
      subject: "operator",
      tenantId: "tenant-1",
      idleTimeoutMs: 30 * 60 * 1000,
      absoluteLifetimeMs: 8 * 60 * 60 * 1000,
    });
    const cookie = await sealWebSessionReference(
      {
        kind: "web-session",
        sid: "session-authoritative",
        provider: "oidc",
        issuedAt: now,
        expiresAt: now + 600,
      },
      SECRET,
    );

    await expect(
      readWebSession(cookie, {
        secret: SECRET,
        sessionStore: store,
        environment: { NODE_ENV: "production" },
      }),
    ).resolves.toMatchObject({
      sid: "session-authoritative",
      accountId: "account-1",
      subject: "operator",
      accessToken: "server-only-bearer",
    });

    await store.revokeSession("session-authoritative", "test");
    await expect(
      readWebSession(cookie, {
        secret: SECRET,
        sessionStore: store,
        environment: { NODE_ENV: "production" },
      }),
    ).resolves.toBeNull();
  });

  it("renews an expired local bearer and persists it before returning", async () => {
    const store = new MockSessionStore();
    const now = Math.floor(Date.now() / 1000);
    const signingSecret = "test-local-signing-material-at-least-32-bytes";
    const expiredToken = await mintLocalJwt({
      subject: "account-local",
      sid: "session-local",
      tenantId: "tenant-local",
      nowSeconds: now - 600,
      expiresInSeconds: 300,
      signingSecret,
    });
    await store.createSession({
      sessionId: "session-local",
      accountId: "account-local",
      provider: "local_password",
      accessToken: expiredToken,
      subject: "operator",
      tenantId: "tenant-local",
      idleTimeoutMs: 30 * 60 * 1000,
      absoluteLifetimeMs: 8 * 60 * 60 * 1000,
    });
    const cookie = await sealWebSessionReference(
      {
        kind: "web-session",
        sid: "session-local",
        provider: "local_password",
        issuedAt: now - 60,
        expiresAt: now + 600,
      },
      SECRET,
    );

    const session = await readWebSession(cookie, {
      secret: SECRET,
      nowSeconds: now,
      sessionStore: store,
      environment: {
        NODE_ENV: "production",
        ODP_IDENTITY_TOKEN_SIGNING_KEY: signingSecret,
        ODP_AUTH_LOCAL_ISSUER: "urn:odp:identity:local",
        ODP_AUTH_AUDIENCES: "https://api.example.run.app",
      },
    });

    expect(session).not.toBeNull();
    expect(session?.accessToken).not.toBe(expiredToken);
    expect(localJwtExpiresAt(session?.accessToken || "")).toBeGreaterThan(now);
    await expect(store.validateSession("session-local")).resolves.toMatchObject(
      {
        accessToken: session?.accessToken,
      },
    );
  });

  it("seals only an opaque reference, never the bearer or identity facts", async () => {
    const cookie = await sealWebSessionReference(
      {
        kind: "web-session",
        sid: "session-opaque",
        provider: "local_password",
        issuedAt: 100,
        expiresAt: 200,
      },
      SECRET,
    );
    const payload = await openJson<Record<string, unknown>>(
      cookie,
      "web-session",
      SECRET,
    );
    expect(payload).toMatchObject({
      kind: "web-session",
      sid: "session-opaque",
      provider: "local_password",
    });
    expect(payload).not.toHaveProperty("accessToken");
    expect(payload).not.toHaveProperty("subject");
    expect(payload).not.toHaveProperty("tenantId");
  });

  it("does not accept a legacy bearer payload in a production read without a store", async () => {
    const cookie = await sealLegacyWebSession(
      {
        kind: "web-session",
        accessToken: "legacy-bearer",
        tokenType: "Bearer",
        subject: "operator",
        issuedAt: 100,
        expiresAt: 200,
      },
      SECRET,
    );

    await expect(
      readWebSession(cookie, {
        secret: SECRET,
        nowSeconds: 150,
        environment: { NODE_ENV: "production" },
        sessionStore: null,
      }),
    ).resolves.toBeNull();
  });
});
