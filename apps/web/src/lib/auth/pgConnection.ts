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
 * (Runtime Release 37453313684). For the modes that libpq does not verify,
 * the mode is therefore taken out of the URL and applied here.
 *
 * The URL is read the way pg-connection-string 2.x reads it (WHATWG URL,
 * percent-decoded query, last value of a repeated key wins) and the target
 * host is resolved the way pg's ConnectionParameters resolves it (query
 * `host`, then the URL host, then PGHOST, then localhost). Anything this
 * helper does not positively recognise is handed to node-postgres untouched,
 * which verifies the certificate: unknown input fails closed.
 */

export interface PgSslConfig {
  rejectUnauthorized: boolean;
}

export interface PgConnectionConfig {
  connectionString: string;
  ssl: PgSslConfig | false | undefined;
}

// Rewritten here with libpq semantics; every other mode (verify-full,
// verify-ca, pg's own no-verify, anything unrecognised) stays with pg.
const LIBPQ_UNVERIFIED_MODES = new Set(["disable", "allow", "prefer", "require"]);

// pg builds its own ssl object from these and lets it override ours, so a URL
// carrying any of them is left to pg as a whole.
const PG_SSL_QUERY_PARAMS = [
  "ssl",
  "sslcert",
  "sslkey",
  "sslrootcert",
  "sslnegotiation",
  "uselibpqcompat",
];

interface PgUrlView {
  params: Map<string, string>;
  host: string;
}

/** Mirrors pg-connection-string's parse() for the fields used here. */
function readLikePg(connectionString: string): PgUrlView | null {
  if (connectionString.charAt(0) === "/") {
    return { params: new Map(), host: connectionString.split(" ")[0] };
  }
  let str = connectionString;
  if (/ |%[^a-f0-9]|%[a-f0-9][^a-f0-9]/i.test(str)) {
    str = encodeURI(str).replace(/%25(\d\d)/g, "%$1");
  }
  let url: URL;
  let dummyHost = false;
  try {
    try {
      url = new URL(str, "postgres://base");
    } catch {
      url = new URL(str.replace("@/", "@___DUMMY___/"), "postgres://base");
      dummyHost = true;
    }
  } catch {
    return null;
  }
  const params = new Map<string, string>();
  for (const [key, value] of url.searchParams.entries()) params.set(key, value);
  if (url.protocol === "socket:") {
    return { params, host: decodeURI(url.pathname) };
  }
  let host = params.get("host") ?? "";
  if (!host) {
    try {
      host = decodeURIComponent(dummyHost ? "" : url.hostname);
    } catch {
      return null;
    }
  }
  return { params, host };
}

/** pg's ConnectionParameters: an empty host falls back to PGHOST, then localhost. */
function effectiveHost(view: PgUrlView): string {
  return view.host || process.env.PGHOST || "localhost";
}

function withoutSslmode(connectionString: string): string {
  const queryStart = connectionString.indexOf("?");
  if (queryStart === -1) return connectionString;
  const fragmentStart = connectionString.indexOf("#", queryStart);
  const queryEnd = fragmentStart === -1 ? connectionString.length : fragmentStart;
  const kept = connectionString
    .slice(queryStart + 1, queryEnd)
    .split("&")
    .filter((part) => {
      if (part.length === 0) return false;
      const [key] = new URLSearchParams(part).keys();
      return key !== "sslmode";
    });
  const base = connectionString.slice(0, queryStart);
  const fragment = connectionString.slice(queryEnd);
  return (kept.length > 0 ? `${base}?${kept.join("&")}` : base) + fragment;
}

export function pgConnectionConfig(connectionString: string): PgConnectionConfig {
  const untouched: PgConnectionConfig = { connectionString, ssl: undefined };
  const view = readLikePg(connectionString);
  const mode = view?.params.get("sslmode");
  if (!view || mode === undefined || !LIBPQ_UNVERIFIED_MODES.has(mode)) return untouched;
  if (PG_SSL_QUERY_PARAMS.some((key) => view.params.has(key))) return untouched;

  const stripped = withoutSslmode(connectionString);
  const check = readLikePg(stripped);
  if (!check || check.params.has("sslmode") || check.host !== view.host) return untouched;

  if (mode === "disable" || mode === "allow") {
    return { connectionString: stripped, ssl: false };
  }
  // libpq never negotiates TLS over a Unix-domain socket (Cloud SQL connector).
  if (effectiveHost(view).startsWith("/")) {
    return { connectionString: stripped, ssl: false };
  }
  // prefer / require over TCP: encrypted, server certificate not verified.
  return { connectionString: stripped, ssl: { rejectUnauthorized: false } };
}
