import { describe, expect, it } from "vitest";
import {
  allowLegacyTrustedHeaders,
  resolveWebBaseUrl,
  safeReturnTo,
} from "../runtime";

describe("web auth runtime policy", () => {
  it("accepts only same-origin relative return paths", () => {
    expect(safeReturnTo("/operator?tab=network")).toBe(
      "/operator?tab=network",
    );
    expect(safeReturnTo("https://attacker.example")).toBe("/operator");
    expect(safeReturnTo("//attacker.example/path")).toBe("/operator");
    expect(safeReturnTo("/operator\u0000bad")).toBe("/operator");
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
  });

  it("never enables trusted browser identity headers in production", () => {
    expect(
      allowLegacyTrustedHeaders({
        NODE_ENV: "production",
        ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS: "true",
      }),
    ).toBe(false);
    expect(allowLegacyTrustedHeaders({ NODE_ENV: "test" })).toBe(true);
  });
});

