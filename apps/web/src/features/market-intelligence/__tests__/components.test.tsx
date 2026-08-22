import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MarketExplorer } from '../MarketExplorer';
import { SiteDossier } from '../SiteDossier';
import { CandidateCompare } from '../CandidateCompare';
import { MarketIntelligenceClient } from '../../../api/generated/market-intelligence/client';

vi.mock('../../../api/generated/market-intelligence/client', () => ({
  MarketIntelligenceClient: {
    getCoverageSurface: vi.fn(),
    getSiteEvidence: vi.fn(),
    compareCandidates: vi.fn(),
  }
}));

describe('Market Intelligence Components', () => {
  it('MarketExplorer handles missing data', async () => {
    vi.mocked(MarketIntelligenceClient.getCoverageSurface).mockResolvedValue({
      cells: [
        { h3_index: '123', readiness: null, state: { current_uncertainty_pct: null, overall_coverage_pct: null } }
      ]
    } as any);

    const jsx = await MarketExplorer({ searchParams: {} });
    render(jsx as any);

    expect(screen.getByTestId('readiness').textContent).toBe('Missing');
    expect(screen.getByTestId('uncertainty').textContent).toBe('Missing');
    expect(screen.getByTestId('coverage-state').textContent).toBe('Missing');
  });

  it('SiteDossier handles missing evidence', async () => {
    vi.mocked(MarketIntelligenceClient.getSiteEvidence).mockResolvedValue({
      site_id: 'site-1',
      overall_confidence_pct: null,
      period_grain: null,
    } as any);

    const jsx = await SiteDossier({ siteId: 'site-1' });
    render(jsx as any);

    expect(screen.getByTestId('overall-confidence').textContent).toBe('Missing');
    expect(screen.getByTestId('period-grain').textContent).toBe('Missing');
  });

  it('CandidateCompare handles missing readiness', async () => {
    vi.mocked(MarketIntelligenceClient.compareCandidates).mockResolvedValue({
      candidates: {
        'site-1': { readiness: null, missing_domains_by_candidate: null }
      }
    } as any);

    const jsx = await CandidateCompare({ siteIds: ['site-1'] });
    render(jsx as any);

    expect(screen.getByTestId('readiness').textContent).toBe('Missing');
    expect(screen.getByTestId('missing-domains').textContent).toBe('Missing');
  });
});
