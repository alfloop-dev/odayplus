import React from "react";

import { MarketIntelligenceClient } from "./api";
import { asRecord, displayValue, stringList } from "./presentation";

export async function CandidateCompare({ siteIds }: { siteIds: string[] }) {
  try {
    const compare = await MarketIntelligenceClient.compareCandidates(siteIds);
    if (!compare) {
      return <div data-testid="candidate-compare-empty">No comparison</div>;
    }

    const readiness = asRecord(compare.readiness_breakdown);
    const missingDomains = asRecord(compare.missing_domains_by_candidate);
    const candidates = compare.candidates.map(asRecord);

    return (
      <section data-testid="candidate-compare">
        <h2>Candidate Compare</h2>
        <ul data-testid="compare-results">
          {siteIds.map((id) => {
            const candidate = candidates.find(
              (item) => item.site_id === id || item.cell_id === id,
            );
            const missing = stringList(missingDomains[id]);
            return (
              <li key={id} data-testid={`cand-${id}`}>
                <strong>{id}</strong>{" "}
                <span data-testid={`readiness-${id}`}>{displayValue(readiness[id])}</span>{" "}
                <span data-testid={`uncertainty-${id}`}>{displayValue(candidate?.uncertainty_pct)}</span>{" "}
                <span data-testid={`missing-domains-${id}`}>
                  {missing === null ? "Missing" : missing.length ? missing.join(", ") : "None"}
                </span>
              </li>
            );
          })}
        </ul>
      </section>
    );
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return <div data-testid="candidate-compare-error">{message}</div>;
  }
}
