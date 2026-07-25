import { afterEach, describe, expect, it, vi } from "vitest";
import {
  inspectOperatorShellPayload,
  isOperatorProductionMode,
  isSeedDataSource,
  operatorFixturesAllowed,
  toUnavailableOperatorStatus,
} from "../operatorDataMode";

const livePayload = {
  meta: {
    source: "operator-shell-production",
    dataMode: "live",
    role: {
      id: "ops-lead",
      label: "Live operator",
      allowedWorkspaces: ["today"],
    },
  },
  navigation: {
    allowedWorkspaces: ["today"],
    workspaces: [{ id: "today" }],
  },
  today: {
    hero: {
      name: "Live operator",
      roleLabel: "Operations",
      scope: "Live tenant",
      dateLabel: "2026-07-24",
    },
    kpis: [{ label: "Live KPI", value: "1" }],
  },
};

describe("operator data mode", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("allows fixtures only outside production", () => {
    expect(operatorFixturesAllowed({ nodeEnv: "test" })).toBe(true);
    expect(operatorFixturesAllowed({ nodeEnv: "production" })).toBe(false);
    expect(
      operatorFixturesAllowed({
        nodeEnv: "production",
        productMode: "poc",
      }),
    ).toBe(false);
    expect(
      operatorFixturesAllowed({
        nodeEnv: "development",
        productMode: "production",
      }),
    ).toBe(false);
    expect(operatorFixturesAllowed({ nodeEnv: "development", productionMode: "true" })).toBe(false);
    expect(operatorFixturesAllowed({ nodeEnv: "development" })).toBe(false);
    expect(
      operatorFixturesAllowed({
        deployEnv: "local",
        nodeEnv: "development",
        productMode: "poc",
      }),
    ).toBe(true);
    expect(
      operatorFixturesAllowed({
        deployEnv: "production",
        nodeEnv: "development",
        productMode: "poc",
      }),
    ).toBe(false);
    expect(
      operatorFixturesAllowed({
        nodeEnv: "development",
        productMode: "poc",
        requireLiveData: "true",
      }),
    ).toBe(false);
    expect(isOperatorProductionMode({ nodeEnv: "production" })).toBe(true);
  });

  it.each(["ODAY_ENV", "ODP_ENV"])(
    "treats %s as a production-owned deployment alias",
    (name) => {
      vi.stubEnv("NODE_ENV", "development");
      vi.stubEnv("ODP_DEPLOY_ENV", "");
      vi.stubEnv(name, "production");

      expect(operatorFixturesAllowed()).toBe(false);
    },
  );

  it("blocks the known seed-backed shell envelope", () => {
    expect(
      inspectOperatorShellPayload({
        ...livePayload,
        meta: { source: "operator-shell-api-envelope" },
      }),
    ).toEqual({
      source: "operator-shell-api-envelope",
      status: "seed",
    });
    expect(isSeedDataSource("fixture-replay")).toBe(true);
    expect(isSeedDataSource("demographics-provider")).toBe(false);
  });

  it("distinguishes empty and live shell payloads", () => {
    expect(
      inspectOperatorShellPayload({
        meta: { source: "operator-shell-production" },
        navigation: { workspaces: [{ id: "today" }] },
        today: { kpis: [], queue: [] },
      }).status,
    ).toBe("empty");
    expect(inspectOperatorShellPayload(livePayload).status).toBe("ready");
    expect(
      inspectOperatorShellPayload({
        ...livePayload,
        meta: {
          source: "operator-shell-api-envelope",
          dataMode: "live",
          dataOrigin: { kind: "live" },
          liveReadiness: { ready: true },
        },
      }).status,
    ).toBe("seed");
    expect(
      inspectOperatorShellPayload({
        ...livePayload,
        meta: {
          source: "operator-shell-api-envelope",
          dataMode: "unavailable",
          dataOrigin: { kind: "unavailable" },
          liveReadiness: { ready: false },
        },
      }).status,
    ).toBe("seed");
  });

  it("fails closed for incomplete or nested seed business payloads", () => {
    expect(
      inspectOperatorShellPayload({
        ...livePayload,
        today: { kpis: [{ label: "Incomplete KPI", value: "1" }] },
      }).status,
    ).toBe("empty");
    expect(
      inspectOperatorShellPayload({
        ...livePayload,
        today: {
          ...livePayload.today,
          kpis: [{ label: "Mock KPI", value: "999", source: "fixture-replay" }],
        },
      }).status,
    ).toBe("seed");
  });

  it("normalizes fixture status to a blocked seed gate", () => {
    expect(toUnavailableOperatorStatus("fixture")).toBe("seed");
    expect(toUnavailableOperatorStatus("error")).toBe("error");
  });
});
