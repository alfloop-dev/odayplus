import { afterEach, describe, expect, it, vi } from "vitest";
import { PostgresIdentityStore } from "../identityStore";
import { PostgresSessionStore } from "../sessionStore";

const accountId = "13faae19-21c6-4663-8e89-b93ea7f1107d";
const sessionParams = {
  sessionId: "e2457742-c92a-4e47-9501-6405714b79ab", accountId,
  provider: "local_password" as const, accessToken: "offline-token",
  idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000,
};

afterEach(() => vi.unstubAllEnvs());

describe("production credential/session SQL adapter", () => {
  it("reads a non-secret revision with the PHC without JS number precision loss", async () => {
    const query = vi.fn().mockResolvedValue({ rows: [{ account_id: accountId,
      phc_hash: "offline-phc", must_change: false, credential_revision: "9007199254740993" }] });
    const store = new PostgresIdentityStore();
    Object.assign(store, { _pool: { query } });
    await expect(store.getPasswordCredential(accountId)).resolves.toMatchObject({ credentialRevision: "9007199254740993" });
    expect(query.mock.calls[0][0]).toContain("credential_revision::text");
  });

  it("CAS rehash returns its own committed revision, never an unrelated later read", async () => {
    const query = vi.fn().mockResolvedValue({ rowCount: 1, rows: [{ credential_revision: "8" }] });
    const store = new PostgresIdentityStore();
    Object.assign(store, { _pool: { query } });
    await expect(store.updatePasswordHash(accountId, "offline-rehash", "7")).resolves.toBe("8");
    expect(query).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("credential_revision = $3::bigint"),
      [accountId, "offline-rehash", "7"]);
    expect(query.mock.calls[0][0]).toContain("RETURNING credential_revision::text");
  });

  it("missing revision never starts a rehash UPDATE; stale revision has no fallback", async () => {
    const query = vi.fn().mockResolvedValue({ rowCount: 0, rows: [] });
    const store = new PostgresIdentityStore();
    Object.assign(store, { _pool: { query } });
    await expect(store.updatePasswordHash(accountId, "offline-rehash")).rejects.toThrow("CREDENTIAL_REVISION_REQUIRED");
    expect(query).not.toHaveBeenCalled();
    await expect(store.updatePasswordHash(accountId, "offline-rehash", "1")).rejects.toThrow("CREDENTIAL_REVISION_CHANGED");
    expect(query).toHaveBeenCalledTimes(1);
  });

  it("password change returns its committed revision and rejects missing/stale verification", async () => {
    const query = vi.fn().mockResolvedValue({ rowCount: 1, rows: [{ credential_revision: "8" }] });
    const store = new PostgresIdentityStore();
    Object.assign(store, { _pool: { query } });
    await expect(store.changePassword(accountId, "offline-new-phc")).rejects.toThrow("CREDENTIAL_REVISION_REQUIRED");
    expect(query).not.toHaveBeenCalled();
    await expect(store.changePassword(accountId, "offline-new-phc", "7")).resolves.toBe("8");
    expect(query).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("RETURNING credential_revision::text"),
      [accountId, "offline-new-phc", "7"]);
    expect(query.mock.calls[0][0]).toContain("credential_revision = $3::bigint");
    query.mockResolvedValueOnce({ rowCount: 0, rows: [] });
    await expect(store.changePassword(accountId, "offline-new-phc", "7")).rejects.toThrow("CREDENTIAL_REVISION_CHANGED");
    expect(query).toHaveBeenCalledTimes(2);
  });

  it("requires verified local-password revision and forwards it to the atomic INSERT fence", async () => {
    const query = vi.fn().mockResolvedValue({ rowCount: 1 });
    const store = new PostgresSessionStore();
    Object.assign(store, { _pool: { query } });
    await expect(store.createSession(sessionParams)).rejects.toThrow("SESSION_CREDENTIAL_REVISION_REQUIRED");
    expect(query).not.toHaveBeenCalled();
    await store.createSession({ ...sessionParams, verifiedCredentialRevision: "3" });
    expect(query.mock.calls[0][0]).toContain("verified_credential_revision");
    expect(query.mock.calls[0][1][10]).toBe("3");
  });
});

// Only the isolated Python PostgreSQL fixture enables this selection. It is NOT
// a live DB option: no test obtains operational credentials or launches recovery.
describe.skipIf(!process.env.ODP_TEST_RECOVERY_DATABASE_URL)("isolated PG production Web stores after recovery", () => {
  it("refuses stale verified insertion and stale rehash; current revision logs in", async () => {
    vi.stubEnv("ODP_IDENTITY_DATABASE_URL", process.env.ODP_TEST_RECOVERY_DATABASE_URL!);
    const identity = new PostgresIdentityStore();
    const sessions = new PostgresSessionStore();
    try {
      const credential = await identity.getPasswordCredential(accountId);
      expect(credential?.credentialRevision).toBe("3");
      expect(await identity.verifyPassword(credential!.phcHash, "Random-new-offline-pair-739!")).toMatchObject({ valid: true });
      await expect(identity.updatePasswordHash(accountId, "never-overwrite-with-old-phc", "2")).rejects.toThrow("CREDENTIAL_REVISION_CHANGED");
      await expect(sessions.createSession({ ...sessionParams, verifiedCredentialRevision: "2" })).rejects.toThrow("SESSION_CREDENTIAL_REVISION_REFUSED");
      expect((await identity.getPasswordCredential(accountId))?.phcHash).toBe(credential!.phcHash);
      const created = await sessions.createSession({ ...sessionParams, verifiedCredentialRevision: "3" });
      expect(await sessions.validateSession(created.sessionId)).not.toBeNull();
      await sessions.revokeSession(created.sessionId, "offline-test-owned-cleanup");
      expect(await sessions.validateSession(created.sessionId)).toBeNull();
      const rehash = await identity.hashPassword("Random-new-offline-pair-739!");
      expect(await identity.updatePasswordHash(accountId, rehash, "3")).toBe("4");
      const current = await identity.getPasswordCredential(accountId);
      expect(current?.credentialRevision).toBe("4");
      expect(await identity.verifyPassword(current!.phcHash, "Random-new-offline-pair-739!")).toMatchObject({ valid: true });
    } finally {
      // Release only pools owned by this test's actual production store objects.
      for (const store of [identity, sessions]) {
        await (store as unknown as { _pool: { end(): Promise<void> } | null })._pool?.end();
      }
    }
  });
});
