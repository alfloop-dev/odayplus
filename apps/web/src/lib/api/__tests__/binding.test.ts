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

    expect(unconfigured).toMatchObject({ state: "unconfigured", source: "fixture", items: [] });
    expect(empty).toMatchObject({ state: "empty", source: "fixture", items: [] });
    expect(failed).toMatchObject({ state: "error", source: "fixture", items: [] });
  });

  it("labels only rendered API rows as API data", async () => {
    const ready = await loadApiBinding<string>({
      client,
      fetcher: async () => ["live"],
    });

    expect(ready).toMatchObject({ state: "ready", source: "api", items: ["live"] });
  });
});
