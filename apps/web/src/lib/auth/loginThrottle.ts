/**
 * Login throttle — the single brute-force control on the production /login path.
 *
 * Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2.2, §6.4
 * - Per account: 5 failures in 15 minutes → 15 minute lockout, doubling on every
 *   further lockout round, capped at 60 minutes.
 * - Per source IP: 50 failures in 15 minutes → rejected and recorded.
 * - A successful login clears the account counter.
 * - State lives in `identity.login_attempts` so every Cloud Run instance shares
 *   it. An in-process map is never used in production.
 * - `attempt_key` stores a derived account key or a source IP digest and never
 *   a plaintext client IP (§2.2).
 *
 * The exponential factor comes from the number of lockout rounds already
 * served (`lockout_count`), not from the failure count inside one window: the
 * counting window is 15 minutes while a lockout runs up to 60, so binding the
 * factor to the in-window failure count would reset the escalation every time
 * the window expired and "doubling" would never actually happen.
 */
import { isProductionWebRuntime } from "./runtime";

const encoder = new TextEncoder();

export const ACCOUNT_KEY_PREFIX = "account:";
export const IP_KEY_PREFIX = "ip:";

// ───────────────────────────────────────────────────────────────────────────
// Configuration (Contract §6.4)
// ───────────────────────────────────────────────────────────────────────────

export interface LoginThrottleConfig {
  /** Counting window. */
  windowMs: number;
  /** Failures per account before the account dimension locks. */
  accountMaxFailures: number;
  /** Failures per source IP before the IP dimension blocks. */
  ipMaxFailures: number;
  /** First lockout duration. */
  baseLockoutMs: number;
  /** Exponential backoff ceiling. */
  maxLockoutMs: number;
  /**
   * How long the escalation state (`lockout_count`) survives after a lockout
   * ends. The contract does not specify when backoff resets; this is the
   * implementation default that keeps `identity.login_attempts` from growing
   * escalation state forever. The account dimension also has an explicit reset
   * path — a successful login deletes the whole row (§6.4).
   */
  lockoutRetentionMs: number;
}

export const DEFAULT_LOGIN_THROTTLE_CONFIG: LoginThrottleConfig = {
  windowMs: 15 * 60 * 1000,
  accountMaxFailures: 5,
  ipMaxFailures: 50,
  baseLockoutMs: 15 * 60 * 1000,
  maxLockoutMs: 60 * 60 * 1000,
  lockoutRetentionMs: 24 * 60 * 60 * 1000,
};

/** One row of `identity.login_attempts`. */
export interface LoginAttemptRecord {
  attemptKey: string;
  windowStartedAt: Date;
  failureCount: number;
  lockedUntil: Date | null;
  /** Lockout rounds already served; carried across windows for the backoff. */
  lockoutCount: number;
}

export type ThrottleReason = "account_locked" | "ip_blocked";

export interface ThrottleDecision {
  allowed: boolean;
  reason?: ThrottleReason;
  lockedUntil?: Date | null;
}

// ───────────────────────────────────────────────────────────────────────────
// Attempt key derivation
// ───────────────────────────────────────────────────────────────────────────

/**
 * Resolve the digest pepper.
 *
 * The IPv4 space is small enough to enumerate, so an unpeppered SHA-256 of a
 * client IP can be reversed offline. `ODP_WEB_SESSION_SECRET` is already
 * required in every mode and never leaves the server, so it doubles as the
 * pepper unless an explicit one is configured. No new deployment variable
 * becomes mandatory. Rotating the session secret re-keys the table, which
 * clears in-flight lockouts; lockouts last at most an hour, so that is an
 * acceptable consequence of a rare operation.
 *
 * Null means "no pepper is configured", which is a legitimate answer only
 * outside production. `getDefaultLoginThrottle` refuses to build a database
 * -backed throttle on a null pepper in production, so the unpeppered branch of
 * `digest` is reachable only from local and test runtimes.
 */
export function resolveThrottlePepper(
  environment: Record<string, string | undefined> = process.env,
): string | null {
  const explicit = environment.ODP_WEB_LOGIN_THROTTLE_PEPPER?.trim();
  if (explicit) return explicit;
  return environment.ODP_WEB_SESSION_SECRET?.trim() || null;
}

