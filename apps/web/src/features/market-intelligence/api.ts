import { getServerApiClient } from "../../lib/api/client";
import type { components, paths } from "../../api/generated/market-intelligence/types";

export type CoverageQuery = NonNullable<
  paths["/market-intelligence/coverage"]["get"]["parameters"]["query"]
>;

export type MarketCellProfile = components["schemas"]["MarketCellProfile"];
export type SiteMarketContext = components["schemas"]["SiteMarketContext"];
export type CandidateCompareResult = components["schemas"]["CandidateCompareResult"];
export type CellEvidenceChain = components["schemas"]["CellEvidenceChain"];
export type SiteEvidenceChain = components["schemas"]["SiteEvidenceChain"];
export type CoverageSurface = components["schemas"]["CoverageSurface"];

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
  const client = await getServerApiClient();
  if (!client) {
    return null;
  }
  try {
    return await client.request<T>(`/api/v1${path}`, options);
  } catch (error: any) {
    if (error.status === 404) return null;
    throw error;
  }
}

export const MarketIntelligenceClient = {
  async getMarketCellProfile(cell_id: string): Promise<MarketCellProfile | null> {
    return fetchApi<MarketCellProfile>(`/market-intelligence/cells/${encodeURIComponent(cell_id)}`);
  },
  async getSiteMarketContext(site_id: string): Promise<SiteMarketContext | null> {
    return fetchApi<SiteMarketContext>(`/market-intelligence/sites/${encodeURIComponent(site_id)}/context`);
  },
  async compareCandidates(site_ids: string[]): Promise<CandidateCompareResult | null> {
    return fetchApi<CandidateCompareResult>(
      `/market-intelligence/compare${queryString({ site_ids: site_ids.join(",") })}`,
    );
  },
  async getCellEvidence(cell_id: string): Promise<CellEvidenceChain | null> {
    return fetchApi<CellEvidenceChain>(`/market-intelligence/evidence/cells/${encodeURIComponent(cell_id)}`);
  },
  async getSiteEvidence(site_id: string): Promise<SiteEvidenceChain | null> {
    return fetchApi<SiteEvidenceChain>(`/market-intelligence/evidence/${encodeURIComponent(site_id)}`);
  },
  async getCoverageSurface(params: CoverageQuery = {}): Promise<CoverageSurface | null> {
    return fetchApi<CoverageSurface>(`/market-intelligence/coverage${queryString(params)}`);
  },
};
