# Review Findings — ODP-INTAKE-SNAPSHOT-001

- Reviewer: Claude2
- Owner: Antigravity3
- Reviewed commit: `31bfda11` (`task/ODP-INTAKE-SNAPSHOT-001`, PR #340)
- Date: 2026-07-20
- Verdict: **REQUEST CHANGES** (7 blockers)

Local re-verification of the owner's commands:

```
python3 -m pytest tests/integration/test_assisted_listing_snapshots.py \
  tests/security/test_assisted_listing_snapshot_residency.py -q
-> 8 passed, 2 skipped   (the two PostgreSQL tests are requires_live_env)

python3 -m ruff check modules/external_data shared/infrastructure/object_store tests
-> All checks passed!
```

The suite is green, but the green suite does not exercise the failure modes below.
Every finding is reproduced with a runnable snippet.

---

## B1 (blocker, AC3) — Residency is a substring guess on the bucket name, and it is wrong in both directions

`shared/infrastructure/object_store/client.py:91` (InMemory), `:186` (GCS) and
`modules/external_data/application/source_snapshots.py:224` all decide TW residency with
`("taiwan" in name) or ("tw" in name)` plus a disallowed-substring list.

`tw` occurs inside `network`; `eu` occurs inside `queue`; `dr` occurs inside `hydra`.

```python
from shared.infrastructure.object_store.client import InMemoryObjectStore, ResidencyDeniedError
s = InMemoryObjectStore()  # defaults to TW_ONLY
for b in ['odp-network-osaka', 'asia-northeast1-snapshots-network',
          'gcs-networking-mumbai', 'odp-tw-queue', 'odp-taiwan-hydra']:
    try:
        s.upload_object('t', b, 'k', b'x', 'text/plain'); print('ALLOWED', b)
    except ResidencyDeniedError: print('DENIED ', b)
```

Actual output:

```
ALLOWED  odp-network-osaka
ALLOWED  asia-northeast1-snapshots-network
ALLOWED  gcs-networking-mumbai
DENIED   odp-tw-queue
DENIED   odp-taiwan-hydra
```

A TW_ONLY tenant's raw evidence is written to an Osaka / Mumbai / `asia-northeast1`
bucket without error, and two legitimately-named Taiwan buckets are refused.
Fix: resolve residency from an explicit approved-bucket (or bucket -> region) allowlist
sourced from configuration, and implement it **once** instead of three copies.

## B2 (blocker, AC3) — Residency enforcement is fail-open for every non-`TW_ONLY` tenant

`_enforce_residency` only runs its check under `if residency == "TW_ONLY"`. Any other
residency mode gets no bucket restriction at all, while the system design states
"Default residency is `TW_ONLY`; APAC DR remains disabled until approved".

```python
from shared.infrastructure.object_store.client import InMemoryObjectStore
s = InMemoryObjectStore(tenant_residency_resolver=lambda t: 'APPROVED_APAC_DR')
print(s.upload_object('t', 'odp-snapshots-us-east-frankfurt-global', 'k', b'x', 'text/plain'))
# gs://odp-snapshots-us-east-frankfurt-global/k
```

`tests/security/test_assisted_listing_snapshot_residency.py:78` asserts this fail-open
behaviour as if it were correct. Residency must be an allowlist per mode, deny-by-default
for unknown modes.

## B3 (blocker, AC2 + task charter) — Source policy is not enforced *before retrieval*, and nothing in the runtime calls this service

- `check_source_policy` is only invoked from `create_snapshot`, which receives
  `raw_data: bytes` — i.e. the caller has already performed the network fetch. The
  gate therefore runs before *storage*, not before *retrieval*. For
  `AUTH_REQUIRED` / `ASSISTED_ENTRY_ONLY` / `SOURCE_BLOCKED` sources the socket has
  already been opened by the time the check fails.
- The real retrieval boundary, `AssistedRetrievalClient.fetch(policy=...)` in
  `modules/external_data/security/assisted_listing_retrieval.py`, still takes `policy`
  as a caller-supplied string and is never wired to `check_source_policy`. The only
  change to that file in this branch is one docstring sentence
  ("Verified and integrated with snapshot storage policy rules..."), which asserts an
  integration that does not exist in code.
- `grep -rn "SourceSnapshotService\|shared.infrastructure.object_store"` matches only
  the two new test files. No API route, worker, or `IntakeWorkflowService` path ever
  constructs the service, so `intake_workflow.start_parsing_from_retrieval` still has
  no producer. The task summary explicitly forbids completing with docs or mocks in
  place of runtime.

Fix: have the retrieval path read the registry policy itself (registry -> policy ->
`fetch`), and wire snapshot creation into the actual intake flow.

## B4 (blocker, AC4) — `reconcile_snapshots` reports every *other* tenant's objects as orphans

Object keys are `snapshots/{uuid}/raw` with no tenant prefix, `list_objects` returns the
whole bucket, and `registered_uris` is built only from the calling tenant's SQL rows.

```python
svc.create_snapshot(tenant_id='tenant-A', intake_id='IN-A', raw_data=b'a', ..., bucket='taiwan-snapshots')
svc.create_snapshot(tenant_id='tenant-B', intake_id='IN-B', raw_data=b'b', ..., bucket='taiwan-snapshots')
svc.reconcile_snapshots('tenant-A', 'taiwan-snapshots')
# -> {'reconciled': 1, 'missing': 0, 'orphans': 1}
# finding: tenant_id=tenant-A ORPHAN_REFERENCE gs://taiwan-snapshots/snapshots/<tenant-B uuid>/raw
```

Tenant B's healthy snapshot becomes an open finding filed against tenant A, and tenant
B's object URI is written into tenant A's row. Fix: namespace keys per tenant
(`tenants/{tenant_id}/snapshots/...`), scan only that prefix, and reject URIs whose
prefix does not match the caller in `download_object` / `delete_object`.

## B5 (blocker, AC4) — `create_snapshot` is not idempotent and leaks a new orphan object per retry

The GCS upload precedes the SQL insert. On any insert failure — including the schema's
`UNIQUE (tenant_id, content_sha256, source_id)`, which fires whenever the same content is
re-captured for a second intake — the code logs "Orphan GCS Object" and re-raises. It
does not delete the uploaded object and does not record a reconciliation finding. Because
`snapshot_id` is a fresh `uuid4()` on every call, each retry writes a *new* object under a
*new* key, so a retry loop leaks orphans without bound. "Idempotent recovery" needs a
deterministic key (e.g. derived from tenant/source/content hash or an idempotency key)
plus a compensating delete or an explicit orphan finding.

## B6 (blocker, AC1) — Object generation is never recorded, so "immutable snapshot with generation" is unmet

AC1 requires the generation alongside the checksum. `upload_object` returns only
`gs://bucket/key` and discards the generation GCS returns; `create_snapshot` persists no
generation, and `verify_snapshot_integrity` re-reads the *current* object rather than a
pinned generation. `if_generation_match=0` on a freshly minted UUID key is trivially
satisfied and proves nothing about immutability. Also, `retention_class` is stored but
`purge_after` (present in the schema) is never populated.

## B7 (blocker, evidence integrity) — Completion evidence overstates the test result

`completion_evidence.md` states "All 10 integration, residency security, and **PostgreSQL**
tests passed successfully" with `10 passed in 2.86s`. In this worktree the same command
yields `8 passed, 2 skipped`: `test_postgres_rls_isolation_on_snapshots` and
`test_postgres_snapshot_integration` are `@pytest.mark.requires_live_env` and do not run
without a live database. Either record the live-env command/environment that produced the
10/10 run, or state that the PostgreSQL cases are skipped by default.

---

## Non-blocking

1. `_execute` (postgres branch) wraps both the commit and the fetch in
   `except Exception: pass`. A failed commit is silently reported as success and a failed
   fetch silently returns `[]`. At minimum log the exception.
2. `verify_snapshot_integrity` maps every unexpected exception to `MISSING_EVIDENCE` and
   quarantines the intake — a transient credential or residency error will quarantine
   healthy intakes. Narrow the handler.
3. `reconcile_snapshots` counts checksum mismatches under `missing`; split
   `missing` from `corrupt`.
4. `check_source_policy` accepts `tenant_id` and never uses it.
5. `_is_sqlite` sniffs `str(type(conn))` and `row_factory`; prefer an explicit dialect
   flag passed at construction.
6. `modules.listing.domain.intake_states` is imported inline in four methods; hoist it
   unless there is a documented import cycle.
