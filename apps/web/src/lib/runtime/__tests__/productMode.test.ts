import { describe, expect, it } from "vitest";
import { resolveProductMode } from "../productMode";

describe("production data mode", () => {
  it.each([
    [{ NODE_ENV: "production", ODP_PRODUCT_MODE: "poc" }],
    [{ ODP_DEPLOY_ENV: "production", ODP_PRODUCT_MODE: "poc" }],
    [{ ODP_DEPLOY_ENV: "staging", ODP_PRODUCT_MODE: "poc" }],
    [{ ODP_REQUIRE_LIVE_DATA: "true", ODP_PRODUCT_MODE: "poc" }],
    [{ NEXT_PUBLIC_PRODUCTION_MODE: "true" }],
    [{ NEXT_PUBLIC_ODP_PRODUCT_MODE: "production" }],
    [{}],
  ])("fails closed as production for %j", (environment) => {
    expect(resolveProductMode(environment)).toBe("production");
  });

  it.each([
    [{ NODE_ENV: "test" }],
    [
      {
        NODE_ENV: "development",
        ODP_DEPLOY_ENV: "local",
        ODP_PRODUCT_MODE: "poc",
      },
    ],
    [
      {
        NODE_ENV: "development",
        ODP_DEPLOY_ENV: "local",
        NEXT_PUBLIC_ODP_PRODUCT_MODE: "poc",
      },
    ],
  ])("allows fixtures only in an isolated test/local POC: %j", (environment) => {
    expect(resolveProductMode(environment)).toBe("poc");
  });

  it("does not let a public POC flag downgrade an unspecified runtime", () => {
    expect(
      resolveProductMode({
        NODE_ENV: "development",
        NEXT_PUBLIC_ODP_PRODUCT_MODE: "poc",
      }),
    ).toBe("production");
  });
});
