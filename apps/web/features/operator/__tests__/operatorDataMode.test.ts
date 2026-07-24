import { describe, expect, it } from "vitest";
import {
  inspectOperatorShellPayload,
  isOperatorProductionMode,
  isSeedDataSource,
  operatorFixturesAllowed,
  toUnavailableOperatorStatus,
} from "../operatorDataMode";

const livePayload = {
  meta: { source: "operator-shell-production" },
  navigation: { workspaces: [{ id: "today" }] },
  today: { kpis: [{ label: "Live KPI", value: "1" }] },
};

describe("operator data mode", () => {
  it("allows fixtures only outside production", () => {
    expect(operatorFixturesAllowed({ nodeEnv: "test" })).toBe(true);
    expect(operatorFixturesAllowed({ nodeEnv: "production" })).toBe(false);
    expect(
      operatorFixturesAllowed({
        nodeEnv: "production",
        productMode: "poc",
      }),
    ).toBe(true);
    expect(
      operatorFixturesAllowed({
        nodeEnv: "development",
        productMode: "production",
      }),
    ).toBe(false);
    expect(operatorFixturesAllowed({ nodeEnv: "development", productionMode: "true" })).toBe(false);
    expect(isOperatorProductionMode({ nodeEnv: "production" })).toBe(true);
  });

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
    ).toBe("ready");
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
    ).toBe("empty");
  });

  it("normalizes fixture status to a blocked seed gate", () => {
    expect(toUnavailableOperatorStatus("fixture")).toBe("seed");
    expect(toUnavailableOperatorStatus("error")).toBe("error");
  });
});
