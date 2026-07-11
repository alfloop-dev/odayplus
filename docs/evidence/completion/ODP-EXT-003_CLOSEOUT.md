# ODP-EXT-003 Live Geocoder Adapter Closeout Evidence

## Scope

ODP-EXT-003 delivered the live geocoder adapter (`PrimaryGeocodeProvider` in `modules/external_data/providers/live.py`) with complete support for credential handling, confidence mapping, lineage capture, request tracking, and retry budgets.

Key implementation components:
- **Credential Handling**: Fails closed in live mode when the geocode API key is missing, placeholder, expired, unauthorized, revoked, or invalid.
- **Geocode Confidence Mapping**: Maps confidence values into `AddressLocation.geocode_confidence` and flags low-confidence records with `low_geocode_confidence`.
- **Request Tracking**: Captures `provider_request_id` and `provider_observed_at` to flow through `GeocodeCandidate` and `GeocodeResult`.
- **Retry Budget**: PrimaryGeocodeProvider honors the retry budget for rate-limit errors (retrying up to the budget limits).
- **Recorded Response Replay**: Fixture-based geocoding fallback is provided via `tests/fixtures/source_data/external/geocode_primary_api.replay.json`.

## Verification Evidence

Verbose pytest suite run on 2026-07-11 confirms all geocoder contract and integration tests pass successfully:

### 1. Geo Pipeline Contract Verification
`uv run pytest tests/data/test_geo_pipeline.py -v`
```
tests/data/test_geo_pipeline.py::test_address_normalization_geocode_and_h3_are_reproducible PASSED
tests/data/test_geo_pipeline.py::test_geo_pipeline_flags_out_of_market_and_low_confidence PASSED
tests/data/test_geo_pipeline.py::test_geo_pipeline_flags_stale_source_snapshot PASSED
tests/data/test_geo_pipeline.py::test_external_geo_feature_job_rolls_up_poi_competitor_and_listing_snapshots PASSED
tests/data/test_geo_pipeline.py::test_primary_geocoder_replays_recorded_response_with_lineage PASSED
tests/data/test_geo_pipeline.py::test_primary_geocoder_low_confidence_maps_quality_flag_and_lineage PASSED
tests/data/test_geo_pipeline.py::test_primary_geocoder_rate_limit_uses_retry_budget PASSED
tests/data/test_geo_pipeline.py::test_primary_geocoder_timeout_and_unauthorized_fail_closed PASSED
```
*Result*: 8 passed.

### 2. Live Adapter Integration Verification
`uv run pytest tests/integration/test_live_geocode_provider_adapter.py -v`
```
tests/integration/test_live_geocode_provider_adapter.py::test_fixture_replay_is_default_and_preserves_geocode_lineage PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_mocked_live_geocoder_success_uses_registry_metadata_and_redacts_secret PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_missing_live_geocode_credential_fails_closed_without_secret_values PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_expired_live_geocode_credential_status_fails_closed_without_secret_values PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_provider_auth_error_from_live_geocoder_fails_closed_without_secret_values PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_provider_timeout_from_live_geocoder_fails_closed_without_secret_values PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_rate_limit_retry_budget_retries_then_preserves_success_lineage PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_malformed_live_geocoder_response_sets_quality_flags_without_fabricating_h3 PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_out_of_market_live_geocoder_response_preserves_provider_identity_and_flags PASSED
tests/integration/test_live_geocode_provider_adapter.py::test_low_confidence_live_geocoder_response_preserves_admin_match_and_precision PASSED
```
*Result*: 10 passed.

## Artifact Mapping

- **Live Geocoder Adapter**: `modules/external_data/providers/live.py` ([PrimaryGeocodeProvider](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-ext-003/modules/external_data/providers/live.py#L423))
- **Fixture Replay File**: `tests/fixtures/source_data/external/geocode_primary_api.replay.json` ([geocode_primary_api.replay.json](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-ext-003/tests/fixtures/source_data/external/geocode_primary_api.replay.json))
- **Geo Pipeline Unit Tests**: `tests/data/test_geo_pipeline.py` ([test_geo_pipeline.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-ext-003/tests/data/test_geo_pipeline.py))
- **Adapter Integration Tests**: `tests/integration/test_live_geocode_provider_adapter.py` ([test_live_geocode_provider_adapter.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-ext-003/tests/integration/test_live_geocode_provider_adapter.py))
- **Dispatch Queue Requirements**: `docs/evidence/fleet_dispatch/ODP-EXT-003.md` ([ODP-EXT-003.md](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-ext-003/docs/evidence/fleet_dispatch/ODP-EXT-003.md))
- **Worker Evidence History**: `docs/evidence/fleet_dispatch/ODP-EXT-001-003_WORKER_EVIDENCE.md` ([ODP-EXT-001-003_WORKER_EVIDENCE.md](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-ext-003/docs/evidence/fleet_dispatch/ODP-EXT-001-003_WORKER_EVIDENCE.md))