function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function digest(
  dimension: "account" | "ip",
  value: string,
  pepper: string | null,
): Promise<string> {
  // The dimension is inside the signed message, so an account key and an IP
  // key can never collide even if the two normalized values look alike.
  const message = encoder.encode(`odp-login-throttle/v1/${dimension}/${value}`);
  if (pepper) {
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(pepper),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    return toHex(await crypto.subtle.sign("HMAC", key, message));
  }
  return toHex(await crypto.subtle.digest("SHA-256", message));
}

export function normalizeUsername(username: string): string {
  return username.normalize("NFKC").trim().toLowerCase();
}

function ipv4ToHexGroups(text: string): string[] | null {
  const octets = normalizeIpv4(text);
  if (!octets) return null;
  const parts = octets.split(".").map(Number);
  return [
    ((parts[0] << 8) | parts[1]).toString(16),
    ((parts[2] << 8) | parts[3]).toString(16),
  ];
}

function normalizeIpv4(value: string): string | null {
  const parts = value.split(".");
  if (parts.length !== 4) return null;
  const octets: number[] = [];
  for (const part of parts) {
    if (!/^\d{1,3}$/.test(part)) return null;
    const octet = Number(part);
    if (octet > 255) return null;
    octets.push(octet);
  }
  return octets.join(".");
}

function normalizeIpv6(value: string): string | null {
  const lower = value.toLowerCase();
  if (!lower.includes(":")) return null;

  const halves = lower.split("::");
  if (halves.length > 2) return null;

  const parseGroups = (segment: string): string[] | null => {
    if (!segment) return [];
    const parts = segment.split(":");
    const groups: string[] = [];
    for (let index = 0; index < parts.length; index += 1) {
      const part = parts[index];
      if (part.includes(".")) {
        // An embedded IPv4 tail is only legal as the final element.
        if (index !== parts.length - 1) return null;
        const embedded = ipv4ToHexGroups(part);
        if (!embedded) return null;
        groups.push(...embedded);
        continue;
      }
      if (!/^[0-9a-f]{1,4}$/.test(part)) return null;
      groups.push(part);
    }
    return groups;
  };

  const head = parseGroups(halves[0]);
  const tail = halves.length === 2 ? parseGroups(halves[1]) : [];
  if (head === null || tail === null) return null;

  let groups: string[];
  if (halves.length === 2) {
    const zeroFill = 8 - head.length - tail.length;
    if (zeroFill < 1) return null;
    groups = [...head, ...new Array<string>(zeroFill).fill("0"), ...tail];
  } else {
    if (head.length !== 8) return null;
    groups = head;
  }

  // Fully expanded lowercase form, so equivalent spellings share one key.
  return groups.map((group) => group.replace(/^0+(?=.)/, "")).join(":");
}

/**
 * Canonicalize a client address so equivalent spellings (case, IPv6
 * abbreviation, brackets, trailing port) map to a single attempt key.
 * Anything unparseable is lowercased rather than rejected — it is hashed
 * either way and must never reach the database in the clear.
 */
export function normalizeClientIp(value: string): string {
  let candidate = value.trim();

  const bracketed = /^\[([^\]]+)\](?::\d+)?$/.exec(candidate);
  if (bracketed) {
    candidate = bracketed[1];
  } else {
    const ipv4WithPort = /^(\d{1,3}(?:\.\d{1,3}){3}):\d+$/.exec(candidate);
    if (ipv4WithPort) candidate = ipv4WithPort[1];
  }

  return (
    normalizeIpv4(candidate) ?? normalizeIpv6(candidate) ?? candidate.toLowerCase()
  );
}

/**
 * Account dimension key.
 *
 * The key is derived from the submitted username, not from an account id: the
 * gate has to run before the credential is verified, and resolving an account
 * first would mean unknown usernames could not be throttled at all — both a
 * bypass and an enumeration oracle. The digest keeps usernames (and passwords
 * mistyped into the username field) out of `identity.login_attempts`.
 */
export async function accountAttemptKey(
  username: string,
  pepper: string | null = null,
): Promise<string> {
  return `${ACCOUNT_KEY_PREFIX}${await digest("account", normalizeUsername(username), pepper)}`;
}

/** Source IP dimension key (§2.2: the plaintext IP is never stored). */
export async function ipAttemptKey(
  ipAddress: string,
  pepper: string | null = null,
): Promise<string> {
  return `${IP_KEY_PREFIX}${await digest("ip", normalizeClientIp(ipAddress), pepper)}`;
}

