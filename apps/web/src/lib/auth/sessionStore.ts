/**
 * Server-side session store — bridges cookie sessions with identity.sessions.
 *
 * Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §5
 * - Cookie carries opaque session_id reference
 * - Authoritative session state lives in identity.sessions
 * - Session revocation is immediate via DB update
 */

export interface SessionRecord {
  sessionId: string;
  accountId: string;
  provider: "local_password" | "oidc";
  createdAt: Date;
  lastSeenAt: Date;
  idleExpiresAt: Date;
  absoluteExpiresAt: Date;
  revokedAt: Date | null;
  revokedReason: string | null;
}

export interface SessionStore {
  /** Create a new session record in identity.sessions. */
  createSession(params: {
    sessionId: string;
    accountId: string;
    provider: "local_password" | "oidc";
    idleTimeoutMs: number;
    absoluteLifetimeMs: number;
  }): Promise<SessionRecord>;

  /** Validate a session is still active. Returns null if revoked/expired/not found. */
  validateSession(sessionId: string): Promise<SessionRecord | null>;

  /** Touch session: update last_seen_at and slide idle_expires_at. */
  touchSession(sessionId: string, idleTimeoutMs: number): Promise<void>;

  /** Revoke a single session. */
  revokeSession(sessionId: string, reason: string): Promise<void>;

  /** Revoke all sessions for an account, optionally excluding one. */
  revokeAllForAccount(
    accountId: string,
    reason: string,
    exceptSessionId?: string,
  ): Promise<number>;
}

export class PostgresSessionStore implements SessionStore {
  private _pool: unknown = null;

  private async pool(): Promise<any> {
    if (this._pool) return this._pool;
    const { Pool } = await import("pg");
    const connectionString =
      process.env.ODP_IDENTITY_DATABASE_URL ||
      process.env.ODAY_DATABASE_URL ||
      process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error("No database connection URL configured for session store");
    }
    this._pool = new Pool({
      connectionString,
      max: 5,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
      ssl: connectionString.includes("sslmode=require")
        ? { rejectUnauthorized: false }
        : undefined,
    });
    return this._pool;
  }

  async createSession(params: {
    sessionId: string;
    accountId: string;
    provider: "local_password" | "oidc";
    idleTimeoutMs: number;
    absoluteLifetimeMs: number;
  }): Promise<SessionRecord> {
    const pool = await this.pool();
    const now = new Date();
    const idleExpiresAt = new Date(now.getTime() + params.idleTimeoutMs);
    const absoluteExpiresAt = new Date(now.getTime() + params.absoluteLifetimeMs);
    await pool.query(
      `INSERT INTO identity.sessions
         (session_id, account_id, provider, created_at, last_seen_at, idle_expires_at, absolute_expires_at)
       VALUES ($1, $2, $3, $4, $4, $5, $6)`,
      [params.sessionId, params.accountId, params.provider, now, idleExpiresAt, absoluteExpiresAt],
    );
    return {
      sessionId: params.sessionId,
      accountId: params.accountId,
      provider: params.provider,
      createdAt: now,
      lastSeenAt: now,
      idleExpiresAt,
      absoluteExpiresAt,
      revokedAt: null,
      revokedReason: null,
    };
  }

  async validateSession(sessionId: string): Promise<SessionRecord | null> {
    const pool = await this.pool();
    const result = await pool.query(
      `SELECT session_id, account_id, provider, created_at, last_seen_at,
              idle_expires_at, absolute_expires_at, revoked_at, revoked_reason
       FROM identity.sessions
       WHERE session_id = $1
         AND revoked_at IS NULL
         AND idle_expires_at > now()
         AND absolute_expires_at > now()`,
      [sessionId],
    );
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      sessionId: row.session_id,
      accountId: row.account_id,
      provider: row.provider,
      createdAt: row.created_at,
      lastSeenAt: row.last_seen_at,
      idleExpiresAt: row.idle_expires_at,
      absoluteExpiresAt: row.absolute_expires_at,
      revokedAt: row.revoked_at,
      revokedReason: row.revoked_reason,
    };
  }

  async touchSession(sessionId: string, idleTimeoutMs: number): Promise<void> {
    const pool = await this.pool();
    const now = new Date();
    const idleExpiresAt = new Date(now.getTime() + idleTimeoutMs);
    await pool.query(
      `UPDATE identity.sessions
       SET last_seen_at = $2, idle_expires_at = $3
       WHERE session_id = $1 AND revoked_at IS NULL`,
      [sessionId, now, idleExpiresAt],
    );
  }

  async revokeSession(sessionId: string, reason: string): Promise<void> {
    const pool = await this.pool();
    await pool.query(
      `UPDATE identity.sessions
       SET revoked_at = now(), revoked_reason = $2
       WHERE session_id = $1 AND revoked_at IS NULL`,
      [sessionId, reason],
    );
  }

  async revokeAllForAccount(
    accountId: string,
    reason: string,
    exceptSessionId?: string,
  ): Promise<number> {
    const pool = await this.pool();
    let query = `UPDATE identity.sessions
                 SET revoked_at = now(), revoked_reason = $2
                 WHERE account_id = $1 AND revoked_at IS NULL`;
    const params: unknown[] = [accountId, reason];
    if (exceptSessionId) {
      query += " AND session_id != $3";
      params.push(exceptSessionId);
    }
    const result = await pool.query(query, params);
    return result.rowCount ?? 0;
  }
}

