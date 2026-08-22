import React from "react";

import type { CoverageQuery } from "../../api/generated/market-intelligence/client";
import { CandidateCompare } from "./CandidateCompare";
import { MarketExplorer } from "./MarketExplorer";
import { SiteDossier } from "./SiteDossier";

export type MarketIntelligenceSearchParams = Record<
  string,
  string | string[] | undefined
>;

function first(
  params: MarketIntelligenceSearchParams,
  name: string,
): string | undefined {
  const value = params[name];
  return Array.isArray(value) ? value[0] : value;
}

function positiveInteger(value: string | undefined): number | undefined {
  if (!value) return undefined;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

export function shouldShowMarketIntelligence(
  params: MarketIntelligenceSearchParams,
): boolean {
  return (
    first(params, "ws") === "network" &&
    first(params, "panel") === "market-intelligence"
  );
}

export function MarketIntelligencePanel({
  searchParams,
}: {
  searchParams: MarketIntelligenceSearchParams;
}) {
  const siteId = first(searchParams, "site_id");
  const siteIds = (first(searchParams, "site_ids") ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const filters: CoverageQuery = {
    surface_id: first(searchParams, "surface_id"),
    admin_code: first(searchParams, "admin_code"),
    h3_index: first(searchParams, "h3_index"),
    business_date: first(searchParams, "business_date"),
    readiness: first(searchParams, "readiness"),
    state: first(searchParams, "state"),
    limit: positiveInteger(first(searchParams, "limit")),
  };

  return (
    <section aria-labelledby="market-intelligence-heading" data-testid="market-intelligence-panel">
      <h1 id="market-intelligence-heading">Market Intelligence</h1>
      <MarketExplorer filters={filters} />
      {siteId ? (
        <SiteDossier siteId={siteId} />
      ) : (
        <p data-testid="site-dossier-prompt">Choose a site.</p>
      )}
      {siteIds.length ? (
        <CandidateCompare siteIds={siteIds} />
      ) : (
        <p data-testid="candidate-compare-prompt">Choose candidates.</p>
      )}
    </section>
  );
}
