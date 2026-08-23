import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MarketIntelligenceClient } from "../api";
import { CandidateCompare } from "../CandidateCompare";
import { MarketExplorer } from "../MarketExplorer";
import { SiteDossier } from "../SiteDossier";

vi.mock('../api', () => ({
  MarketIntelligenceClient: {
    getCoverageSurface: vi.fn(),
    getSiteEvidence: vi.fn(),
    compareCandidates: vi.fn(),
  }
}));

describe('Market Intelligence Components', () => {
  it('MarketExplorer handles missing data', async () => {
    vi.mocked(MarketIntelligenceClient.getCoverageSurface).mockResolvedValue({
      surface_id: "surface-1",
      domain: "listing",
      readiness: "ready",
      cells: [
        { cell_id: "cell-123", h3_index: "123", readiness: null, state: null },
      ],
    } as never);

    const jsx = await MarketExplorer({ filters: {} });
    render(jsx);

    expect(screen.getByTestId("surface-readiness").textContent).toBe("ready");
    expect(screen.getByTestId("readiness").textContent).toBe("Missing");
    expect(screen.getByTestId("coverage-state").textContent).toBe("Missing");
  });

  it('SiteDossier handles missing evidence', async () => {
    vi.mocked(MarketIntelligenceClient.getSiteEvidence).mockResolvedValue({
      site_id: "site-1",
      overall_confidence_pct: null,
      period_grain: null,
      domains: {},
    } as never);

    const jsx = await SiteDossier({ siteId: "site-1" });
    render(jsx);

    expect(screen.getByTestId('overall-confidence').textContent).toBe('Missing');
    expect(screen.getByTestId('period-grain').textContent).toBe('Missing');
  });

  it('CandidateCompare handles missing readiness', async () => {
    vi.mocked(MarketIntelligenceClient.compareCandidates).mockResolvedValue({
      candidates: [{ site_id: "site-1", uncertainty_pct: null }],
      readiness_breakdown: { "site-1": null },
      missing_domains_by_candidate: { "site-1": null },
    } as never);

    const jsx = await CandidateCompare({ siteIds: ["site-1"] });
    render(jsx);

    expect(screen.getByTestId("readiness").textContent).toBe("Missing");
    expect(screen.getByTestId("uncertainty").textContent).toBe("Missing");
    expect(screen.getByTestId("missing-domains").textContent).toBe("Missing");
  });
});