/** In-memory mock for testing. */
export class MockSessionStore implements SessionStore {
  sessions: Map<string, SessionRecord> = new Map();

  async createSession(params: {
    sessionId: string;
    accountId: string;
    provider: "local_password" | "oidc";
    idleTimeoutMs: number;
    absoluteLifetimeMs: number;
  }): Promise<SessionRecord> {
    const now = new Date();
    const record: SessionRecord = {
      sessionId: params.sessionId,
      accountId: params.accountId,
      provider: params.provider,
      createdAt: now,
      lastSeenAt: now,
      idleExpiresAt: new Date(now.getTime() + params.idleTimeoutMs),
      absoluteExpiresAt: new Date(now.getTime() + params.absoluteLifetimeMs),
      revokedAt: null,
      revokedReason: null,
    };
    this.sessions.set(params.sessionId, record);
    return record;
  }

  async validateSession(sessionId: string): Promise<SessionRecord | null> {
    const session = this.sessions.get(sessionId);
    if (!session) return null;
    if (session.revokedAt) return null;
    const now = new Date();
    if (now > session.idleExpiresAt || now > session.absoluteExpiresAt) return null;
    return session;
  }

  async touchSession(sessionId: string, idleTimeoutMs: number): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (session && !session.revokedAt) {
      const now = new Date();
      session.lastSeenAt = now;
      session.idleExpiresAt = new Date(now.getTime() + idleTimeoutMs);
    }
  }

  async revokeSession(sessionId: string, reason: string): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (session && !session.revokedAt) {
      session.revokedAt = new Date();
      session.revokedReason = reason;
    }
  }

  async revokeAllForAccount(
    accountId: string,
    reason: string,
    exceptSessionId?: string,
  ): Promise<number> {
    let count = 0;
    for (const session of this.sessions.values()) {
      if (
        session.accountId === accountId &&
        !session.revokedAt &&
        session.sessionId !== exceptSessionId
      ) {
        session.revokedAt = new Date();
        session.revokedReason = reason;
        count++;
      }
    }
    return count;
  }
}

let _defaultStore: SessionStore | null = null;

export function getDefaultSessionStore(): SessionStore | null {
  const hasDbUrl = Boolean(
    process.env.ODP_IDENTITY_DATABASE_URL ||
    process.env.ODAY_DATABASE_URL ||
    process.env.DATABASE_URL,
  );
  if (!hasDbUrl) return null;
  if (!_defaultStore) {
    _defaultStore = new PostgresSessionStore();
  }
  return _defaultStore;
}
