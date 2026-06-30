# ODP-EXT-001/002/003 Worker Evidence

Recorded: 2026-06-29
Worker lane: External provider foundation
Scope: repo-side implementation, tests, and evidence for provider registry/secrets, live listing adapter, and live geocoder adapter.

## Release Authority

Current release authority must be re-checked from PR #82 before promotion. This
worker handback originally observed:

- `gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url`
- original handback headRefOid: `1494e51f7c90a35abbbc1b9feec6bb2dbb8d5633`
- state: `OPEN`
- isDraft: `true`
- mergeable: `MERGEABLE`
- GitHub returned successful attached checks for PR #82 at command time.

Authority refresh procedure:

- Before promotion, run
  `gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url`
  and use the returned `headRefOid` plus attached checks as the current release
  authority.
- A sample refresh was recorded on 2026-06-30 before PR #128 merged. Because
  evidence-only merges move PR #82, do not treat that sample SHA as current.

## ODP-EXT-001 Provider Registry And Secrets

Status: repo-side complete; provider-specific production credential/OAuth proof remains externally blocked.

Implementation evidence:

- Provider registry: `modules/external_data/connectors/provider_registry.py`
- Registered provider classes cover listing, POI, geocode, admin boundary, and competitor/manual sources.
- Secret inventory records names only:
- `ODP_LISTING_PROVIDER_API_KEY`
- `ODP_POI_PROVIDER_API_KEY`
- `ODP_GEOCODE_PROVIDER_API_KEY`
- `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN`
- `ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION`
- Auth modes are metadata-only and include API key, bearer token, and manual attestation.
- Startup validation fails closed in live mode when credentials are missing, placeholder, expired, unauthorized, revoked, or invalid.
- Tests assert the rendered secret inventory does not contain committed mock secret values.

Verification evidence:

- `uv run pytest tests/e2e/test_external_source_product_e2e.py -k "license_gate_and_fixture_default" -q`
- Result: `1 passed`
- `uv run pytest tests/data/test_geo_pipeline.py tests/e2e/test_external_source_product_e2e.py -q`
- Result: `15 passed`

## ODP-EXT-002 Live Listing Feed Adapter

Status: repo-side complete; production listing provider proof remains externally blocked.

Implementation evidence:

- Listing adapter: `modules/external_data/providers/live.py` as `ListingPartnerFeedProvider`
- Authenticated HTTP provider boundary sends `X-API-Key` and `X-Correlation-Id` through `HttpListingFeedClient`.
- Fixture/source-stub replay remains the default when live mode is absent.
- Raw landing snapshot is represented by `RawListingSnapshot`.
- Canonical transform is represented by `CanonicalListingSnapshot` plus `ListingConnector`.
- Idempotency keys use `provider_id:snapshot_id:source_listing_id`.
- Duplicate records and malformed records enter connector quarantine with contract issue codes.
- Scheduler integration records source snapshot ids, provider observed time, ingestion time, correlation id, alerts, audit events, and blocked/fresh/stale status.

Verification evidence:

- `uv run pytest tests/e2e/test_external_source_product_e2e.py -k "live_provider_mode_product_e2e" -q`
- Result: `1 passed`
- `uv run pytest tests/data/test_geo_pipeline.py tests/e2e/test_external_source_product_e2e.py -q`
- Result: `15 passed`

Contract coverage:

- Approved mock success path persists raw and canonical snapshot ids plus lineage evidence.
- Duplicate listing feed records quarantine with `duplicate_idempotency_key`.
- Malformed listing feed records quarantine with `missing_required_field`.
- Unauthorized mock provider returns product-visible `BLOCKED` state with correlation id.
- Timeout simulation returns product-visible `BLOCKED` state with correlation id.
- Fixture replay remains the default CI path.

## ODP-EXT-003 Live Geocoder Adapter

Status: repo-side complete; production geocoder proof remains externally blocked.

Implementation evidence:

- Geocoder adapter: `modules/external_data/providers/live.py` as `PrimaryGeocodeProvider`
- Credential handling fails closed in live mode when the geocode API key is missing, placeholder, expired, unauthorized, revoked, or invalid.
- Fixture/source-stub geocoder replay remains the default when live mode is absent.
- Geocode confidence is mapped into `AddressLocation.geocode_confidence` and low-confidence records receive `low_geocode_confidence`.
- Provider request id and provider observed time flow through `GeocodeCandidate` and `GeocodeResult`.
- Retry budget is honored for rate-limit errors in `PrimaryGeocodeProvider.lookup`.
- Recorded response fixture: `tests/fixtures/source_data/external/geocode_primary_api.replay.json`

Verification evidence:

- `uv run pytest tests/data/test_geo_pipeline.py tests/e2e/test_external_source_product_e2e.py -q`
- Result: `15 passed`

Contract coverage:

- Recorded response success test asserts confidence, provider request id, provider observed time, and provider lineage.
- Low-confidence response test asserts confidence mapping and quality flag.
- Rate-limit test asserts a one-retry budget succeeds on the second client call.
- Timeout test asserts fail-closed provider error with correlation id.
- Unauthorized credential status test asserts fail-closed startup/config error with correlation id.

## Remaining External Blockers

- Provider-specific production credentials/OAuth are not present in this workspace.
- No production listing provider raw snapshot was fetched.
- No production geocoder response was fetched.
- Current live-mode proof uses deterministic replay, injected mock clients, and the approved mock HTTP service only.
