import type { paths } from "./types";
import { headers } from "next/headers";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  let baseUrl = process.env.ODP_API_BASE_URL || "http://127.0.0.1:8099";
  
  const reqHeaders = await headers();
  const forwardHeaders: Record<string, string> = {};
  for (const name of ["x-correlation-id", "x-operator-role", "x-roles", "x-subject-id", "x-tenant-id"]) {
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
    // Return null on 404 or throw error
    if (res.status === 404) return null as any;
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const MarketIntelligenceClient = {
  async getMarketCellProfile(cell_id: string) {
    type Res = paths["/market-intelligence/cells/{cell_id}"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/cells/${cell_id}`);
  },
  async getSiteMarketContext(site_id: string) {
    type Res = paths["/market-intelligence/sites/{site_id}/context"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/sites/${site_id}/context`);
  },
  async compareCandidates(site_ids: string[]) {
    type Res = paths["/market-intelligence/compare"]["get"]["responses"]["200"]["content"]["application/json"];
    const query = site_ids.map(id => `site_ids=${encodeURIComponent(id)}`).join("&");
    return fetchApi<Res>(`/market-intelligence/compare?${query}`);
  },
  async getCellEvidence(cell_id: string) {
    type Res = paths["/market-intelligence/evidence/cells/{cell_id}"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/evidence/cells/${cell_id}`);
  },
  async getSiteEvidence(site_id: string) {
    type Res = paths["/market-intelligence/evidence/{site_id}"]["get"]["responses"]["200"]["content"]["application/json"];
    return fetchApi<Res>(`/market-intelligence/evidence/${site_id}`);
  },
  async getCoverageSurface(params: { min_lat: number, min_lng: number, max_lat: number, max_lng: number }) {
    type Res = paths["/market-intelligence/coverage"]["get"]["responses"]["200"]["content"]["application/json"];
    const query = new URLSearchParams(params as any).toString();
    return fetchApi<Res>(`/market-intelligence/coverage?${query}`);
  },
};
