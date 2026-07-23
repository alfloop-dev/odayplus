# ODP-INTAKE-FCL-RUNTIME-001 Verification

Integration baseline: `11a1b51f8a5a65c7a447206b1fbbf2b61190931a`

Verification branch: `task/ODP-INTAKE-FCL-RUNTIME-P0-001`

## Persisted Effect Proof

- Canonical `POST /api/v1/intakes/url` enqueues a durable
  `assisted-listing-intake` job.
- `ODayWorker.run_once()` claims the production job type, performs the approved
  HTTP retrieval boundary, and persists stage history, snapshot, parser,
  matching, and terminal readback.
- Structured manual, CSV, and approved-feed rows persist through the canonical
  runtime; readback succeeds after the process-local projection is cleared.
- Revision decisions append immutable `ListingRevision` records.
- Identity decisions persist graph plans, edges, review/reversal lineage, and
  decision receipts.
- `AWAITING_ASSISTED_ENTRY` accepts canonical address, rent, and area
  corrections. Identity-affecting values remain unapplied until a different
  authorized reviewer approves the decision. Once all required values are
  effective, processing records `MATCHING` and reaches `READY` or
  `NEEDS_REVIEW`.
- Candidate promotion enqueues a real `sitescore-candidate` record in the
  configured durable `job_queue`. `ODayWorker` executes the job, persists the
  SiteScore report, advances the promotion saga, and exposes the same job
  receipt after the API and persistence bundle are recreated.
- Assignment, claim, transfer, SLA pause/resume, and saved views are persisted
  through the assisted-intake repository and remain operable after a new
  `create_app` instance opens the same database.
- Lifecycle idempotency records are durable. A restart followed by the same
  tenant, actor, operation, key, and fingerprint replays the original receipt;
  changing the payload returns `IDEMPOTENCY_KEY_REUSED`.
- Canonical Inbox location summaries project latitude, longitude, geocode
  confidence, and source from effective persisted field lineage. Coordinate
  masking clears the coordinate provenance as one unit.

## Restart and API Readback Scenarios

```text
pytest -q \
  tests/integration/test_assisted_listing_functional_runtime.py::test_assisted_entry_canonical_corrections_complete_matching_after_second_review \
  tests/integration/test_assisted_listing_functional_runtime.py::test_canonical_promotion_persists_candidate_and_sitescore_job \
  tests/integration/test_assisted_listing_functional_runtime.py::test_assignment_sla_saved_view_and_replay_survive_api_restart \
  tests/integration/test_assisted_listing_functional_runtime.py::test_lifecycle_cancel_idempotency_replays_original_receipt_after_restart \
  tests/integration/test_assisted_listing_functional_runtime.py::test_canonical_inbox_projects_authoritative_effective_coordinates
5 passed
```

The promotion scenario closes the first SQLite bundle, opens a second API
instance, claims and executes the persisted SiteScore job, closes that bundle,
and verifies the completed promotion and job receipt from a third instance.
The assisted-entry, assignment/SLA/saved-view, and lifecycle scenarios also
close and reopen the database before their readback and replay assertions.

## Runtime and Contract Verification

```text
pytest -q tests/integration/test_assisted_listing_functional_runtime.py
16 passed

pytest -q \
  tests/contract/test_assisted_listing_v1_runtime.py \
  tests/contract/test_assisted_listing_intake_schema.py \
  tests/contract/test_assisted_listing_intake_states.py
passed

python3 scripts/openapi/export_openapi.py --check
packages/openapi-client/openapi.json matches the live schema

python3 scripts/openapi/generate_client.py --check
packages/openapi-client/src/generated/types.ts matches the artifact

python3 -m py_compile \
  apps/api/app/routes/listings.py \
  apps/api/oday_api/main.py \
  apps/worker/assisted_listing_intake/worker.py \
  apps/worker/oday_worker/handlers.py \
  modules/external_data/application/assisted_intake.py \
  modules/listing/application/promotion.py \
  modules/opsboard/application/network_listings.py \
  shared/infrastructure/persistence/factory.py \
  tests/integration/test_assisted_listing_functional_runtime.py
passed
```

The separate worktree does not contain its own `node_modules`; invoking the
repository TypeScript compiler reaches `tsc` but cannot resolve the `vitest`
types imported by `src/index.test.ts`. The generated-client consistency checks
above are therefore the client contract evidence for this change.

## Baseline Test Drift

The broad legacy contract batch still contains tests that directly mutate
process-local `AssistedIntakeStore` records or expect operator submission and
promotion to complete synchronously without a worker. The representative
`test_url_intake_and_concurrency_lifecycle` failure reproduces unchanged on the
integration baseline commit. Those fixtures are not used as durable runtime
evidence; the restart/readback tests above exercise the authoritative
repository, queue, worker, and API paths.
