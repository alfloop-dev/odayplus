import { describe, expect, it } from "vitest";
import { parse } from "pg-connection-string";
import { pgConnectionConfig } from "../pgConnection";

const TCP_REQUIRE = "postgresql://app:p%40ss@10.20.0.3:5432/oday?sslmode=require";

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
    const viaHostParam = pgConnectionConfig(
      "postgresql://app:pw@/oday?host=/cloudsql/proj:asia-east1:oday&sslmode=require",
    );
    expect(viaHostParam.ssl).toBe(false);
    expect(viaHostParam.connectionString).toBe(
      "postgresql://app:pw@/oday?host=/cloudsql/proj:asia-east1:oday",
    );

    const encodedHost = pgConnectionConfig(
      "postgresql://app:pw@/oday?host=%2Fcloudsql%2Fproj%3Aasia-east1%3Aoday&sslmode=require",
    );
    expect(encodedHost.ssl).toBe(false);
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
    }
  });
});
