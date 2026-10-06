/**
 * Postgres connection settings shared by the Web identity, session and login
 * throttle stores.
 *
 * The runtime DATABASE_URL is written for libpq/psycopg, where
 * `sslmode=require` means "encrypt, do not verify the server certificate".
 * node-postgres 8.x reads `sslmode` out of the connection string, treats
 * `prefer`/`require`/`verify-ca` as `verify-full`, and lets that override any
 * `ssl` option passed next to it. Against Cloud SQL that verification fails,
 * so every store connect threw and /login answered 503 WEB_AUTH_UNAVAILABLE
 * (Runtime Release 37453313684). The mode is therefore taken out of the URL
 * and applied here with libpq semantics.
 */

export interface PgSslConfig {
  rejectUnauthorized: boolean;
}

export interface PgConnectionConfig {
  connectionString: string;
  ssl: PgSslConfig | false | undefined;
}

const SSL_QUERY_PARAMS = new Set(["sslmode", "uselibpqcompat"]);

function splitQuery(connectionString: string): {
  base: string;
  params: Array<[string, string]>;
} {
  const index = connectionString.indexOf("?");
  if (index === -1) return { base: connectionString, params: [] };
  const params = connectionString
    .slice(index + 1)
    .split("&")
    .filter((part) => part.length > 0)
    .map((part): [string, string] => {
      const eq = part.indexOf("=");
      return eq === -1 ? [part, ""] : [part.slice(0, eq), part.slice(eq + 1)];
    });
  return { base: connectionString.slice(0, index), params };
}

function isUnixSocketTarget(base: string, params: Array<[string, string]>): boolean {
  const hostParam = params.find(([key]) => key === "host");
  if (hostParam) {
    return decodeURIComponent(hostParam[1]).startsWith("/");
  }
  // postgresql://user:pass@/db has no network host at all.
  return /^[a-z][a-z0-9+.-]*:\/\/[^/]*@\//i.test(base);
}

export function pgConnectionConfig(connectionString: string): PgConnectionConfig {
  const { base, params } = splitQuery(connectionString);
  const mode = (params.find(([key]) => key === "sslmode")?.[1] ?? "").toLowerCase();
  const kept = params.filter(([key]) => !SSL_QUERY_PARAMS.has(key));
  const query = kept.map(([key, value]) => (value === "" ? key : `${key}=${value}`)).join("&");
  const stripped = query ? `${base}?${query}` : base;

  if (mode === "verify-full" || mode === "verify-ca") {
    // Verification was asked for explicitly; keep node-postgres's own handling.
    return { connectionString, ssl: undefined };
  }
  if (mode === "" || mode === "disable" || mode === "allow") {
    return { connectionString: stripped, ssl: mode === "" ? undefined : false };
  }
  // libpq never negotiates TLS over a Unix-domain socket (Cloud SQL connector).
  if (isUnixSocketTarget(base, params)) {
    return { connectionString: stripped, ssl: false };
  }
  // prefer / require: encrypted, server certificate not verified.
  return { connectionString: stripped, ssl: { rejectUnauthorized: false } };
}
