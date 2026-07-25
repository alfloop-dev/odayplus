# External Provider Live-Required Reconciliation

- doc_id: ODP-EXT-PROVIDER-RECONCILE-001
- date: 2026-07-25
- status: accepted

## Decision

The standing set of providers that MUST be live-configured before the External
Data Platform runs in production live mode is reconciled to the three
enrichment/reference providers that have a live upstream today:

- `geocode.primary_api`
- `poi.commercial_api`
- `admin_boundary.official_dataset`

`listing.partner_feed` is **removed from the live-required set** (both
`REQUIRED_PRODUCTION_PROVIDER_IDS` in
`modules/external_data/connectors/provider_registry.py` and
`REQUIRED_PRODUCT_PROVIDER_IDS` in
`scripts/deployment/validate_cloud_run_live_deployment.py`). It is **not deleted**:
`ListingPartnerFeedProvider`, its scheduled-fetch worker, connectivity probe, and
its full test suite remain intact.

## Why

Listings enter ODay Plus through two channels:

1. **Assisted Listing Intake (live, in use).** An internal user submits one
   listing URL. For sources whose Terms of Service forbid server-side retrieval
   (591, 樂屋網, 好房網 — see `SOURCE_REGISTRY` in
   `modules/external_data/application/assisted_intake.py`), the system keeps the
   URL as provenance and the user manually enters the fields. This path is
   governed by the assisted-intake subsystem's own release/privacy/workflow
   gates and is live (durable Postgres + geocode enrichment).

2. **Bulk partner feed (built, not contracted).** `listing.partner_feed` is a
   fully implemented, tested channel for a licensed data partner that supplies
   listings in bulk via API. It requires a signed partner (a real feed endpoint
   plus credentials) that does not exist today.

Requiring a *live* endpoint for an uncontracted partner would block production
live mode indefinitely on a business dependency that the product does not
currently rely on. Removing it from the standing live-required set makes the gate
reflect the real product: listings are sourced live via assisted intake; the bulk
feed is a ready capability that is gated back in the moment a licensed partner is
configured (add its id to `ODP_PRODUCTION_PROVIDER_IDS` and the required set).

This is a reconciliation to reality, not a relaxation of coverage: the assisted
intake path retains its own gates, and the partner-feed adapter retains its own
tests.
