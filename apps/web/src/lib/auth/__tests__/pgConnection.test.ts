import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Client } from "pg";
import { parse } from "pg-connection-string";
import { pgConnectionConfig } from "../pgConnection";

const TCP_REQUIRE = "postgresql://app:p%40ss@10.20.0.3:5432/oday?sslmode=require";

interface ResolvedParameters {
  host: string;
  isDomainSocket: boolean;
  ssl: unknown;
}

/** What node-postgres would actually use for the stores' Pool config (no connect). */
function resolved(connectionString: string): ResolvedParameters {
  const client = new Client(pgConnectionConfig(connectionString));
  return (client as unknown as { connectionParameters: ResolvedParameters })
    .connectionParameters;
}

/** pg verifies the server certificate unless ssl is off or rejectUnauthorized is false. */
function verifiesCertificate(ssl: unknown): boolean {
  if (!ssl) return false;
  return typeof ssl !== "object" || (ssl as PgSslLike).rejectUnauthorized !== false;
}

interface PgSslLike {
  rejectUnauthorized?: boolean;
}

let savedPgHost: string | undefined;
let savedPgSslMode: string | undefined;

beforeEach(() => {
  savedPgHost = process.env.PGHOST;
  savedPgSslMode = process.env.PGSSLMODE;
  delete process.env.PGHOST;
  delete process.env.PGSSLMODE;
});

afterEach(() => {
  if (savedPgHost === undefined) delete process.env.PGHOST;
  else process.env.PGHOST = savedPgHost;
  if (savedPgSslMode === undefined) delete process.env.PGSSLMODE;
  else process.env.PGSSLMODE = savedPgSslMode;
});

