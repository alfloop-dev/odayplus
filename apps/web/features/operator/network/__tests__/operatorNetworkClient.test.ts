import { afterEach, describe, expect, it, vi } from "vitest";
import { buildOperatorNetworkClient } from "../operatorNetworkClient";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("buildOperatorNetworkClient", () => {
  it("uses the same-origin BFF whenever live data is required", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("ODP_REQUIRE_LIVE_DATA", "true");
    vi.stubEnv("ODP_PRODUCT_MODE", "poc");
    vi.stubEnv(
      "NEXT_PUBLIC_ODP_API_BASE_URL",
      "https://browser-visible-api.example",
    );

    const client = buildOperatorNetworkClient("ops-lead");

    expect(client?.baseUrl).toBe(window.location.origin);
  });

  it("allows an explicit direct API only in isolated test mode", () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv(
      "NEXT_PUBLIC_ODP_API_BASE_URL",
      "http://127.0.0.1:8099",
    );

    const client = buildOperatorNetworkClient("ops-lead");

    expect(client?.baseUrl).toBe("http://127.0.0.1:8099");
  });
});
