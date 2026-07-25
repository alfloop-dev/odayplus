import { afterEach, describe, expect, it, vi } from "vitest";
import { operatorSecurityHeaders } from "../operatorSecurityHeaders";

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllEnvs();
});

describe("operatorSecurityHeaders", () => {
  it("sends no browser-asserted identity in production", () => {
    vi.stubEnv("NODE_ENV", "production");
    sessionStorage.setItem("oday.operator.subject", "spoofed-subject");
    sessionStorage.setItem("oday.operator.tenant", "spoofed-tenant");

    expect(operatorSecurityHeaders("ops-lead", "spoofed-explicit")).toEqual({
      "X-Operator-Role": "ops-lead",
    });
  });

  it("also strips browser identity when live data is required outside a production NODE_ENV", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("ODP_REQUIRE_LIVE_DATA", "true");
    vi.stubEnv("ODP_PRODUCT_MODE", "poc");
    sessionStorage.setItem("oday.operator.subject", "spoofed-subject");
    sessionStorage.setItem("oday.operator.tenant", "spoofed-tenant");

    expect(operatorSecurityHeaders("ops-lead", "spoofed-explicit")).toEqual({
      "X-Operator-Role": "ops-lead",
    });
  });

  it("keeps explicitly configured trusted headers in local/test mode", () => {
    vi.stubEnv("NODE_ENV", "test");
    sessionStorage.setItem("oday.operator.subject", "local-user");
    sessionStorage.setItem("oday.operator.tenant", "local-tenant");

    expect(operatorSecurityHeaders("ops-lead")).toEqual({
      "X-Operator-Role": "ops-lead",
      "X-Roles": "operations_manager",
      "X-Subject-Id": "local-user",
      "X-Tenant-Id": "local-tenant",
    });
  });
});