/**
 * Pick the client address that the platform appended, not one the caller sent.
 *
 * Cloud Run appends the real peer address to `X-Forwarded-For`, so the last
 * entry is the one a client cannot forge. Deployments that put additional
 * trusted proxies in front declare how many hops to skip with
 * `ODP_WEB_TRUSTED_PROXY_HOPS`.
 */
export function resolveClientIp(
  headers: Headers,
  environment: Record<string, string | undefined> = process.env,
): string | null {
  const forwarded = headers.get("x-forwarded-for");
  if (forwarded) {
    const entries = forwarded
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);
    if (entries.length > 0) {
      const parsed = Number(environment.ODP_WEB_TRUSTED_PROXY_HOPS);
      const hops =
        Number.isFinite(parsed) && parsed >= 1 ? Math.floor(parsed) : 1;
      const index = Math.min(
        Math.max(entries.length - hops, 0),
        entries.length - 1,
      );
      return entries[index];
    }
  }
  const realIp = headers.get("x-real-ip")?.trim();
  return realIp || null;
}

// ───────────────────────────────────────────────────────────────────────────
// State machine (pure; shared by every store implementation)
// ───────────────────────────────────────────────────────────────────────────

function reasonFor(attemptKey: string): ThrottleReason {
  return attemptKey.startsWith(ACCOUNT_KEY_PREFIX)
    ? "account_locked"
    : "ip_blocked";
}

function escalationIsLive(
  record: LoginAttemptRecord,
  now: Date,
  config: LoginThrottleConfig,
): boolean {
  return (
    record.lockoutCount > 0 &&
    record.lockedUntil !== null &&
    now.getTime() - record.lockedUntil.getTime() <= config.lockoutRetentionMs
  );
}

/** Decide whether an attempt may proceed, without mutating anything. */
export function evaluateAttempt(
  record: LoginAttemptRecord | null,
  maxFailures: number,
  now: Date,
  config: LoginThrottleConfig = DEFAULT_LOGIN_THROTTLE_CONFIG,
): ThrottleDecision {
  if (!record) return { allowed: true };

  // The lockout check must come first. Backoff can exceed the counting window
  // (a 60 minute lockout against a 15 minute window), so treating an expired
  // window as a reset before looking at the lockout would end it early.
  if (record.lockedUntil && now < record.lockedUntil) {
    return {
      allowed: false,
      reason: reasonFor(record.attemptKey),
      lockedUntil: record.lockedUntil,
    };
  }

  if (now.getTime() - record.windowStartedAt.getTime() > config.windowMs) {
    return { allowed: true };
  }

  // Over the threshold with the lockout expired (or never applied) still
  // refuses until the counting window rolls over.
  if (record.failureCount >= maxFailures) {
    return { allowed: false, reason: reasonFor(record.attemptKey) };
  }

  return { allowed: true };
}

/** Count one attempt against a key, rolling the window when it has expired. */
export function countAttempt(
  record: LoginAttemptRecord | null,
  attemptKey: string,
  now: Date,
  config: LoginThrottleConfig = DEFAULT_LOGIN_THROTTLE_CONFIG,
): LoginAttemptRecord {
  if (!record) {
    return {
      attemptKey,
      windowStartedAt: now,
      failureCount: 1,
      lockedUntil: null,
      lockoutCount: 0,
    };
  }

  // While a lockout is live the record must not be rebuilt: dropping
  // lockedUntil/lockoutCount would make a 60 minute lockout disappear as soon
  // as the 15 minute window elapsed.
  if (record.lockedUntil && now < record.lockedUntil) {
    return { ...record, failureCount: record.failureCount + 1 };
  }

  if (now.getTime() - record.windowStartedAt.getTime() > config.windowMs) {
    const keepEscalation = escalationIsLive(record, now, config);
    return {
      attemptKey: record.attemptKey,
      windowStartedAt: now,
      failureCount: 1,
      lockedUntil: keepEscalation ? record.lockedUntil : null,
      lockoutCount: keepEscalation ? record.lockoutCount : 0,
    };
  }

  return { ...record, failureCount: record.failureCount + 1 };
}

/**
 * Open a new lockout round once a confirmed failure crosses the threshold.
 * Returns null when nothing changes.
 *
 * Failures during a live lockout do not recompute it — the doubling unit is
 * the lockout round, not the failure, otherwise a burst inside one round would
 * jump straight to the ceiling.
 */
