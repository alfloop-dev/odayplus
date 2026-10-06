import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";
import { pgPoolConnectionOptions } from "../pgPoolOptions";

const require = createRequire(import.meta.url);
const ConnectionParameters = require("pg/lib/connection-parameters");

const PRIVATE_IP_URL =
  "postgresql://user:p%40ss@10.50.0.3:5432/oday_plus?sslmode=require";

describe("pgPoolConnectionOptions", () => {
  it("documents why: pg lets URL sslmode=require override an explicit ssl option", () => {
    const params = new ConnectionParameters({
      connectionString: PRIVATE_IP_URL,
      ssl: { rejectUnauthorized: false },
    });
    expect(params.ssl.rejectUnauthorized).not.toBe(false);
  });

  it("keeps libpq require semantics (encrypted, unverified) once applied to pg", () => {
    const options = pgPoolConnectionOptions(PRIVATE_IP_URL);
    expect(options.connectionString).toBe(
      "postgresql://user:p%40ss@10.50.0.3:5432/oday_plus",
    );
    const params = new ConnectionParameters(options);
    expect(params.ssl).toEqual({ rejectUnauthorized: false });
    expect(params.host).toBe("10.50.0.3");
    expect(params.password).toBe("p@ss");
  });

  it("preserves other query parameters", () => {
    const options = pgPoolConnectionOptions(
      "postgresql://u:p@10.50.0.3/db?application_name=web&sslmode=require&connect_timeout=5",
    );
    expect(options.connectionString).toBe(
      "postgresql://u:p@10.50.0.3/db?application_name=web&connect_timeout=5",
    );
    expect(options.ssl).toEqual({ rejectUnauthorized: false });
  });

  it("leaves URLs without sslmode=require untouched", () => {
    for (const url of [
      "postgresql://u:p@/oday_plus?host=/cloudsql/project:region:instance",
      "postgresql://u:p@10.50.0.3/db",
      "postgresql://u:p@db.example/db?sslmode=verify-full",
      "postgresql://u:p@10.50.0.3/db?sslmode=disable",
    ]) {
      expect(pgPoolConnectionOptions(url)).toEqual({
        connectionString: url,
        ssl: undefined,
      });
    }
  });
});
