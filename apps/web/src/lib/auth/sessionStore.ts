/**
 * Server-side session store — the only authoritative source for web sessions.
 *
 * The browser cookie contains a sealed session id reference only.  Credentials
 * and the subject used to build the API request live here, so revocation is
 * observed by every request and never depends on claims supplied by a browser.
 */

export const DEFAULT_SESSION_IDLE_TIMEOUT_MS = 30 * 60 * 1000;
export const MAX_SESSION_ABSOLUTE_LIFETIME_MS = 8 * 60 * 60 * 1000;

export type SessionProvider = "local_password" | "oidc";

export interface SessionRecord {
  sessionId: string;
  accountId: string;
  provider: SessionProvider;
  /** API bearer retained on the server, never in the browser cookie. */
  accessToken: string;
  /** Display subject; authorization still uses accountId and the API boundary. */
  subject: string;
  tenantId?: string;
  createdAt: Date;
  lastSeenAt: Date;
  idleExpiresAt: Date;
  absoluteExpiresAt: Date;
  revokedAt: Date | null;
  revokedReason: string | null;
  rotatedFrom?: string | null;
}

export interface LegacyOidcAccount {
  accountId: string;
  subject: string;
  tenantId?: string;
}

export interface SessionStore {
  /** Create a new session record in identity.sessions. */
  createSession(params: {
    sessionId: string;
    accountId: string;
    provider: SessionProvider;
    accessToken: string;
    subject?: string;
    tenantId?: string;
    idleTimeoutMs: number;
    absoluteLifetimeMs: number;
    rotatedFrom?: string;
  }): Promise<SessionRecord>;

  /** Validate a session is still active. Returns null if revoked/expired/not found. */
  validateSession(sessionId: string): Promise<SessionRecord | null>;

  /** Touch session and slide idle expiry without exceeding absolute expiry. */
  touchSession(sessionId: string, idleTimeoutMs: number): Promise<void>;

  /** Revoke a single session. */
  revokeSession(sessionId: string, reason: string): Promise<void>;

  /** Revoke all sessions for an account, optionally excluding one. */
  revokeAllForAccount(
    accountId: string,
    reason: string,
    exceptSessionId?: string,
  ): Promise<number>;

  /** Resolve a legacy OIDC subject to an already linked identity account. */
  resolveOidcAccount?(
    subject: string,
    issuer?: string,
  ): Promise<LegacyOidcAccount | null>;
}

function boundedIdleTimeout(idleTimeoutMs: number): number {
  return Math.min(
    Math.max(idleTimeoutMs, 15 * 60 * 1000),
    60 * 60 * 1000,
  );
}

function boundedAbsoluteLifetime(absoluteLifetimeMs: number): number {
  return Math.min(Math.max(absoluteLifetimeMs, 1), MAX_SESSION_ABSOLUTE_LIFETIME_MS);
}