export function escalateLockout(
  record: LoginAttemptRecord,
  maxFailures: number,
  exponential: boolean,
  now: Date,
  config: LoginThrottleConfig = DEFAULT_LOGIN_THROTTLE_CONFIG,
): LoginAttemptRecord | null {
  if (record.failureCount < maxFailures) return null;
  if (record.lockedUntil && now < record.lockedUntil) return null;

  const lockoutCount = record.lockoutCount + 1;
  const lockoutMs = exponential
    ? Math.min(
        config.baseLockoutMs * 2 ** (lockoutCount - 1),
        config.maxLockoutMs,
      )
    : // The IP dimension only has to "reject and record" (§6.4); no doubling.
      config.baseLockoutMs;

  return {
    ...record,
    lockoutCount,
    lockedUntil: new Date(now.getTime() + lockoutMs),
  };
}

// ───────────────────────────────────────────────────────────────────────────
// Stores
// ───────────────────────────────────────────────────────────────────────────

export interface LoginThrottleStore {
  /**
   * Evaluate the key and, when the attempt is allowed, count it — atomically,
   * so concurrent Cloud Run instances cannot lose an increment.
   */
  beginAttempt(params: {
    attemptKey: string;
    maxFailures: number;
    now: Date;
  }): Promise<ThrottleDecision>;

  /** Apply the lockout for a confirmed failure that crossed the threshold. */
  escalate(params: {
    attemptKey: string;
    maxFailures: number;
    exponential: boolean;
    now: Date;
  }): Promise<void>;

  /** Delete a key outright (a successful login clears the account counter). */
  clearAttempts(attemptKey: string): Promise<void>;

  /** Give back one counted attempt that did not turn out to be a failure. */
  releaseAttempt(attemptKey: string): Promise<void>;

  /** Read the persisted row. */
  readAttempt(attemptKey: string): Promise<LoginAttemptRecord | null>;
}

/** `identity.login_attempts` — the shared state for every Cloud Run instance. */
export class PostgresLoginThrottleStore implements LoginThrottleStore {
  private _pool: any = null;

  constructor(private readonly config: LoginThrottleConfig = DEFAULT_LOGIN_THROTTLE_CONFIG) {}

  private async pool(): Promise<any> {
    if (this._pool) return this._pool;
    const { Pool } = await import("pg");
    const connectionString =
      process.env.ODP_IDENTITY_DATABASE_URL ||
      process.env.ODAY_DATABASE_URL ||
      process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error(
        "No database connection URL configured for login throttle store",
      );
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

  private static toRecord(row: any): LoginAttemptRecord {
    return {
      attemptKey: row.attempt_key,
      windowStartedAt: new Date(row.window_started_at),
      failureCount: Number(row.failure_count),
      lockedUntil: row.locked_until ? new Date(row.locked_until) : null,
      lockoutCount: Number(row.lockout_count ?? 0),
    };
  }

  /**
   * Read-modify-write under a row lock. `SELECT ... FOR UPDATE` serializes the
   * instances that touch the same key, so the read state the decision is based
   * on is the state the write lands on.
   */
  private async withLockedRow<T>(
    attemptKey: string,
    now: Date,
    handler: (
      record: LoginAttemptRecord,
      write: (next: LoginAttemptRecord) => Promise<void>,
    ) => Promise<T>,
  ): Promise<T> {
    const pool = await this.pool();
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await client.query(
        `INSERT INTO identity.login_attempts
           (attempt_key, window_started_at, failure_count, locked_until, lockout_count)
         VALUES ($1, $2, 0, NULL, 0)
         ON CONFLICT (attempt_key) DO NOTHING`,
        [attemptKey, now],
      );
      const selected = await client.query(
        `SELECT attempt_key, window_started_at, failure_count, locked_until, lockout_count
           FROM identity.login_attempts
          WHERE attempt_key = $1
          FOR UPDATE`,
        [attemptKey],
      );
      const record = PostgresLoginThrottleStore.toRecord(selected.rows[0]);
      const result = await handler(record, async (next) => {
        await client.query(
          `UPDATE identity.login_attempts
              SET window_started_at = $2,
                  failure_count = $3,
                  locked_until = $4,
                  lockout_count = $5
            WHERE attempt_key = $1`,
          [
            attemptKey,
            next.windowStartedAt,
            next.failureCount,
            next.lockedUntil,
            next.lockoutCount,
          ],
        );
      });
      await client.query("COMMIT");
      return result;
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
  }

  async beginAttempt(params: {
    attemptKey: string;
    maxFailures: number;
    now: Date;
  }): Promise<ThrottleDecision> {
    return this.withLockedRow(
      params.attemptKey,
      params.now,
      async (record, write) => {
        const decision = evaluateAttempt(
          record,
          params.maxFailures,
          params.now,
          this.config,
        );
        if (decision.allowed) {
          await write(
            countAttempt(record, params.attemptKey, params.now, this.config),
          );
        }
        return decision;
      },
    );
  }

  async escalate(params: {
    attemptKey: string;
    maxFailures: number;
    exponential: boolean;
    now: Date;
  }): Promise<void> {
    await this.withLockedRow(
      params.attemptKey,
      params.now,
      async (record, write) => {
        const next = escalateLockout(
          record,
          params.maxFailures,
          params.exponential,
          params.now,
          this.config,
        );
        if (next) await write(next);
      },
    );
  }

  async clearAttempts(attemptKey: string): Promise<void> {
    const pool = await this.pool();
    await pool.query(
      `DELETE FROM identity.login_attempts WHERE attempt_key = $1`,
      [attemptKey],
    );
  }

  async releaseAttempt(attemptKey: string): Promise<void> {
    const pool = await this.pool();
    await pool.query(
      `UPDATE identity.login_attempts
          SET failure_count = GREATEST(failure_count - 1, 0)
        WHERE attempt_key = $1`,
      [attemptKey],
    );
    // A key with nothing left to remember is removed so the table does not
    // accumulate a row per address ever seen. A concurrent increment between
    // the two statements simply leaves the row in place.
    await pool.query(
      `DELETE FROM identity.login_attempts
        WHERE attempt_key = $1 AND failure_count = 0 AND locked_until IS NULL`,
      [attemptKey],
    );
  }

  async readAttempt(attemptKey: string): Promise<LoginAttemptRecord | null> {
    const pool = await this.pool();
    const result = await pool.query(
      `SELECT attempt_key, window_started_at, failure_count, locked_until, lockout_count
         FROM identity.login_attempts
        WHERE attempt_key = $1`,
      [attemptKey],
    );
    if (result.rows.length === 0) return null;
    return PostgresLoginThrottleStore.toRecord(result.rows[0]);
  }
}

/** In-memory store for non-production development and tests only. */
export class MockLoginThrottleStore implements LoginThrottleStore {
  readonly records: Map<string, LoginAttemptRecord> = new Map();

