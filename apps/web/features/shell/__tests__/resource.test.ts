import { describe, expect, it } from "vitest";
import type { OdpApiClient } from "@oday-plus/openapi-client";
import { loadApiResource } from "../resource";

const client = { baseUrl: "https://api.example.test" } as OdpApiClient;

describe("loadApiResource", () => {
  it.each([null, undefined, [], {}])(
    "fails closed for an empty API resource: %j",
    async (payload) => {
      const resource = await loadApiResource({
        client,
        fetcher: async () => payload,
      });

      expect(resource).toMatchObject({
        state: "error",
        source: "none",
        data: null,
        error: "LIVE_DATA_EMPTY",
      });
    },
  );

  it("blocks nested seed data instead of exposing it as a ready shell resource", async () => {
    const resource = await loadApiResource({
      client,
      fetcher: async () => ({
        meta: { source: "operator-shell-production", dataMode: "live" },
        status: { openTasks: 4 },
        tasks: [{ id: "task-1", provenance: { source: "fixture-replay" } }],
      }),
    });

    expect(resource).toMatchObject({
      state: "error",
      source: "none",
      data: null,
      error: "NON_PRODUCTION_DATA_BLOCKED",
    });
  });

  it("returns an unconfigured state when no API client exists", async () => {
    const resource = await loadApiResource({
      client: null,
      fetcher: async () => ({ value: "unused" }),
    });

    expect(resource).toMatchObject({
      state: "unconfigured",
      source: "none",
      data: null,
    });
  });
});
