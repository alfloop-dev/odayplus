/**
 * Identity store — connects to PostgreSQL identity schema for local auth mode.
 *
 * Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §6
 * - Queries identity.accounts + identity.password_credentials
 * - Argon2id verification with rehash-on-verify
 * - Fail-closed: missing DB → AUTH_INVALID_CREDENTIALS (never allow)
 */
import { pgConnectionConfig } from "./pgConnection";

// Public representative PHC, NOT any account credential. Precomputed once with
// the same Argon2id parameters; every refused lookup performs ONE verify, even
// on cold start, rather than an extra per-request hash before verification.
const DUMMY_PASSWORD_PHC = "$argon2id$v=19$m=65536,t=3,p=1$b2ZmbGluZS1kdW1teS1pdiE$uRlzo/4z0+tuRzwm9kFYGcr72lLCU7YnT96TC56B6pk";

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
  /** Revision read WITH the verified PHC; never fetch a newer one after verify. */
  credentialRevision?: string;
}

export interface IdentityStore {
  /** Resolve a tenant-qualified username; omitted selector must be globally unambiguous. */
  findAccountByUsername(username: string, tenantId?: string): Promise<IdentityAccount | null>;
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
  /** CAS rehash against the verified revision; returns the committed revision. */
  updatePasswordHash(accountId: string, newHash: string, expectedRevision?: string): Promise<string | void>;
  /** Hash a new password for storage. */
  hashPassword(password: string): Promise<string>;
  /** CAS password change; return the revision committed with this hash. */
  changePassword(accountId: string, newHash: string, expectedRevision?: string): Promise<string>;
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
      ...pgConnectionConfig(connectionString),
      max: 5,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
    });
    return this._pool;
  }

  private async argon2(): Promise<any> {
    if (this._argon2) return this._argon2;
    this._argon2 = await import("argon2");
    return this._argon2;
  }

  async findAccountByUsername(username: string, tenantId?: string): Promise<IdentityAccount | null> {
    const pool = await this.pool();
    const result = await pool.query(
      `SELECT account_id, tenant_id, username, email, status
       FROM identity.accounts
       WHERE lower(username) = lower($1)
         AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
       LIMIT 2`,
      [username.trim(), tenantId ?? null],
    );
    // Never choose an arbitrary tenant's credential when the browser omitted
    // the selector. It selects an account, never supplies session authority.
    if (result.rows.length !== 1) return null;
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
      `SELECT account_id, phc_hash, must_change, credential_revision::text
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
      credentialRevision: row.credential_revision,
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

  async updatePasswordHash(accountId: string, newHash: string, expectedRevision?: string): Promise<string> {
    if (!expectedRevision || !/^[1-9][0-9]{0,18}$/.test(expectedRevision)) {
      throw new Error("CREDENTIAL_REVISION_REQUIRED");
    }
    const pool = await this.pool();
    const result = await pool.query(
      `UPDATE identity.password_credentials
       SET phc_hash = $2, updated_at = now()
       WHERE account_id = $1 AND credential_revision = $3::bigint
       RETURNING credential_revision::text`,
      [accountId, newHash, expectedRevision],
    );
    if (result.rowCount !== 1) throw new Error("CREDENTIAL_REVISION_CHANGED");
    // The DB revision trigger runs before RETURNING; this is the revision of
    // our successful CAS, not an unrelated later credential read.
    return result.rows[0].credential_revision;
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

  async changePassword(accountId: string, newHash: string, expectedRevision?: string): Promise<string> {
    if (!expectedRevision || !/^[1-9][0-9]{0,18}$/.test(expectedRevision)) {
      throw new Error("CREDENTIAL_REVISION_REQUIRED");
    }
    const pool = await this.pool();
    const client = await pool.connect();
    try {
      // A separate READ COMMITTED revocation statement sees old sessions that
      // committed while CAS waited for their credential SHARE lock. A single
      // data-modifying CTE would retain its earlier snapshot and can miss them.
      await client.query("BEGIN ISOLATION LEVEL READ COMMITTED");
      const result = await client.query(
        `UPDATE identity.password_credentials
         SET phc_hash = $2, must_change = false, last_rotated_at = now(), updated_at = now()
         WHERE account_id = $1 AND credential_revision = $3::bigint
         RETURNING credential_revision::text`,
        [accountId, newHash, expectedRevision],
      );
      if (result.rowCount !== 1) throw new Error("CREDENTIAL_REVISION_CHANGED");
      await client.query(
        `UPDATE identity.sessions SET revoked_at = now(), revoked_reason = $2
         WHERE account_id = $1 AND revoked_at IS NULL`,
        [accountId, "password_change"],
      );
      await client.query("COMMIT");
      // Password and ONLY this account's old-session revocation are atomic.
      // Replacement-session mint/INSERT failure cannot leave old cookies active.
      return result.rows[0].credential_revision;
    } catch (error) {
      try { await client.query("ROLLBACK"); } catch { /* Outcome may be unknown. No retry. */ }
      throw error;
    } finally {
      client.release();
    }
  }

  async dummyVerify(): Promise<void> {
    const argon2 = await this.argon2();
    try {
      await argon2.verify(DUMMY_PASSWORD_PHC, "wrong-password-for-dummy");
    } catch {
      // Expected mismatch
    }
  }
}

/**
 * In-memory mock identity store for testing.
 */
export class MockIdentityStore implements IdentityStore {
  private accounts: Map<string, IdentityAccount & { phcHash: string; mustChange: boolean; credentialRevision: string }> = new Map();

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
      this.accounts.set(e.accountId, {
        accountId: e.accountId,
        tenantId: e.tenantId,
        username: e.username,
        email: e.email,
        status: e.status,
        phcHash: e.password, // For mock, store plaintext and do constant-time compare
        mustChange: e.mustChange ?? false,
        credentialRevision: "1",
      });
    }
  }

  async findAccountByUsername(username: string, tenantId?: string): Promise<IdentityAccount | null> {
    const matches = [...this.accounts.values()].filter(entry =>
      entry.username.toLowerCase() === username.trim().toLowerCase() &&
      (tenantId === undefined || entry.tenantId === tenantId));
    if (matches.length !== 1) return null;
    const entry = matches[0];
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
          credentialRevision: entry.credentialRevision,
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

  async updatePasswordHash(accountId: string, newHash: string, expectedRevision?: string): Promise<string> {
    for (const [key, entry] of this.accounts.entries()) {
      if (entry.accountId === accountId) {
        if (entry.credentialRevision !== expectedRevision) throw new Error("CREDENTIAL_REVISION_CHANGED");
        const credentialRevision = entry.phcHash === newHash ? entry.credentialRevision : String(BigInt(entry.credentialRevision) + 1n);
        this.accounts.set(key, { ...entry, phcHash: newHash, credentialRevision });
        return credentialRevision;
      }
    }
    throw new Error("CREDENTIAL_REVISION_CHANGED");
  }

  async hashPassword(password: string): Promise<string> {
    return password.normalize("NFKC"); // Mock: store plaintext
  }

  async changePassword(accountId: string, newHash: string, expectedRevision?: string): Promise<string> {
    for (const [key, entry] of this.accounts.entries()) {
      if (entry.accountId === accountId) {
        if (expectedRevision !== undefined && entry.credentialRevision !== expectedRevision) {
          throw new Error("CREDENTIAL_REVISION_CHANGED");
        }
        const credentialRevision = entry.phcHash !== newHash || entry.mustChange
          ? String(BigInt(entry.credentialRevision) + 1n) : entry.credentialRevision;
        this.accounts.set(key, { ...entry, phcHash: newHash, mustChange: false, credentialRevision });
        return credentialRevision;
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
