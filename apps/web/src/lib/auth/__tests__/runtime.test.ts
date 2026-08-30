import { describe, expect, it } from "vitest";
import {
  allowLegacyTrustedHeaders,
  isOidcEnabled,
  resolveAuthMode,
  resolveWebBaseUrl,
  safeReturnTo,
  verifyCsrfOrigin,
} from "../runtime";

describe("web auth runtime policy", () => {
  it("T12: accepts only same-origin relative return paths and rejects open redirects", () => {
    expect(safeReturnTo("/operator?tab=network")).toBe(
      "/operator?tab=network",
    );
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

    // Complete OIDC configuration
    expect(
      isOidcEnabled({
        ODP_AUTH_MODE: "oidc",
        ODP_WEB_OIDC_ISSUER: "https://accounts.google.com",
        ODP_WEB_OIDC_CLIENT_ID: "client-id-123.apps.googleusercontent.com",
      }),
    ).toBe(true);

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