describe("pgConnectionConfig", () => {
  it("reproduces the defect: node-postgres turns sslmode=require into certificate verification", () => {
    // What the stores handed to Pool before: the URL's sslmode wins over the
    // sibling ssl option and asks for a verified TLS connection.
    expect(parse(TCP_REQUIRE).ssl).not.toEqual({ rejectUnauthorized: false });
    expect(parse(TCP_REQUIRE).ssl).toBeTruthy();
  });

  it("applies libpq require semantics: encrypted, certificate not verified", () => {
    const config = pgConnectionConfig(TCP_REQUIRE);

    expect(config.ssl).toEqual({ rejectUnauthorized: false });
    expect(config.connectionString).toBe("postgresql://app:p%40ss@10.20.0.3:5432/oday");
    expect(parse(config.connectionString).ssl).toBeUndefined();
    expect(resolved(TCP_REQUIRE)).toMatchObject({
      host: "10.20.0.3",
      isDomainSocket: false,
      ssl: { rejectUnauthorized: false },
    });
  });

  it("treats prefer like require and keeps unrelated parameters intact", () => {
    const config = pgConnectionConfig(
      "postgresql://app:pw@db.internal/oday?application_name=web&sslmode=prefer&connect_timeout=5",
    );

    expect(config.ssl).toEqual({ rejectUnauthorized: false });
    expect(config.connectionString).toBe(
      "postgresql://app:pw@db.internal/oday?application_name=web&connect_timeout=5",
    );
  });

  it("uses no TLS over a Cloud SQL unix socket, as libpq does", () => {
    const viaHostParam = "postgresql://app:pw@/oday?host=/cloudsql/proj:asia-east1:oday&sslmode=require";
    expect(pgConnectionConfig(viaHostParam)).toEqual({
      connectionString: "postgresql://app:pw@/oday?host=/cloudsql/proj:asia-east1:oday",
      ssl: false,
    });
    expect(resolved(viaHostParam)).toMatchObject({
      host: "/cloudsql/proj:asia-east1:oday",
      isDomainSocket: true,
      ssl: false,
    });

    for (const url of [
      "postgresql://app:pw@/oday?host=%2Fcloudsql%2Fproj%3Aasia-east1%3Aoday&sslmode=require",
      "postgresql://app:pw@%2Fcloudsql%2Fproj/oday?sslmode=require",
      "socket://app:pw@/cloudsql/proj?db=oday&sslmode=require",
    ]) {
      expect(pgConnectionConfig(url).ssl).toBe(false);
      expect(resolved(url)).toMatchObject({ isDomainSocket: true, ssl: false });
    }
  });

  it("resolves an omitted host to localhost over TCP and keeps TLS on", () => {
    const url = "postgresql://app:dummy@/oday?sslmode=require";

    expect(pgConnectionConfig(url)).toEqual({
      connectionString: "postgresql://app:dummy@/oday",
      ssl: { rejectUnauthorized: false },
    });
    expect(resolved(url)).toMatchObject({
      host: "localhost",
      isDomainSocket: false,
      ssl: { rejectUnauthorized: false },
    });
  });

  it("follows PGHOST when the URL omits the host", () => {
    const url = "postgresql://app:dummy@/oday?sslmode=require";

    process.env.PGHOST = "db.internal";
    expect(pgConnectionConfig(url).ssl).toEqual({ rejectUnauthorized: false });
    expect(resolved(url)).toMatchObject({
      host: "db.internal",
      isDomainSocket: false,
      ssl: { rejectUnauthorized: false },
    });

    process.env.PGHOST = "/cloudsql/proj:asia-east1:oday";
    expect(pgConnectionConfig(url).ssl).toBe(false);
    expect(resolved(url)).toMatchObject({
      host: "/cloudsql/proj:asia-east1:oday",
      isDomainSocket: true,
      ssl: false,
    });
  });

  it("honours explicit disable and leaves a URL without sslmode alone", () => {
    expect(pgConnectionConfig("postgresql://app:pw@db/oday?sslmode=disable")).toEqual({
      connectionString: "postgresql://app:pw@db/oday",
      ssl: false,
    });
    expect(pgConnectionConfig("postgresql://app:pw@db/oday")).toEqual({
      connectionString: "postgresql://app:pw@db/oday",
      ssl: undefined,
    });
  });

  it("keeps explicit verification modes untouched", () => {
    for (const mode of ["verify-full", "verify-ca"]) {
      const url = `postgresql://app:pw@db/oday?sslmode=${mode}`;
      expect(pgConnectionConfig(url)).toEqual({ connectionString: url, ssl: undefined });
      expect(verifiesCertificate(resolved(url).ssl)).toBe(true);
    }
  });

  it("decodes the mode and lets the last sslmode win, as pg does", () => {
    for (const url of [
      "postgresql://app:pw@db/oday?sslmode=verify%2Dfull",
      "postgresql://app:pw@db/oday?sslmode=require&sslmode=verify-full",
      "postgresql://app:pw@db/oday?ssl%6Dode=verify-full",
      "postgresql://app:pw@db/oday?sslmode=require&ssl%6Dode=verify-full",
    ]) {
      expect(pgConnectionConfig(url)).toEqual({ connectionString: url, ssl: undefined });
      expect(verifiesCertificate(resolved(url).ssl)).toBe(true);
    }

    const lastRequire = "postgresql://app:pw@db/oday?sslmode=verify-full&sslmode=require";
    expect(pgConnectionConfig(lastRequire)).toEqual({
      connectionString: "postgresql://app:pw@db/oday",
      ssl: { rejectUnauthorized: false },
    });

    const encodedRequire = "postgresql://app:pw@db/oday?sslmode=re%71uire&ssl%6Dode=require";
    expect(pgConnectionConfig(encodedRequire)).toEqual({
      connectionString: "postgresql://app:pw@db/oday",
      ssl: { rejectUnauthorized: false },
    });
  });

  it("fails closed on modes it does not recognise", () => {
    for (const mode of ["REQUIRE", "Require", "bogus", "require%20"]) {
      const url = `postgresql://app:pw@db/oday?sslmode=${mode}`;
      expect(pgConnectionConfig(url)).toEqual({ connectionString: url, ssl: undefined });
      expect(verifiesCertificate(resolved(url).ssl)).toBe(true);
    }
  });

  it("leaves URLs with other TLS parameters to node-postgres", () => {
    for (const url of [
      "postgresql://app:pw@db/oday?sslmode=require&sslrootcert=/etc/ssl/server-ca.pem",
      "postgresql://app:pw@db/oday?sslmode=require&sslcert=/c.pem&sslkey=/k.pem",
      "postgresql://app:pw@db/oday?sslmode=require&ssl=true",
      "postgresql://app:pw@db/oday?sslmode=require&sslnegotiation=direct",
      "postgresql://app:pw@db/oday?uselibpqcompat=true&sslmode=require",
    ]) {
      expect(pgConnectionConfig(url)).toEqual({ connectionString: url, ssl: undefined });
    }
  });
});
