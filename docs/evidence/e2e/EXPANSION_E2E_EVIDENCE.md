# Expansion Product E2E Evidence

ODP-PV-005 adds `tests/e2e/e2e-expansion-product.spec.ts`, a product-level flow for:

- HeatZone scoring and map/list synchronization.
- Listing import with accepted, duplicate, and hard-rule-failed records.
- Candidate conversion through the listing API.
- SiteScore scoring, return for revision, re-score, approval guard, final approval, and realization.
- Audit correlation through `corr-pv005-expansion-product`.
- UI evidence panels and decision separation screenshots attached to the Playwright run.

## Executable Coverage

The spec writes live backend state through:

- `POST /heatzones/score-jobs`
- `POST /listings/import-jobs`
- `GET /listings/candidates`
- `POST /sitescore/score-jobs`
- `POST /sitescore/decisions`
- `POST /sitescore/decisions/{decision_id}/decision`
- `GET /sitescore/reports/{candidate_site_id}`
- `GET /sitescore/realized`
- `GET /audit/events?correlation_id=corr-pv005-expansion-product`

The UI portion verifies:

- HeatZone selected map state and ranked-list deep link sync.
- Listing drawer field lineage and correlation id display.
- Candidate Site evidence/readiness surface.
- SiteScore evidence panel and non-optimistic approval panel.

## Verification Commands

```bash
npm run typecheck --workspace=@oday-plus/web
npx playwright test tests/e2e/e2e-expansion-product.spec.ts --project=chromium
npx playwright test tests/e2e/e2e-expansion-product.spec.ts tests/e2e/e2e-map.spec.ts --project=chromium
```

The deterministic E2E environment from ODP-PV-004 can run the same spec by setting `ODP_PLAYWRIGHT_REUSE_EXISTING=1` and `ODP_API_BASE_URL` to the compose API URL.
