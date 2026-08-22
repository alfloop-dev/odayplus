import React from 'react';
import { MarketExplorer, SiteDossier, CandidateCompare } from '../../features/market-intelligence';

export default function MarketIntelligencePage() {
  return (
    <main>
      <h1>Market Intelligence</h1>
      <MarketExplorer />
      <SiteDossier siteId="site-1" />
      <CandidateCompare siteIds={["site-1", "site-2"]} />
    </main>
  );
}
