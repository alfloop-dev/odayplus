import React from "react";

import { MarketIntelligenceClient } from "../../api/generated/market-intelligence/client";
import { displayValue } from "./presentation";

export async function SiteDossier({ siteId }: { siteId: string }) {
  try {
    const evidence = await MarketIntelligenceClient.getSiteEvidence(siteId);
    if (!evidence) {
      return <div data-testid="site-dossier-empty">No evidence</div>;
    }
    return (
      <section data-testid="site-dossier">
        <h2>Site Dossier {siteId}</h2>
        <div data-testid="evidence">
          <span data-testid="overall-confidence">{displayValue(evidence.overall_confidence_pct)}</span>{" "}
          <span data-testid="period-grain">{displayValue(evidence.period_grain)}</span>
        </div>
        <ul data-testid="evidence-domains">
          {Object.entries(evidence.domains).map(([domain, item]) => (
            <li key={domain}>
              <strong>{domain}</strong>{" "}
              <span>{displayValue(item.status)}</span>{" "}
              <span>{displayValue(item.freshness_state)}</span>{" "}
              <span>{displayValue(item.confidence_pct)}</span>
            </li>
          ))}
        </ul>
      </section>
    );
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return <div data-testid="site-dossier-error">{message}</div>;
  }
}
