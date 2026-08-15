# ODP-FLOW-001 — Verification

**Owner:** Claude2 · **Reviewer:** Claude

## Commands run (all green)

### Focused new suite
```
uv run pytest tests/integration/test_external_ingestion_persistence.py
# 6 passed
```
Covers: manual ingestion persists + is readable via API (lineage preserved);
freshness reads persisted run state (fixture only on cold store); idempotent
retry rejection + `accepted`/`idempotent_replay` audit; DQ quarantine + lineage
queryable (duplicate fixture → 1 accepted / 1 quarantined,
`duplicate_idempotency_key`); scheduled ingestion persists with
`trigger="scheduled"`; **durable survive-restart** — run persisted before a
simulated restart is retrievable and a same-key retry after restart returns
`created=False` with the original `run_id`.

### Regression + related surface
```
uv run pytest \
  tests/integration/test_external_ingestion_persistence.py \
  tests/integration/test_external_scheduled_fetch_worker.py \
  tests/integration/test_external_provider_registry.py \
  tests/integration/test_external_source_connectors.py \
  tests/integration/test_domain_api_rbac.py \
  tests/integration/test_durable_repository_wiring.py \
  tests/contract/test_platform_api.py \
  tests/contract/test_ingestion_contracts.py \
  tests/data/test_external_providers.py
# 161 passed, 2 warnings
```

### Lint
```
uv run ruff check <all changed python files>
# All checks passed!
git diff --check    # clean
```

### Frontend typecheck
```
(cd packages/openapi-client && tsc --noEmit)   # exit 0
(cd apps/web && tsc --noEmit)                   # exit 0
```

## Manual behavioural check (in-process)

`ExternalIngestionService` exercised directly:
- run 1 (`Idempotency-Key=k1`) → `created=True`, `status=SUCCEEDED`,
  `accepted=2`, `quarantined=0`, `canonical_snapshot=listing-2026-06-26`,
  freshness `FRESH`, lineage count 2.
- run 2 (same api key) and run 3 (same window, no api key) →
  `created=False`, identical `run_id`; store holds exactly 1 run; audit shows
  `accepted` + two `idempotent_replay`.

Durable restart (fresh `SqliteEngine`/bundle on the same file): the persisted
run is visible to the new process and both window- and API-key retries are
rejected (`created=False`, same `run_id`).

## Non-regression notes

- `GET /external-data/freshness` still returns the documented fixture
  (`snap-expansion-20260628-0100`) on a **cold** store, so
  `tests/contract/test_platform_api.py` and the expansion product E2E remain
  green; persisted values take over only after a run exists.
- Expansion UI fixture fallback preserved (`external-freshness-lineage` shows
  fixture values when no API base URL is configured), so
  `tests/e2e/e2e-exp.spec.ts` assertions still hold; a `DataSourceBadge`
  (`external-freshness-source`) exposes the api-vs-fixture state.
- `PersistenceBundle` gained one field (`ingestion_run_store`); no code
  constructs it positionally, and durable-wiring tests pass.
