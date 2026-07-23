# ODP-INTAKE-FCL-RUNTIME-001 Verification

Baseline: `c900e906f96cb3750274c24e1a8f2922999f9048`

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
- Candidate promotion persists the Candidate Site and authoritative SiteScore
  job; detail readback selects the promotion job after completion.

## Verification

```text
pytest -q tests/integration/test_assisted_listing_functional_runtime.py \
  tests/integration/test_assisted_listing_identity.py \
  tests/contract/test_assisted_listing_intake_states.py
28 passed

npx --yes vitest@4.0.18 run packages/openapi-client/src/index.test.ts
5 passed

node_modules/.bin/tsc --noEmit --target ES2020 --module ESNext \
  --moduleResolution Bundler --allowImportingTsExtensions \
  --lib ES2022,DOM --strict --noUncheckedIndexedAccess \
  --esModuleInterop --skipLibCheck packages/openapi-client/src/index.ts
passed

python3 scripts/openapi/export_openapi.py
python3 scripts/openapi/generate_client.py
passed
```

The older synchronous promotion tests still expect the legacy operator submit
route to become `READY` without a worker and directly mutate
`AssistedIntakeStore`. They are not completion evidence for the canonical
queued runtime.