  constructor(private readonly config: LoginThrottleConfig = DEFAULT_LOGIN_THROTTLE_CONFIG) {}

  async beginAttempt(params: {
    attemptKey: string;
    maxFailures: number;
    now: Date;
  }): Promise<ThrottleDecision> {
    const record = this.records.get(params.attemptKey) ?? null;
    const decision = evaluateAttempt(
      record,
      params.maxFailures,
      params.now,
      this.config,
    );
    if (decision.allowed) {
      this.records.set(
        params.attemptKey,
        countAttempt(record, params.attemptKey, params.now, this.config),
      );
    }
    return decision;
  }

  async escalate(params: {
    attemptKey: string;
    maxFailures: number;
    exponential: boolean;
    now: Date;
  }): Promise<void> {
    const record = this.records.get(params.attemptKey);
    if (!record) return;
    const next = escalateLockout(
      record,
      params.maxFailures,
      params.exponential,
      params.now,
      this.config,
    );
    if (next) this.records.set(params.attemptKey, next);
  }

  async clearAttempts(attemptKey: string): Promise<void> {
    this.records.delete(attemptKey);
  }

  async releaseAttempt(attemptKey: string): Promise<void> {
    const record = this.records.get(attemptKey);
    if (!record) return;
    const failureCount = Math.max(record.failureCount - 1, 0);
    if (failureCount === 0 && record.lockedUntil === null) {
      this.records.delete(attemptKey);
      return;
    }
    this.records.set(attemptKey, { ...record, failureCount });
  }

  async readAttempt(attemptKey: string): Promise<LoginAttemptRecord | null> {
    return this.records.get(attemptKey) ?? null;
  }
}

// ───────────────────────────────────────────────────────────────────────────
// Service
// ───────────────────────────────────────────────────────────────────────────

export class LoginThrottle {
  constructor(
    private readonly store: LoginThrottleStore,
    private readonly config: LoginThrottleConfig = DEFAULT_LOGIN_THROTTLE_CONFIG,
    private readonly pepper: string | null = null,
  ) {}

  private async ipKey(clientIp: string | null): Promise<string | null> {
    if (!clientIp) return null;
    return ipAttemptKey(clientIp, this.pepper);
  }

