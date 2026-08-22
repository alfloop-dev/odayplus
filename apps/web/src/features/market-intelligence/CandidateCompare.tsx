import React from 'react';
import { MarketIntelligenceClient } from '../../api/generated/market-intelligence/client';

export async function CandidateCompare({ siteIds }: { siteIds: string[] }) {
  try {
    const compare = await MarketIntelligenceClient.compareCandidates(siteIds);
    if (!compare) {
      return <div data-testid="candidate-compare-empty">No comparison</div>;
    }
    return (
      <div data-testid="candidate-compare">
        <h2>Candidate Compare</h2>
        <div data-testid="compare-results">
          {Object.entries(compare.candidates ?? {}).map(([id, cand]: [string, any]) => (
             <div key={id} data-testid={`cand-${id}`}>
               <span data-testid="readiness">{cand.readiness ?? "Missing"}</span>
               <span data-testid="missing-domains">{cand.missing_domains_by_candidate ? Object.keys(cand.missing_domains_by_candidate).length : "Missing"}</span>
             </div>
          ))}
        </div>
      </div>
    );
  } catch (error: any) {
    return <div data-testid="candidate-compare-error">{error.message}</div>;
  }
}
