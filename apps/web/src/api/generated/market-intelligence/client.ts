import { headers } from "next/headers";

import type { paths } from "./types";

export type CoverageQuery = NonNullable<
  paths["/market-intelligence/coverage"]["get"]["parameters"]["query"]
>;

type QueryValue = string | number | boolean | null | undefined;

function queryString(params: object): string {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(params) as [string, QueryValue][]) {
    if (value !== undefined && value !== null && value !== "") {
      query.set(name, String(value));
    }
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T | null> {
  const baseUrl = process.env.ODP_API_BASE_URL || "http://127.0.0.1:8099";
  const reqHeaders = await headers();
  const forwardHeaders: Record<string, string> = {};
  for (const name of [
    "x-correlation-id",
    "x-operator-role",
    "x-roles",
    "x-subject-id",
    "x-tenant-id",
  ]) {
    const val = reqHeaders.get(name);
    if (val) forwardHeaders[name] = val;
  }

  const res = await fetch(`${baseUrl}/api/v1${path}`, {
    ...options,
    headers: {
      ...forwardHeaders,
      ...options?.headers,
    },
    cache: "no-store",
  });

  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const MarketIntelligenceClient = {
  async getMarketCellProfile(cell_id: string) {
    type Res = paths["/market-intelligence/cells/{cell_id}"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/cells/${encodeURIComponent(cell_id)}`);
  },
  async getSiteMarketContext(site_id: string) {
    type Res = paths["/market-intelligence/sites/{site_id}/context"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/sites/${encodeURIComponent(site_id)}/context`);
  },
  async compareCandidates(site_ids: string[]) {
    type Res = paths["/market-intelligence/compare"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(
      `/market-intelligence/compare${queryString({ site_ids: site_ids.join(",") })}`,
    );
  },
  async getCellEvidence(cell_id: string) {
    type Res = paths["/market-intelligence/evidence/cells/{cell_id}"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/evidence/cells/${encodeURIComponent(cell_id)}`);
  },
  async getSiteEvidence(site_id: string) {
    type Res = paths["/market-intelligence/evidence/{site_id}"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/evidence/${encodeURIComponent(site_id)}`);
  },
  async getCoverageSurface(params: CoverageQuery = {}) {
    type Res = paths["/market-intelligence/coverage"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/coverage${queryString(params)}`);
  },
};