  /**
   * Gate an attempt and count it, before the credential is verified.
   *
   * Counting up front is what makes the control fail safe: an attempt that
   * never reaches a verdict — a crash, a timeout, an instance being torn down
   * mid-request — stays counted. `recordSuccess` is the only thing that gives
   * the attempt back.
   *
   * The IP dimension is evaluated first so a blocked source can never drive an
   * otherwise untouched account towards its own lockout.
   */
  async beginAttempt(
    username: string,
    clientIp: string | null,
    now: Date = new Date(),
  ): Promise<ThrottleDecision> {
    const ipKey = await this.ipKey(clientIp);
    if (ipKey) {
      const ipDecision = await this.store.beginAttempt({
        attemptKey: ipKey,
        maxFailures: this.config.ipMaxFailures,
        now,
      });
      if (!ipDecision.allowed) return ipDecision;
    }

    return this.store.beginAttempt({
      attemptKey: await accountAttemptKey(username, this.pepper),
      maxFailures: this.config.accountMaxFailures,
      now,
    });
  }

  /** A verified failure: escalate whichever dimension crossed its threshold. */
  async recordFailure(
    username: string,
    clientIp: string | null,
    now: Date = new Date(),
  ): Promise<void> {
    await this.store.escalate({
      attemptKey: await accountAttemptKey(username, this.pepper),
      maxFailures: this.config.accountMaxFailures,
      exponential: true,
      now,
    });
    const ipKey = await this.ipKey(clientIp);
    if (ipKey) {
      await this.store.escalate({
        attemptKey: ipKey,
        maxFailures: this.config.ipMaxFailures,
        exponential: false,
        now,
      });
    }
  }

  /**
   * A verified success clears the account counter (§6.4) and gives back the
   * attempt this request counted against the source IP — the IP dimension
   * counts failures, so a successful login must not consume its budget.
   */
  async recordSuccess(
    username: string,
    clientIp: string | null,
  ): Promise<void> {
    await this.store.clearAttempts(await accountAttemptKey(username, this.pepper));
    const ipKey = await this.ipKey(clientIp);
    if (ipKey) await this.store.releaseAttempt(ipKey);
  }
}

let _defaultStore: LoginThrottleStore | null = null;
let _overrideThrottle: LoginThrottle | null | undefined;

/** Test/dev hook; production always resolves the Postgres store from env. */
export function setLoginThrottleForTests(
  throttle: LoginThrottle | null | undefined,
): void {
  _overrideThrottle = throttle;
  _defaultStore = null;
}

/**
 * Resolve the throttle for the production login path.
 *
 * Returns null in production when no database is configured: an in-process
 * counter would not be shared between Cloud Run instances, so /login fails
 * closed rather than serving an unthrottled login form. It also returns null in
 * production when a database is configured but no pepper is, rather than
 * writing reversible attempt keys. Both cases surface as 503 at the route.
 */
export function getDefaultLoginThrottle(
  environment: Record<string, string | undefined> = process.env,
): LoginThrottle | null {
  if (_overrideThrottle !== undefined) return _overrideThrottle;

  const hasDbUrl = Boolean(
    environment.ODP_IDENTITY_DATABASE_URL ||
      environment.ODAY_DATABASE_URL ||
      environment.DATABASE_URL,
  );
  if (hasDbUrl) {
    // Fail closed when a production runtime has a database but no pepper.
    // `digest` would otherwise fall back to a raw SHA-256, and the attempt-key
    // inputs are both small enough to enumerate offline: the IPv4 space is
    // 2^32, and usernames come from a dictionary. An unpeppered digest column
    // is therefore a reversible record of who tried to log in and from where,
    // so /login answers 503 rather than writing one.
    if (
      isProductionWebRuntime(environment) &&
      !resolveThrottlePepper(environment)
    ) {
      return null;
    }
    if (!(_defaultStore instanceof PostgresLoginThrottleStore)) {
      _defaultStore = new PostgresLoginThrottleStore();
    }
  } else {
    if (isProductionWebRuntime(environment)) return null;
    if (!(_defaultStore instanceof MockLoginThrottleStore)) {
      _defaultStore = new MockLoginThrottleStore();
    }
  }

  return new LoginThrottle(
    _defaultStore,
    DEFAULT_LOGIN_THROTTLE_CONFIG,
    resolveThrottlePepper(environment),
  );
}