export class PostgresSessionStore implements SessionStore {
  private _pool: any = null;

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
    provider: SessionProvider;
    accessToken: string;
    subject?: string;
    tenantId?: string;
    idleTimeoutMs: number;
    absoluteLifetimeMs: number;
    rotatedFrom?: string;
  }): Promise<SessionRecord> {
    if (!params.accessToken) throw new Error("Session access token is required");
    const pool = await this.pool();
    const now = new Date();
    const idleExpiresAt = new Date(
      now.getTime() + boundedIdleTimeout(params.idleTimeoutMs),
    );
    const absoluteExpiresAt = new Date(
      now.getTime() + boundedAbsoluteLifetime(params.absoluteLifetimeMs),
    );
    await pool.query(
      `INSERT INTO identity.sessions
         (session_id, account_id, provider, access_token, session_subject, tenant_id,
          created_at, last_seen_at, idle_expires_at, absolute_expires_at, rotated_from)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $7, $8, $9, $10)`,
      [
        params.sessionId,
        params.accountId,
        params.provider,
        params.accessToken,
        params.subject || params.accountId,
        params.tenantId || null,
        now,
        idleExpiresAt,
        absoluteExpiresAt,
        params.rotatedFrom || null,
      ],
    );
    return {
      sessionId: params.sessionId,
      accountId: params.accountId,
      provider: params.provider,
      accessToken: params.accessToken,
      subject: params.subject || params.accountId,
      tenantId: params.tenantId,
      createdAt: now,
      lastSeenAt: now,
      idleExpiresAt,
      absoluteExpiresAt,
      revokedAt: null,
      revokedReason: null,
      rotatedFrom: params.rotatedFrom || null,
    };
  }

  async validateSession(sessionId: string): Promise<SessionRecord | null> {
    const pool = await this.pool();
    const result = await pool.query(
      `SELECT session_id, account_id, provider, access_token, session_subject, tenant_id,
              created_at, last_seen_at, idle_expires_at, absolute_expires_at,
              revoked_at, revoked_reason, rotated_from
       FROM identity.sessions
       WHERE session_id = $1
         AND revoked_at IS NULL
         AND access_token IS NOT NULL
         AND access_token <> ''
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
      accessToken: row.access_token,
      subject: row.session_subject || row.account_id,
      tenantId: row.tenant_id || undefined,
      createdAt: row.created_at,
      lastSeenAt: row.last_seen_at,
      idleExpiresAt: row.idle_expires_at,
      absoluteExpiresAt: row.absolute_expires_at,
      revokedAt: row.revoked_at,
      revokedReason: row.revoked_reason,
      rotatedFrom: row.rotated_from,
    };
  }

  async touchSession(sessionId: string, idleTimeoutMs: number): Promise<void> {
    const pool = await this.pool();
    const now = new Date();
    const idleExpiresAt = new Date(
      now.getTime() + boundedIdleTimeout(idleTimeoutMs),
    );
    await pool.query(
      `UPDATE identity.sessions
       SET last_seen_at = $2,
           idle_expires_at = LEAST($3, absolute_expires_at)
       WHERE session_id = $1 AND revoked_at IS NULL
         AND idle_expires_at > now() AND absolute_expires_at > now()`,
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

  async resolveOidcAccount(
    subject: string,
    issuer?: string,
  ): Promise<LegacyOidcAccount | null> {
    const pool = await this.pool();
    const params: unknown[] = [subject];
    let issuerClause = "";
    if (issuer) {
      issuerClause = " AND f.issuer = $2";
      params.push(issuer);
    }
    const result = await pool.query(
      `SELECT a.account_id, a.username, a.tenant_id
       FROM identity.federated_identities f
       JOIN identity.accounts a ON a.account_id = f.account_id
       WHERE f.subject = $1${issuerClause}
         AND a.status = 'active'
       LIMIT 1`,
      params,
    );
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      accountId: row.account_id,
      subject: row.username || row.account_id,
      tenantId: row.tenant_id || undefined,
    };
  }
}

/** In-memory implementation used only by non-production development/tests. */
export class MockSessionStore implements SessionStore {
  sessions: Map<string, SessionRecord> = new Map();
  oidcAccounts: Map<string, LegacyOidcAccount> = new Map();

  async createSession(params: {
    sessionId: string;
    accountId: string;
    provider: SessionProvider;
    accessToken: string;
    subject?: string;
    tenantId?: string;
    idleTimeoutMs: number;
    absoluteLifetimeMs: number;
    rotatedFrom?: string;
  }): Promise<SessionRecord> {
    if (!params.accessToken) throw new Error("Session access token is required");
    const now = new Date();
    const record: SessionRecord = {
      sessionId: params.sessionId,
      accountId: params.accountId,
      provider: params.provider,
      accessToken: params.accessToken,
      subject: params.subject || params.accountId,
      tenantId: params.tenantId,
      createdAt: now,
      lastSeenAt: now,
      idleExpiresAt: new Date(
        now.getTime() + boundedIdleTimeout(params.idleTimeoutMs),
      ),
      absoluteExpiresAt: new Date(
        now.getTime() + boundedAbsoluteLifetime(params.absoluteLifetimeMs),
      ),
      revokedAt: null,
      revokedReason: null,
      rotatedFrom: params.rotatedFrom || null,
    };
    this.sessions.set(params.sessionId, record);
    return record;
  }

  async validateSession(sessionId: string): Promise<SessionRecord | null> {
    const session = this.sessions.get(sessionId);
    if (!session || session.revokedAt) return null;
    const now = new Date();
    if (now >= session.idleExpiresAt || now >= session.absoluteExpiresAt) return null;
    return session;
  }

  async touchSession(sessionId: string, idleTimeoutMs: number): Promise<void> {
    const session = await this.validateSession(sessionId);
    if (!session) return;
    const now = new Date();
    session.lastSeenAt = now;
    session.idleExpiresAt = new Date(
      Math.min(
        now.getTime() + boundedIdleTimeout(idleTimeoutMs),
        session.absoluteExpiresAt.getTime(),
      ),
    );
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

  async resolveOidcAccount(subject: string): Promise<LegacyOidcAccount | null> {
    return this.oidcAccounts.get(subject) || null;
  }
}

let _defaultStore: SessionStore | null = null;
let _overrideStore: SessionStore | null | undefined;

/** Test/dev hook; production always resolves the Postgres store from env. */
export function setSessionStoreForTests(store: SessionStore | null | undefined): void {
  _overrideStore = store;
}

export function getDefaultSessionStore(
  environment: Record<string, string | undefined> = process.env,
): SessionStore | null {
  if (_overrideStore !== undefined) return _overrideStore;
  const hasDbUrl = Boolean(
    environment.ODP_IDENTITY_DATABASE_URL ||
    environment.ODAY_DATABASE_URL ||
    environment.DATABASE_URL,
  );
  if (hasDbUrl) {
    if (!_defaultStore || !(_defaultStore instanceof PostgresSessionStore)) {
      _defaultStore = new PostgresSessionStore();
    }
    return _defaultStore;
  }

  // Never silently turn a production deployment into an in-memory auth store.
  if (
    environment.NODE_ENV === "production" ||
    environment.ODP_DEPLOY_ENV === "production" ||
    environment.ODP_PRODUCT_MODE === "production"
  ) {
    return null;
  }
  if (!_defaultStore || !(_defaultStore instanceof MockSessionStore)) {
    _defaultStore = new MockSessionStore();
  }
  return _defaultStore;
}
