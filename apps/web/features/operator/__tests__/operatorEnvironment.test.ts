import { describe, expect, it } from "vitest";
import { readDeploymentEnvironment, resolveOperatorEnvironment } from "../operatorEnvironment";

describe("resolveOperatorEnvironment", () => {
  it.each([
    ["dev", "dev", "DEV"],
    ["development", "dev", "DEV"],
    ["staging", "staging", "STAGING"],
    ["prod", "production", "PRODUCTION"],
    [" Production ", "production", "PRODUCTION"],
    ["e2e", "e2e", "E2E"],
    ["local", "local", "LOCAL"],
  ])("maps %j to %s", (value, id, label) => {
    expect(resolveOperatorEnvironment(value)).toEqual({ id, label });
  });

  it("never guesses production for a missing or unknown deploy target", () => {
    expect(resolveOperatorEnvironment(undefined)).toEqual({ id: "unset", label: "ENV UNSET" });
    expect(resolveOperatorEnvironment("")).toEqual({ id: "unset", label: "ENV UNSET" });
    expect(resolveOperatorEnvironment("qa-17")).toEqual({ id: "unset", label: "ENV UNSET" });
  });
});

describe("readDeploymentEnvironment", () => {
  it("prefers the runtime deploy target over the build-time public copy", () => {
    expect(
      readDeploymentEnvironment({ ODP_DEPLOY_ENV: "dev", NEXT_PUBLIC_ODP_DEPLOY_ENV: "staging" }),
    ).toBe("dev");
    expect(readDeploymentEnvironment({ NEXT_PUBLIC_ODP_DEPLOY_ENV: "staging" })).toBe("staging");
    expect(readDeploymentEnvironment({})).toBeUndefined();
  });
});
