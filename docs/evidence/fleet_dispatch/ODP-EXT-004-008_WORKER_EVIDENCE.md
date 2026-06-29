# ODP-EXT-004-008 Worker Evidence

Date: 2026-06-29
Lane: External source operations
Worktree: `/home/lupin/odayplus-dev`

## Scope

This worker pass covered:

- ODP-EXT-004 scheduled external fetch/backfill
- ODP-EXT-005 quota and rate-limit resilience
- ODP-EXT-006 freshness and data-quality gate
- ODP-EXT-007 licensing and allowed-use gate
- ODP-EXT-008 external source product E2E

Proof boundary: deterministic fixture mode and approved mock live-provider mode. This is not production licensing approval and does not prove third-party provider credentials.

## Repo-Side Evidence

- Scheduled fetch creates durable job, idempotency, snapshot, and watermark fields through `modules.external_data.workers.scheduled_fetch.ExternalFetchScheduler`.
- Backfill command is exposed by `scripts/external_data_backfill.py`.
- Provider mock live boundary is exposed by `modules.external_data.providers.provider_mock.ListingProviderMockService`.
- Provider auth, quota, freshness, license, lineage, idempotent backfill replay, circuit breaker, timeout, malformed payload, duplicate quarantine, fixture default, and export restriction assertions are covered in `tests/e2e/test_external_source_product_e2e.py`.
- Freshness/geocode lineage remains covered by `tests/data/test_geo_pipeline.py`.

## Verification Commands

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
```

Result: passed. PR #82 headRefOid `1494e51f7c90a35abbbc1b9feec6bb2dbb8d5633`, draft `true`, state `OPEN`, mergeable `MERGEABLE`, listed checks successful at command time.

```bash
python3 scripts/external_data_backfill.py --provider-id listing.partner_feed --start 2026-06-28T10:00:00Z --end 2026-06-28T12:00:00Z --interval-hours 1
```

Result: passed. Produced two `SUCCEEDED` manual-backfill runs with durable idempotency keys for `10:00-11:00` and `11:00-12:00`, `source_snapshot_ids: ["listing-2026-06-26"]`, and last-success watermarks through `2026-06-28T12:00:00+00:00`.

```bash
uv run pytest tests/e2e/test_external_source_product_e2e.py -k "live_provider_mode_product_e2e" -q
```

Result: passed, `1 passed`.

```bash
uv run pytest tests/e2e/test_external_source_product_e2e.py -k "auth_quota_and_freshness" -q
```

Result: passed, `1 passed`.

```bash
uv run pytest tests/e2e/test_external_source_product_e2e.py tests/data/test_geo_pipeline.py -q
```

Result: passed, `17 passed`.

```bash
uv run pytest tests/e2e/test_external_source_product_e2e.py -k "license_gate_and_fixture_default" -q
```

Result: passed, `1 passed`.

```bash
uv run pytest tests/e2e/test_external_source_product_e2e.py -q
```

Result: passed, `9 passed`.

```bash
python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-004
python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-005
python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-006
python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-007
python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-008
```

Result: passed. Each command emitted the expected fleet execution brief.

## Remaining External Blockers

- Real provider secrets/live credentials must be supplied outside the repo by environment or approved provider-mock service.
- Third-party provider license approval and allowed production use require external evidence; this worker only verifies repo-side fail-closed metadata and mock-live behavior.
- Provider-specific production proof must remain separate from deterministic fixture and approved mock-live proof.
