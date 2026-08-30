import { describe, expect, it } from "vitest";
import { openJson } from "../crypto";
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
      provider: "local_password",
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
        provider: "local_password",
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
