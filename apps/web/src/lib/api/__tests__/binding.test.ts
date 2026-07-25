import { describe, expect, it } from "vitest";
import type { OdpApiClient } from "@oday-plus/openapi-client";
import { loadApiBinding } from "../binding";

const client = { baseUrl: "https://api.example.test" } as OdpApiClient;

describe("loadApiBinding", () => {
  it("never labels unconfigured, empty, or failed bindings as API data", async () => {
    const unconfigured = await loadApiBinding({
      client: null,
      fetcher: async () => ["unused"],
    });
    const empty = await loadApiBinding<string>({
      client,
      fetcher: async () => [],
    });
    const failed = await loadApiBinding<string>({
      client,
      fetcher: async () => {
        throw new Error("offline");
      },
    });

    expect(unconfigured).toMatchObject({ state: "unconfigured", source: "unavailable", items: [] });
    expect(empty).toMatchObject({ state: "empty", source: "unavailable", items: [] });
    expect(failed).toMatchObject({ state: "error", source: "unavailable", items: [] });
  });

  it("labels only rendered API rows as API data", async () => {
    const ready = await loadApiBinding<string>({
      client,
      fetcher: async () => ["live"],
    });

    expect(ready).toMatchObject({ state: "ready", source: "api", items: ["live"] });
  });

  it.each([
    [{ source: "fixture-replay", value: 1 }],
    [{ meta: { dataMode: "mock" }, value: 1 }],
    [{ rows: [{ provenance: { origin: "seed-r4" } }] }],
    [{ _meta: { description: "prototype fixture payload" } }],
    [{ nested: { isFixture: true } }],
  ])("blocks nested non-production payloads: %j", async (row) => {
    const result = await loadApiBinding({
      client,
      fetcher: async () => [row],
    });

    expect(result).toMatchObject({
      state: "error",
      source: "unavailable",
      items: [],
      error: "NON_PRODUCTION_DATA_BLOCKED",
    });
  });

  it("does not reject a legitimate demographics source", async () => {
    const row = { source: "demographics-provider", value: 1 };
    const result = await loadApiBinding({
      client,
      fetcher: async () => [row],
    });

    expect(result).toMatchObject({
      state: "ready",
      source: "api",
      items: [row],
    });
  });
});
