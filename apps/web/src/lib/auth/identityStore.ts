/**
 * Identity store — connects to PostgreSQL identity schema for local auth mode.
 *
 * Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §6
 * - Queries identity.accounts + identity.password_credentials
 * - Argon2id verification with rehash-on-verify
 * - Fail-closed: missing DB → AUTH_INVALID_CREDENTIALS (never allow)
 */

export interface IdentityAccount {
  accountId: string;
  tenantId: string;
  username: string;
  email: string;
  status: "invited" | "active" | "disabled" | "locked";
}

export interface PasswordCredential {
  accountId: string;
  phcHash: string;
  mustChange: boolean;
}

export interface IdentityStore {
  /** Find an active account by username (case-insensitive, within any tenant). */
  findAccountByUsername(username: string): Promise<IdentityAccount | null>;
  /** Resolve a session account without trusting the browser subject. */
  findAccountById?(accountId: string): Promise<IdentityAccount | null>;
  /** Resolve an OIDC identity only when it is explicitly linked. */
  findAccountByFederatedIdentity?(
    issuer: string,
    subject: string,
  ): Promise<IdentityAccount | null>;
  /** Fetch password credential for an account. */
  getPasswordCredential(accountId: string): Promise<PasswordCredential | null>;
  /** Verify password against stored Argon2id hash. Returns { valid, newHash? }. */
  verifyPassword(phcHash: string, password: string): Promise<{ valid: boolean; newHash: string | null }>;
  /** Update the stored password hash (for rehash-on-verify). */
  updatePasswordHash(accountId: string, newHash: string): Promise<void>;
  /** Hash a new password for storage. */
  hashPassword(password: string): Promise<string>;
  /** Update password credential with new hash. */
  changePassword(accountId: string, newHash: string): Promise<void>;
  /** Perform dummy verification to prevent timing-based account enumeration. */
  dummyVerify(): Promise<void>;
}

/**
 * PostgreSQL-backed identity store.
 * Connects to the identity schema using DATABASE_URL or ODP_IDENTITY_DATABASE_URL.
 */
export class PostgresIdentityStore implements IdentityStore {
  private _pool: any = null;
  private _argon2: any = null;

  private async pool(): Promise<any> {
    if (this._pool) return this._pool;
    const { Pool } = await import("pg");
    const connectionString =
      process.env.ODP_IDENTITY_DATABASE_URL ||
      process.env.ODAY_DATABASE_URL ||
      process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error("No database connection URL configured for identity store");
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

  private async argon2(): Promise<any> {
    if (this._argon2) return this._argon2;
    this._argon2 = await import("argon2");
    return this._argon2;
  }

  async findAccountByUsername(username: string): Promise<IdentityAccount | null> {
    const pool = await this.pool();
    const result = await pool.query(
      `SELECT account_id, tenant_id, username, email, status
       FROM identity.accounts
       WHERE lower(username) = lower($1)
       LIMIT 1`,
      [username.trim()],
    );
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      accountId: row.account_id,
      tenantId: row.tenant_id,
      username: row.username,
      email: row.email,
      status: row.status,
    };
  }

  async findAccountById(accountId: string): Promise<IdentityAccount | null> {
    const pool = await this.pool();
    const result = await pool.query(
      `SELECT account_id, tenant_id, username, email, status
       FROM identity.accounts
       WHERE account_id = $1
       LIMIT 1`,
      [accountId],
    );
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      accountId: row.account_id,
      tenantId: row.tenant_id,
      username: row.username,
      email: row.email,
      status: row.status,
    };
  }

  async findAccountByFederatedIdentity(
    issuer: string,
    subject: string,
  ): Promise<IdentityAccount | null> {
    const pool = await this.pool();
    const result = await pool.query(
      `SELECT a.account_id, a.tenant_id, a.username, a.email, a.status
       FROM identity.federated_identities f
       JOIN identity.accounts a ON a.account_id = f.account_id
       WHERE f.issuer = $1 AND f.subject = $2
       LIMIT 1`,
      [issuer, subject],
    );
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      accountId: row.account_id,
      tenantId: row.tenant_id,
      username: row.username,
      email: row.email,
      status: row.status,
    };
  }

  async getPasswordCredential(accountId: string): Promise<PasswordCredential | null> {
    const pool = await this.pool();
    const result = await pool.query(
      `SELECT account_id, phc_hash, must_change
       FROM identity.password_credentials
       WHERE account_id = $1`,
      [accountId],
    );
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      accountId: row.account_id,
      phcHash: row.phc_hash,
      mustChange: row.must_change,
    };
  }

  async verifyPassword(
    phcHash: string,
    password: string,
  ): Promise<{ valid: boolean; newHash: string | null }> {
    const argon2 = await this.argon2();
    try {
      const valid = await argon2.verify(phcHash, password);
      if (!valid) return { valid: false, newHash: null };
      const needsRehash = argon2.needsRehash(phcHash, {
        memoryCost: 65536,
        timeCost: 3,
        parallelism: 1,
      });
      if (needsRehash) {
        const newHash = await argon2.hash(password, {
          type: argon2.argon2id,
          memoryCost: 65536,
          timeCost: 3,
          parallelism: 1,
          hashLength: 32,
          saltLength: 16,
        });
        return { valid: true, newHash };
      }
      return { valid: true, newHash: null };
    } catch {
      return { valid: false, newHash: null };
    }
  }

  async updatePasswordHash(accountId: string, newHash: string): Promise<void> {
    const pool = await this.pool();
    await pool.query(
      `UPDATE identity.password_credentials
       SET phc_hash = $2, updated_at = now()
       WHERE account_id = $1`,
      [accountId, newHash],
    );
  }

  async hashPassword(password: string): Promise<string> {
    const argon2 = await this.argon2();
    return argon2.hash(password.normalize("NFKC"), {
      type: argon2.argon2id,
      memoryCost: 65536,
      timeCost: 3,
      parallelism: 1,
      hashLength: 32,
      saltLength: 16,
    });
  }

  async changePassword(accountId: string, newHash: string): Promise<void> {
    const pool = await this.pool();
    await pool.query(
      `UPDATE identity.password_credentials
       SET phc_hash = $2, must_change = false, last_rotated_at = now(), updated_at = now()
       WHERE account_id = $1`,
      [accountId, newHash],
    );
  }

  async dummyVerify(): Promise<void> {
    const argon2 = await this.argon2();
    // Pre-computed dummy hash for timing equalization
    const dummyHash = await argon2.hash("dummy-password-for-timing-safety", {
      type: argon2.argon2id,
      memoryCost: 65536,
      timeCost: 3,
      parallelism: 1,
    });
    try {
      await argon2.verify(dummyHash, "wrong-password-for-dummy");
    } catch {
      // Expected mismatch
    }
  }
}

