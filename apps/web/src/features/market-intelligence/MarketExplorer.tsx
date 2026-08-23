import React from "react";

import {
  MarketIntelligenceClient,
  type CoverageQuery,
} from "./api";
import { asRecord, displayValue } from "./presentation";

export async function MarketExplorer({ filters = {} }: { filters?: CoverageQuery }) {
  try {
    const coverage = await MarketIntelligenceClient.getCoverageSurface(filters);
    if (!coverage || coverage.cells.length === 0) {
      return <div data-testid="market-explorer-empty">No cells found</div>;
    }

    return (
      <section data-testid="market-explorer">
        <h2>Market Explorer</h2>
        <p>
          Surface readiness: <strong data-testid="surface-readiness">{displayValue(coverage.readiness)}</strong>
        </p>
        <ul data-testid="coverage">
          {coverage.cells.map((rawCell, index) => {
            const cell = asRecord(rawCell);
            const identity = cell.h3_index ?? cell.cell_id;
            const key = displayValue(identity) === "Missing" ? `cell-${index}` : String(identity);
            return (
              <li key={key} data-testid={`cell-${key}`}>
                <span data-testid={`cell-identity-${key}`}>{displayValue(identity)}</span>{" "}
                <span data-testid={`readiness-${key}`}>{displayValue(cell.readiness)}</span>{" "}
                <span data-testid={`coverage-state-${key}`}>{displayValue(cell.state)}</span>{" "}
                <span data-testid={`observed-count-${key}`}>{displayValue(cell.observed_count)}</span>
              </li>
            );
          })}
        </ul>
      </section>
    );
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return <div data-testid="market-explorer-error">{message}</div>;
  }
}
