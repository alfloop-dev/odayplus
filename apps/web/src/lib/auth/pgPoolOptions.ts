/**
 * Shared node-postgres pool options for the Web identity, session and login
 * throttle stores.
 *
 * `sslmode=require` in the database URL means "encrypt, do not verify the
 * server certificate" (libpq semantics), which the Python services honour.
 * node-postgres 8.x instead treats `require` as `verify-full`, and the values
 * it parses from `connectionString` override an explicit `ssl` option. Cloud
 * SQL's private-IP server certificate is signed by a Google-internal CA and is
 * not issued for the private IP, so verification always fails and every login
 * degrades to WEB_AUTH_UNAVAILABLE. Strip `sslmode=require` from the URL and
 * express the intended libpq behaviour through the `ssl` option instead.
 * Other modes (`verify-ca`, `verify-full`, `disable`) are left to the driver.
 */

export interface PgPoolConnectionOptions {
  connectionString: string;
  ssl: { rejectUnauthorized: false } | undefined;
}

export function pgPoolConnectionOptions(
  connectionString: string,
): PgPoolConnectionOptions {
  const queryStart = connectionString.indexOf("?");
  if (queryStart < 0) {
    return { connectionString, ssl: undefined };
  }
  const base = connectionString.slice(0, queryStart);
  const params = connectionString.slice(queryStart + 1).split("&");
  const kept = params.filter((param) => param !== "sslmode=require");
  if (kept.length === params.length) {
    return { connectionString, ssl: undefined };
  }
  return {
    connectionString: kept.length ? `${base}?${kept.join("&")}` : base,
    ssl: { rejectUnauthorized: false },
  };
}
