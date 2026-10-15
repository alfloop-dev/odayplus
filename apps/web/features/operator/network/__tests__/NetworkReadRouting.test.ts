import { describe, expect, it } from "vitest";
import { OdpApiClient } from "@oday-plus/openapi-client";
import { loadNetworkFindAreasBindings } from "../networkFindAreasLoader";

const origin = "https://console.example.invalid";
const identity = { "x-tenant-id": "tenant-scoped-read", "x-operator-role": "expansion-manager" };
const candidateId = "00000000-0000-0000-0000-000000000201";

function webClient(status = 200, empty = false) {
  const requests: Array<{ path: string; headers: Headers }> = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    requests.push({ path: url.pathname + url.search, headers: new Headers(init?.headers) });
    // The Web host exposes the authenticated BFF at /api/v1, with no
    // unversioned domain routes. This reproduces the actual routing boundary.
    if (!url.pathname.startsWith("/api/v1/")) {
      return Response.json({ detail: "Not Found" }, { status: 404 });
    }
    if (status !== 200) return Response.json({ detail: "Domain read denied" }, { status });
    if (url.pathname === "/api/v1/heatzones") {
      return Response.json({ items: empty ? [] : [{ h3_index: "8928308280fffff", confidence: 0.8 }], count: empty ? 0 : 1 });
    }
    if (url.pathname === "/api/v1/listings/candidates") {
      return Response.json({ candidates: empty ? [] : [{ candidate_site_id: candidateId, siteStatus: "new" }] });
    }
    if (url.pathname === "/api/v1/sitescore/reports") return Response.json({ items: [], count: 0 });
    throw new Error(`Unexpected endpoint ${url.pathname}`);
  };
  return { client: new OdpApiClient({ baseUrl: origin, defaultHeaders: identity, fetchImpl }), requests };
}

describe("Network domain reads through the same-origin Web BFF", () => {
  it.each([
    ["heatzones", "/api/v1/heatzones?limit=5"],
    ["candidates", "/api/v1/listings/candidates"],
    ["reports", "/api/v1/sitescore/reports"],
  ])("routes %s to the versioned API and keeps the caller's identity", async (method, path) => {
    const { client, requests } = webClient();
    if (method === "heatzones") await client.listHeatzones({ limit: 5 });
    else if (method === "candidates") await client.listCandidates();
    else await client.listSiteScoreReports();
    expect(requests.map((request) => request.path)).toEqual([path]);
    for (const [name, value] of Object.entries(identity)) expect(requests[0]?.headers.get(name)).toBe(value);
  });

  it("loads nonempty authorized bindings and enrichment through the existing BFF", async () => {
    const { client, requests } = webClient();
    const result = await loadNetworkFindAreasBindings(client);
    expect(result.heatZones.state).toBe("ready");
    expect(result.candidates.state).toBe("ready");
    expect(result.candidates.source).toBe("api");
    expect(result.candidates.items[0]?.id).toBe(candidateId);
    expect(result.candidates.items[0]?.score).toBeNull();
    expect(requests.map((request) => request.path)).toEqual([
      "/api/v1/heatzones", "/api/v1/listings/candidates", "/api/v1/sitescore/reports",
    ]);
  });

  it("keeps a real domain denial unavailable without changing identity or retrying an alias", async () => {
    const { client, requests } = webClient(403);
    const result = await loadNetworkFindAreasBindings(client);
    expect(result.heatZones.state).toBe("error");
    expect(result.candidates.state).toBe("error");
    expect(result.candidates.source).toBe("unavailable");
    expect(result.candidates.items).toEqual([]);
    expect(requests.map((request) => request.path)).toEqual(["/api/v1/heatzones", "/api/v1/listings/candidates"]);
    expect(requests.every((request) => request.headers.get("x-tenant-id") === identity["x-tenant-id"])).toBe(true);
  });

  it("preserves successful empty results and does not request enrichment for absent candidates", async () => {
    const { client, requests } = webClient(200, true);
    const result = await loadNetworkFindAreasBindings(client);
    expect(result.heatZones.state).toBe("empty");
    expect(result.candidates.state).toBe("empty");
    expect(result.candidates.items).toEqual([]);
    expect(requests.map((request) => request.path)).toEqual(["/api/v1/heatzones", "/api/v1/listings/candidates"]);
  });
});
