# ODP-OC-R4-006 Implementation Receipt

Task: ODP-OC-R4-006
Owner: Claude2
Reviewer: Antigravity5
Status: review

## Delivered Implementation

This task productizes the Network Workspace Candidate, SiteScore Lab, and Compare tab surfaces, backed by the new `/operator/network-scoring` sub-router:

- **Candidate Data Gate**: Implemented a server-side and client-side data completeness gate enforcing six required dimensions (address, geocode, rent, area, floor, hard-rule). Geocode is required to have confidence >= 0.80. If any dimension is missing or fails, the gate blocks scoring, disabling the "執行 SiteScore" action. CS-1003 is gate-blocked due to geocode confidence 0.71.
- **Re-runnable SiteScore Job**: Integrated single-candidate and batch scoring jobs that persist deterministic scorecards. Re-runs are idempotent. Batch scoring sorts persisted results and safely skips gated candidates.
- **R4 Scorecard**: Renders complete scorecards with M1/M3/M6/M12 monthly revenue paths, P10/P50/P90 valuation bands, six risk-breakdown sub-scores (rent reasonableness, cannibalization, competition, demand, POI fit, access), support reasons, primary risks, and recommendation-specific conditions/reject reasons.
- **Compare Recommendation Panel**: Derives recommendation basket consistently (primary recommendation, alternate, and avoid) based on score-sorted results.
- **Wiring**: Connected the `CandidatePanel`, `SiteScorePanel`, and `ComparePanel` into the NetworkFindAreasWorkspace tabs.

## Owned Layer

- `apps/web/features/operator/network/CandidatePanel.tsx`
- `apps/web/features/operator/network/SiteScorePanel.tsx`
- `apps/web/features/operator/network/ComparePanel.tsx`
- `apps/web/features/operator/network/networkScoringTypes.ts`
- `apps/api/app/routes/operator_modules/network_scoring.py`
- `modules/opsboard/application/network_scoring.py`
- `tests/e2e/operator-network-scoring.spec.ts`
- `tests/contract/test_operator_network_scoring_api.py`

## Not Changing

- Standalone expansion map routes or existing listing radars.
- Governance, AVM, NetPlan, or low-efficiency store rebalancing workflows.

## Evidence Files

- `api-proof.json` captures initial, intermediate, and final states of Candidate, SiteScore, and Compare sets.
- `screenshot-manifest.json` tracks viewport settings, URLs, design labels, and screenshot files.
- `screenshots/` directory holds desktop and constrained captures for all three productized tabs under both local app and design console.
