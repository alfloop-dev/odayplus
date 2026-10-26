import { describe, expect, it } from "vitest";
import {
  allowLegacyTrustedHeaders,
  isOidcEnabled,
  resolveAuthMode,
  resolveWebBaseUrl,
  safeReturnTo,
  trustedRequestOrigin,
  verifyCsrfOrigin,
} from "../runtime";

describe("web auth runtime policy", () => {
  it("T12: accepts only same-origin relative return paths and rejects open redirects", () => {
    expect(safeReturnTo("/operator?tab=network")).toBe("/operator?tab=network");
    expect(safeReturnTo("https://attacker.example")).toBe("/operator");
    expect(safeReturnTo("//attacker.example/path")).toBe("/operator");
    expect(safeReturnTo("/operator\u0000bad")).toBe("/operator");
    expect(safeReturnTo(null)).toBe("/operator");
    expect(safeReturnTo("")).toBe("/operator");
    expect(safeReturnTo("javascript:alert(1)")).toBe("/operator");
  });

  it("requires an explicit HTTPS web origin in production", () => {
    expect(() =>
      resolveWebBaseUrl("https://untrusted-host.example", {
        NODE_ENV: "production",
      }),
    ).toThrow("ODP_WEB_BASE_URL is required");
    expect(
      resolveWebBaseUrl("https://ignored.example", {
        NODE_ENV: "production",
        ODP_WEB_BASE_URL: "https://ops.oday.plus",
      }),
    ).toBe("https://ops.oday.plus");
    expect(() =>
      resolveWebBaseUrl("https://ignored.example", {
        NODE_ENV: "production",
        ODP_WEB_BASE_URL: "http://ops.oday.plus",
      }),
    ).toThrow("must use https");
    expect(() =>
      resolveWebBaseUrl("https://ignored.example", {
        NODE_ENV: "development",
        ODP_DEPLOY_ENV: "production",
        ODP_WEB_BASE_URL: "http://ops.oday.plus",
      }),
    ).toThrow("must use https");
  });

  it("never enables trusted browser identity headers in production", () => {
    expect(
      allowLegacyTrustedHeaders({
        NODE_ENV: "development",
        ODP_DEPLOY_ENV: "production",
        ODP_PRODUCT_MODE: "poc",
        ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS: "true",
      }),
    ).toBe(false);
    expect(allowLegacyTrustedHeaders({ NODE_ENV: "test" })).toBe(true);
  });

  it("resolves auth mode properly according to contract precedence", () => {
    // 1. Default when unset -> local
    expect(resolveAuthMode({})).toBe("local");

    // 2. Explicit ODP_AUTH_MODE
    expect(resolveAuthMode({ ODP_AUTH_MODE: "local" })).toBe("local");
    expect(resolveAuthMode({ ODP_AUTH_MODE: " Local " })).toBe("local");
    expect(resolveAuthMode({ ODP_AUTH_MODE: "oidc" })).toBe("oidc");
    expect(resolveAuthMode({ ODP_AUTH_MODE: " OIDC " })).toBe("oidc");

    // 3. Conflict detection
    expect(() =>
      resolveAuthMode({
        ODP_AUTH_MODE: "local",
        ODP_AUTH_OIDC_ENABLED: "true",
      }),
    ).toThrow("conflicts");
    expect(() =>
      resolveAuthMode({
        ODP_AUTH_MODE: "oidc",
        ODP_AUTH_OIDC_ENABLED: "false",
      }),
    ).toThrow("conflicts");

    // 4. Legacy flag
    expect(resolveAuthMode({ ODP_AUTH_OIDC_ENABLED: "true" })).toBe("oidc");
    expect(resolveAuthMode({ ODP_AUTH_OIDC_ENABLED: "false" })).toBe("local");

    // 5. Pre-contract issuer detection
    expect(
      resolveAuthMode({
        ODP_WEB_OIDC_ISSUER: "https://accounts.google.com",
      }),
    ).toBe("oidc");
    expect(
      resolveAuthMode({
        ODP_WEB_OIDC_ISSUER: "placeholder",
      }),
    ).toBe("local");
  });

  it("T14: enforces OIDC availability only when fully configured", () => {
    // Local mode -> OIDC not enabled
    expect(isOidcEnabled({ ODP_AUTH_MODE: "local" })).toBe(false);
    expect(isOidcEnabled({})).toBe(false);

    // Complete OIDC configuration (issuer + client_id + client_secret)
    expect(
      isOidcEnabled({
        ODP_AUTH_MODE: "oidc",
        ODP_WEB_OIDC_ISSUER: "https://accounts.google.com",
        ODP_WEB_OIDC_CLIENT_ID: "client-id-123.apps.googleusercontent.com",
        ODP_WEB_OIDC_CLIENT_SECRET: ["configured", "oidc", "credential"].join(
          "-",
        ),
      }),
    ).toBe(true);

    // Missing client_secret -> fail closed
    expect(() =>
      isOidcEnabled({
        ODP_AUTH_MODE: "oidc",
        ODP_WEB_OIDC_ISSUER: "https://accounts.google.com",
        ODP_WEB_OIDC_CLIENT_ID: "client-id-123.apps.googleusercontent.com",
      }),
    ).toThrow("OIDC mode requires complete configuration");

    // Incomplete OIDC configuration -> fail closed
    expect(() =>
      isOidcEnabled({
        ODP_AUTH_MODE: "oidc",
        ODP_WEB_OIDC_ISSUER: "https://accounts.google.com",
      }),
    ).toThrow("OIDC mode requires complete configuration");

    expect(() =>
      isOidcEnabled({
        ODP_AUTH_MODE: "oidc",
        ODP_WEB_OIDC_ISSUER: "placeholder",
        ODP_WEB_OIDC_CLIENT_ID: "placeholder",
      }),
    ).toThrow("OIDC mode requires complete configuration");
  });

  it("T11: verifies CSRF request origin against canonical web origin", () => {
    const canonicalEnv = {
      ODP_WEB_BASE_URL: "https://ops.oday.plus",
    };

    // Same origin matches
    expect(
      verifyCsrfOrigin(
        {
          headers: new Headers({ origin: "https://ops.oday.plus" }),
          nextUrl: { origin: "https://ops.oday.plus" },
        },
        canonicalEnv,
      ),
    ).toBe(true);

    // Referer fallback matches
    expect(
      verifyCsrfOrigin(
        {
          headers: new Headers({ referer: "https://ops.oday.plus/login" }),
          nextUrl: { origin: "https://ops.oday.plus" },
        },
        canonicalEnv,
      ),
    ).toBe(true);

    // Cross-origin rejected
    expect(
      verifyCsrfOrigin(
        {
          headers: new Headers({ origin: "https://attacker.example" }),
          nextUrl: { origin: "https://ops.oday.plus" },
        },
        canonicalEnv,
      ),
    ).toBe(false);

    // Missing origin / referer rejected
    expect(
      verifyCsrfOrigin(
        {
          headers: new Headers(),
          nextUrl: { origin: "https://ops.oday.plus" },
        },
        canonicalEnv,
      ),
    ).toBe(false);
  });
});

