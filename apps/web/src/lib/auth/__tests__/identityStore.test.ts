import { randomUUID } from "node:crypto";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PostgresIdentityStore, setIdentityStoreForTests } from "../identityStore";
import { sealWebSessionReference, readWebSession, webSessionCookieName } from "../session";
import { PostgresSessionStore, setSessionStoreForTests } from "../sessionStore";

const accountId = "13faae19-21c6-4663-8e89-b93ea7f1107d";
const sessionParams = {
  sessionId: "e2457742-c92a-4e47-9501-6405714b79ab", accountId,
  provider: "local_password" as const, accessToken: "offline-token",
  idleTimeoutMs: 1800000, absoluteLifetimeMs: 3600000,
};

afterEach(() => {
  setIdentityStoreForTests(undefined);
  setSessionStoreForTests(undefined);
  vi.unstubAllEnvs();
});

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
    const release = vi.fn();
    const connect = vi.fn().mockResolvedValue({ query, release });
    Object.assign(store, { _pool: { connect } });
    await expect(store.changePassword(accountId, "offline-new-phc")).rejects.toThrow("CREDENTIAL_REVISION_REQUIRED");
    expect(connect).not.toHaveBeenCalled();
    await expect(store.changePassword(accountId, "offline-new-phc", "7")).resolves.toBe("8");
    expect(query.mock.calls[0][0]).toBe("BEGIN ISOLATION LEVEL READ COMMITTED");
    expect(query.mock.calls[1]).toEqual([expect.stringContaining("RETURNING credential_revision::text"),
      [accountId, "offline-new-phc", "7"]]);
    expect(query.mock.calls[1][0]).toContain("credential_revision = $3::bigint");
    expect(query.mock.calls[2]).toEqual([expect.stringContaining("UPDATE identity.sessions"), [accountId, "password_change"]]);
    expect(query.mock.calls[3][0]).toBe("COMMIT");
    expect(release).toHaveBeenCalledTimes(1);
    query.mockResolvedValue({ rowCount: 0, rows: [] });
    await expect(store.changePassword(accountId, "offline-new-phc", "7")).rejects.toThrow("CREDENTIAL_REVISION_CHANGED");
    expect(query.mock.calls.at(-1)![0]).toBe("ROLLBACK");
    expect(release).toHaveBeenCalledTimes(2);
  });

  it("does not commit a password if old-session revocation fails", async () => {
    const query = vi.fn(async (sql: string) => {
      if (sql.startsWith("UPDATE identity.sessions")) throw new Error("offline-revocation-fault");
      return { rowCount: 1, rows: [{ credential_revision: "8" }] };
    });
    const release = vi.fn();
    const store = new PostgresIdentityStore();
    Object.assign(store, { _pool: { connect: vi.fn().mockResolvedValue({ query, release }) } });
    await expect(store.changePassword(accountId, "offline-new-phc", "7")).rejects.toThrow("offline-revocation-fault");
    expect(query.mock.calls.map(([sql]) => sql)).not.toContain("COMMIT");
    expect(query.mock.calls.at(-1)![0]).toBe("ROLLBACK");
    expect(release).toHaveBeenCalledTimes(1);
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

  it("mounted lost replacement INSERT reply leaves neither old nor newly owned session active; SQL failure rolls back both", async () => {
    const secret = "offline-test-session-secret-at-least-32-bytes";
    vi.stubEnv("ODP_IDENTITY_DATABASE_URL", process.env.ODP_TEST_RECOVERY_DATABASE_URL!);
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("ODP_PRODUCT_MODE", "production");
    vi.stubEnv("ODP_WEB_SESSION_SECRET", secret);
    vi.stubEnv("ODP_IDENTITY_TOKEN_SIGNING_KEY", secret);
    vi.stubEnv("ODP_AUTH_LOCAL_AUDIENCES", "urn:odp:api:local");
    vi.stubEnv("ODP_WEB_BASE_URL", "https://ops.oday.plus");
    const identity = new PostgresIdentityStore();
    const sessions = new PostgresSessionStore();
    setIdentityStoreForTests(identity);
    setSessionStoreForTests(sessions);
    let rollbackSessionId: string | undefined;
    let triggerInstalled = false;
    try {
      const credential = await identity.getPasswordCredential(accountId);
      expect(credential?.credentialRevision).toBe("4");
      const old = await sessions.createSession({ ...sessionParams, sessionId: randomUUID(),
        subject: "odp-dev-smoke", verifiedCredentialRevision: "4" });
      const seconds = Math.floor(Date.now() / 1000);
      const cookie = await sealWebSessionReference({ kind: "web-session", sid: old.sessionId,
        provider: "local_password", issuedAt: seconds, expiresAt: seconds + 3600 }, secret);
      expect(await readWebSession(cookie)).not.toBeNull();
      const realCreate = sessions.createSession.bind(sessions);
      let replacementId: string | undefined;
      const insert = vi.spyOn(sessions, "createSession").mockImplementationOnce(async (params) => {
        await realCreate(params); // Actual durable PostgreSQL INSERT, then lose reply.
        replacementId = params.sessionId;
        throw new Error("offline-lost-insert-reply");
      });
      const cleanup = vi.spyOn(sessions, "revokeSession");
      const request = new NextRequest("https://ops.oday.plus/auth/password", { method: "POST",
        body: JSON.stringify({ currentPassword: "Random-new-offline-pair-739!", newPassword: "Next-safe-offline-pair-8432!" }) });
      request.headers.set("origin", "https://ops.oday.plus");
      request.headers.set("content-type", "application/json");
      request.cookies.set(webSessionCookieName, cookie);
      const { POST } = await import("../../../app/auth/password/route");
      const response = await POST(request);
      expect(response.status).toBe(503);
      expect(response.headers.get("set-cookie")).toBeNull();
      expect(insert).toHaveBeenCalledTimes(1);
      expect(cleanup).toHaveBeenCalledExactlyOnceWith(replacementId, "password_change_failed");
      expect(await readWebSession(cookie)).toBeNull();
      expect(await sessions.validateSession(replacementId!)).toBeNull();
      insert.mockRestore();
      cleanup.mockRestore();
      const committed = await identity.getPasswordCredential(accountId);
      expect(committed?.credentialRevision).toBe("5");
      expect(await identity.verifyPassword(committed!.phcHash, "Next-safe-offline-pair-8432!")).toMatchObject({ valid: true });

      // Actual SQL failure between password CAS and old-row revocation: both
      // password/revision and session must remain unchanged after ROLLBACK.
      const retained = await sessions.createSession({ ...sessionParams, sessionId: randomUUID(), verifiedCredentialRevision: "5" });
      rollbackSessionId = retained.sessionId;
      const pool = (identity as any)._pool;
      await pool.query(`CREATE FUNCTION identity.offline_fail_password_revocation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'offline-session-revocation-fault'; END $$`);
      await pool.query(`CREATE TRIGGER offline_fail_password_revocation BEFORE UPDATE ON identity.sessions
        FOR EACH ROW EXECUTE FUNCTION identity.offline_fail_password_revocation()`);
      triggerInstalled = true;
      await expect(identity.changePassword(accountId, "offline-uncommittable-phc", "5")).rejects.toThrow("offline-session-revocation-fault");
      expect(await identity.getPasswordCredential(accountId)).toEqual(committed);
      expect(await sessions.validateSession(rollbackSessionId)).not.toBeNull();
      await pool.query("DROP TRIGGER offline_fail_password_revocation ON identity.sessions");
      triggerInstalled = false;
      await pool.query("DROP FUNCTION identity.offline_fail_password_revocation()");

      // Actual INSERT takes a credential SHARE lock, then CAS starts and waits.
      // Commit INSERT AFTER CAS's statement snapshot. Revocation must still see
      // this row: a one-statement UPDATE/CTE would miss its late commit.
      const blocker = await pool.connect();
      let pending: Promise<string> | undefined;
      try {
        await blocker.query("BEGIN");
        const writer = new PostgresSessionStore();
        Object.assign(writer, { _pool: { query: blocker.query.bind(blocker) } });
        const late = await writer.createSession({ ...sessionParams, sessionId: randomUUID(), verifiedCredentialRevision: "5" });
        const nextHash = await identity.hashPassword("Last-safe-offline-pair-9543!");
        pending = identity.changePassword(accountId, nextHash, "5");
        const deadline = performance.now() + 5000;
        let waiting = false;
        while (performance.now() < deadline) {
          const rows = await pool.query(`SELECT 1 FROM pg_stat_activity WHERE datname = current_database()
            AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'
            AND query LIKE 'UPDATE identity.password_credentials%'`);
          if (rows.rowCount > 0) { waiting = true; break; }
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
        expect(waiting).toBe(true); // Real PG lock witness, not a sleep-only proof.
        await blocker.query("COMMIT");
        expect(await pending).toBe("6");
        expect(await sessions.validateSession(late.sessionId)).toBeNull();
        expect(await sessions.validateSession(rollbackSessionId)).toBeNull();
      } finally {
        await blocker.query("ROLLBACK");
        blocker.release();
        await pending?.catch(() => undefined);
      }
    } finally {
      const pool = (identity as any)._pool;
      if (triggerInstalled) await pool.query("DROP TRIGGER offline_fail_password_revocation ON identity.sessions");
      if (pool) await pool.query("DROP FUNCTION IF EXISTS identity.offline_fail_password_revocation()");
      if (rollbackSessionId) await sessions.revokeSession(rollbackSessionId, "offline-test-owned-cleanup");
      for (const store of [identity, sessions]) await (store as any)._pool?.end();
    }
  }, 30_000);
});
