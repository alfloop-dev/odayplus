import React from 'react';
import { MarketIntelligenceClient } from '../../api/generated/market-intelligence/client';

export async function MarketExplorer({ searchParams }: { searchParams?: { min_lat?: string, min_lng?: string, max_lat?: string, max_lng?: string } }) {
  try {
    const min_lat = Number(searchParams?.min_lat ?? 0);
    const min_lng = Number(searchParams?.min_lng ?? 0);
    const max_lat = Number(searchParams?.max_lat ?? 0);
    const max_lng = Number(searchParams?.max_lng ?? 0);

    const coverage = await MarketIntelligenceClient.getCoverageSurface({ min_lat, min_lng, max_lat, max_lng });
    if (!coverage || !coverage.cells) {
      return <div data-testid="market-explorer-empty">No cells found</div>;
    }

    return (
      <div data-testid="market-explorer">
        <h2>Market Explorer</h2>
        <div data-testid="coverage">
          {coverage.cells.map((cell: any) => (
            <div key={cell.h3_index} data-testid={`cell-${cell.h3_index}`}>
              <span>{cell.h3_index}</span>
              <span data-testid="readiness">{cell.readiness ?? "Missing"}</span>
              <span data-testid="uncertainty">{cell.state?.current_uncertainty_pct ?? "Missing"}</span>
              <span data-testid="coverage-state">{cell.state?.overall_coverage_pct ?? "Missing"}</span>
            </div>
          ))}
        </div>
      </div>
    );
  } catch (error: any) {
    return <div data-testid="market-explorer-error">{error.message}</div>;
  }
}