describe("Cloud Run hostname aliases", () => {
  const cloudRunEnv = { ODP_WEB_BASE_URL: "https://oday-web-767864276141.asia-east1.run.app" };
  const internal = { origin: "http://0.0.0.0:3000" };
  const formFrom = (host: string, origin: string) => ({
    headers: new Headers({ host, origin }),
    nextUrl: internal,
  });

  it.each([
    "oday-web-767864276141.asia-east1.run.app",
    "oday-web-2l6wuyl67q-de.a.run.app",
    "candidate-aa3705ec5a2f7909---oday-web-2l6wuyl67q-de.a.run.app",
    "candidate-aa3705ec5a2f7909---oday-web-767864276141.asia-east1.run.app",
  ])("treats %s as this service and accepts its same-origin form", (host) => {
    expect(trustedRequestOrigin({ headers: new Headers({ host }) }, cloudRunEnv)).toBe(`https://${host}`);
    expect(verifyCsrfOrigin(formFrom(host, `https://${host}`), cloudRunEnv)).toBe(true);
  });

  it("still rejects a cross-site page posting to an alias host", () => {
    expect(
      verifyCsrfOrigin(formFrom("oday-web-2l6wuyl67q-de.a.run.app", "https://attacker.example"), cloudRunEnv),
    ).toBe(false);
  });

  const publicHost = "oday-web-767864276141.asia-east1.run.app";
  const legacyHost = "oday-web-2l6wuyl67q-de.a.run.app";
  const tagHost = "candidate-aa3705ec5a2f7909---oday-web-2l6wuyl67q-de.a.run.app";

  it.each([
    [publicHost, legacyHost],
    [legacyHost, publicHost],
    [publicHost, tagHost],
    [tagHost, publicHost],
    [legacyHost, tagHost],
    [tagHost, legacyHost],
  ])("rejects Host %s receiving a form whose Origin is the sibling %s", (host, originHost) => {
    expect(verifyCsrfOrigin(formFrom(host, `https://${originHost}`), cloudRunEnv)).toBe(false);
  });

  it("applies the same exact-host rule to a Referer-only request", () => {
    const refererFrom = (host: string, referer: string) => ({
      headers: new Headers({ host, referer }),
      nextUrl: internal,
    });
    expect(verifyCsrfOrigin(refererFrom(legacyHost, `https://${legacyHost}/login`), cloudRunEnv)).toBe(true);
    expect(verifyCsrfOrigin(refererFrom(legacyHost, `https://${publicHost}/login`), cloudRunEnv)).toBe(false);
    expect(verifyCsrfOrigin(refererFrom(publicHost, `https://${legacyHost}/login`), cloudRunEnv)).toBe(false);
  });

  it("rejects the container's internal origin once a trusted Host is known", () => {
    expect(verifyCsrfOrigin(formFrom(legacyHost, internal.origin), cloudRunEnv)).toBe(false);
  });

  it.each([
    "attacker.example",
    "other-service-767864276141.asia-east1.run.app",
    "oday-web-999.asia-east1.run.app",
    "oday-web-767864276141.us-central1.run.app",
    "oday-web-2l6wuyl67q-de.a.run.app.attacker.example",
  ])("does not trust Host %s even when Origin matches it", (host) => {
    expect(trustedRequestOrigin({ headers: new Headers({ host }) }, cloudRunEnv)).toBeNull();
    expect(verifyCsrfOrigin(formFrom(host, `https://${host}`), cloudRunEnv)).toBe(false);
  });

  it("ignores X-Forwarded-Host and derives no aliases for a custom domain", () => {
    expect(
      trustedRequestOrigin(
        { headers: new Headers({ "x-forwarded-host": "oday-web-2l6wuyl67q-de.a.run.app" }) },
        cloudRunEnv,
      ),
    ).toBeNull();
    const customEnv = { ODP_WEB_BASE_URL: "https://ops.oday.plus" };
    expect(trustedRequestOrigin({ headers: new Headers({ host: "ops.oday.plus" }) }, customEnv)).toBe(
      "https://ops.oday.plus",
    );
    expect(
      trustedRequestOrigin({ headers: new Headers({ host: "oday-web-2l6wuyl67q-de.a.run.app" }) }, customEnv),
    ).toBeNull();
    expect(trustedRequestOrigin({ headers: new Headers({ host: "ops.oday.plus" }) }, {})).toBeNull();
  });
});