/**
 * In-memory mock identity store for testing.
 */
export class MockIdentityStore implements IdentityStore {
  private accounts: Map<string, IdentityAccount & { phcHash: string; mustChange: boolean }> = new Map();

  constructor(
    entries?: Array<{
      accountId: string;
      tenantId: string;
      username: string;
      email: string;
      status: IdentityAccount["status"];
      password: string;
      mustChange?: boolean;
    }>,
  ) {
    for (const e of entries ?? []) {
      this.accounts.set(e.username.toLowerCase(), {
        accountId: e.accountId,
        tenantId: e.tenantId,
        username: e.username,
        email: e.email,
        status: e.status,
        phcHash: e.password, // For mock, store plaintext and do constant-time compare
        mustChange: e.mustChange ?? false,
      });
    }
  }

  async findAccountByUsername(username: string): Promise<IdentityAccount | null> {
    const entry = this.accounts.get(username.trim().toLowerCase());
    if (!entry) return null;
    return {
      accountId: entry.accountId,
      tenantId: entry.tenantId,
      username: entry.username,
      email: entry.email,
      status: entry.status,
    };
  }

  async findAccountById(accountId: string): Promise<IdentityAccount | null> {
    for (const entry of this.accounts.values()) {
      if (entry.accountId === accountId) {
        return {
          accountId: entry.accountId,
          tenantId: entry.tenantId,
          username: entry.username,
          email: entry.email,
          status: entry.status,
        };
      }
    }
    return null;
  }

  async findAccountByFederatedIdentity(
    _issuer: string,
    _subject: string,
  ): Promise<IdentityAccount | null> {
    // The mock has no separate federated identity table. Tests that exercise
    // callback linking can provide a store implementation with this method.
    return null;
  }

  async getPasswordCredential(accountId: string): Promise<PasswordCredential | null> {
    for (const entry of this.accounts.values()) {
      if (entry.accountId === accountId) {
        return {
          accountId: entry.accountId,
          phcHash: entry.phcHash,
          mustChange: entry.mustChange,
        };
      }
    }
    return null;
  }

  async verifyPassword(
    phcHash: string,
    password: string,
  ): Promise<{ valid: boolean; newHash: string | null }> {
    // Mock: do constant-time comparison of plaintext passwords
    const { constantTimeEqual } = await import("./crypto");
    return { valid: constantTimeEqual(phcHash, password), newHash: null };
  }

  async updatePasswordHash(accountId: string, newHash: string): Promise<void> {
    for (const [key, entry] of this.accounts.entries()) {
      if (entry.accountId === accountId) {
        this.accounts.set(key, { ...entry, phcHash: newHash });
        break;
      }
    }
  }

  async hashPassword(password: string): Promise<string> {
    return password.normalize("NFKC"); // Mock: store plaintext
  }

  async changePassword(accountId: string, newHash: string): Promise<void> {
    for (const [key, entry] of this.accounts.entries()) {
      if (entry.accountId === accountId) {
        this.accounts.set(key, { ...entry, phcHash: newHash, mustChange: false });
        return;
      }
    }
    throw new Error("Account not found");
  }

  async dummyVerify(): Promise<void> {
    // Mock: just do a SHA-256 digest for timing
    const dummyInput = new TextEncoder().encode("dummy-account-timing-equalization");
    await crypto.subtle.digest("SHA-256", dummyInput);
  }
}

let _defaultStore: IdentityStore | null = null;
let _overrideStore: IdentityStore | null | undefined;

/** Test hook; production callers always use the configured PostgreSQL store. */
export function setIdentityStoreForTests(store: IdentityStore | null | undefined): void {
  _overrideStore = store;
}

/**
 * Get or create the default identity store.
 * In production: PostgresIdentityStore connected to the database.
 * In dev without DB: returns null (caller falls back to dev accounts).
 */
export function getDefaultIdentityStore(): IdentityStore | null {
  if (_overrideStore !== undefined) return _overrideStore;
  const hasDbUrl = Boolean(
    process.env.ODP_IDENTITY_DATABASE_URL ||
    process.env.ODAY_DATABASE_URL ||
    process.env.DATABASE_URL,
  );
  if (!hasDbUrl) return null;
  if (!_defaultStore) {
    _defaultStore = new PostgresIdentityStore();
  }
  return _defaultStore;
}
