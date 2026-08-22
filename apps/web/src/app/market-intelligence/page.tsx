import React from "react";

import type { CoverageQuery } from "../../api/generated/market-intelligence/client";
import {
  CandidateCompare,
  MarketExplorer,
  SiteDossier,
} from "../../features/market-intelligence";

type SearchParams = Record<string, string | string[] | undefined>;

function first(params: SearchParams, name: string): string | undefined {
  const value = params[name];
  return Array.isArray(value) ? value[0] : value;
}

function positiveInteger(value: string | undefined): number | undefined {
  if (!value) return undefined;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

type PageProps = {
  searchParams?: Promise<SearchParams>;
};

export default async function MarketIntelligencePage({ searchParams }: PageProps) {
  const params = (await searchParams) ?? {};
  const siteId = first(params, "site_id");
  const siteIds = (first(params, "site_ids") ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const filters: CoverageQuery = {
    surface_id: first(params, "surface_id"),
    admin_code: first(params, "admin_code"),
    h3_index: first(params, "h3_index"),
    business_date: first(params, "business_date"),
    readiness: first(params, "readiness"),
    state: first(params, "state"),
    limit: positiveInteger(first(params, "limit")),
  };

  return (
    <main>
      <h1>Market Intelligence</h1>
      <MarketExplorer filters={filters} />
      {siteId ? <SiteDossier siteId={siteId} /> : <p data-testid="site-dossier-prompt">Choose a site.</p>}
      {siteIds.length ? (
        <CandidateCompare siteIds={siteIds} />
      ) : (
        <p data-testid="candidate-compare-prompt">Choose candidates.</p>
      )}
    </main>
  );
}
