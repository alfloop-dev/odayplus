import React from 'react';
import { MarketIntelligenceClient } from '../../api/generated/market-intelligence/client';

export async function SiteDossier({ siteId }: { siteId: string }) {
  try {
    const evidence = await MarketIntelligenceClient.getSiteEvidence(siteId);
    if (!evidence) {
      return <div data-testid="site-dossier-empty">No evidence</div>;
    }
    return (
      <div data-testid="site-dossier">
        <h2>Site Dossier {siteId}</h2>
        <div data-testid="evidence">
          <span data-testid="overall-confidence">{evidence.overall_confidence_pct ?? "Missing"}</span>
          <span data-testid="period-grain">{evidence.period_grain ?? "Missing"}</span>
        </div>
      </div>
    );
  } catch (error: any) {
    return <div data-testid="site-dossier-error">{error.message}</div>;
  }
}
