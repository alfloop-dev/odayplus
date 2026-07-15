# ODP-PGAP-API-001 — Verification Record

Owner: Claude2 · Reviewer: Codex
Verified on `task/ODP-PGAP-API-001`, based on `dev` @ `a6b939d9`.

All commands below were run in this worktree; results are transcribed as
observed, not summarised optimistically.

## Commands

| Command | Result |
| --- | --- |
| `uv run ruff check tests modules apps shared models solver pipelines infra scripts` | All checks passed |
| `uv run pytest -m "not requires_live_env" tests modules apps shared models` (the exact CI product command) | **865 passed**, 0 failed |
| `npm run typecheck --workspace=@oday-plus/openapi-client` | clean |
| `npm run typecheck --workspace=@oday-plus/web` | clean |
| `make api-contract` | API contract gate: PASS |
| `git diff --check origin/dev...HEAD` | clean |

New tests added by this task: **61** (`tests/contract/test_api_error_envelope.py`
9, `test_api_versioning.py` 7, `test_openapi_artifact_and_client.py` 17,
`test_api_idempotency_and_pagination.py` 28).

## Runtime evidence (not mocks)

The brief requires runtime evidence rather than mocks or static documents.
Every assertion below is produced by driving the composed app through
`TestClient(create_app())` — the same factory the service runs.

**Envelope, from a legacy route that still raises a bare `HTTPException`:**

```
GET /api/v1/jobs/no-such-job          → 404
{"detail": "job not found",
 "error": {"code": "not_found", "message": "job not found",
           "next_action": "Verify the identifier; the resource may have been removed.",
           "occurred_at": "2026-07-15T…Z", "details": [],
           "correlation_id": "corr-env-2"}}
```

`error.correlation_id` is asserted equal to the `X-Correlation-Id` response
header, so the id in the body is the one the audit log recorded.

**Versioned + alias parity, measured on the live app:**

```
versioned paths in schema : 152
alias templates served    : 152   (exactly paired)
unversioned in schema     : ['/health', '/healthz', '/platform/health', '/platform/version']
GET /priceops/plans   → Deprecation: true, Link: </api/v1/priceops/plans>; rel="successor-version"
GET /api/v1/priceops/plans → no Deprecation header
```

The only unversioned schema paths are the four probes, which are unversioned by
design and asserted as such.

**Idempotency replay and conflict, over HTTP:**

```
POST /api/v1/priceops/plans/PLAN-REPLAY/approve   (Idempotency-Key: idem-approve-1) → 200, idempotent_replay=false
POST  … same key, same body                                                          → 200, idempotent_replay=true
  audit events for corr-approve-1 with type priceops.approved.v1: 1   (approved exactly once)
POST /api/v1/priceops/plans/PLAN-CONFLICT/approve (key idem-conflict-1, decision APPROVE) → 200
POST  … same key, decision REJECT                                                    → 409 idempotency_conflict
```

**Pagination:**

```
GET /api/v1/priceops/plans?limit=2&offset=0 → count=2, total=5, has_more=true
GET /api/v1/priceops/plans?limit=2&offset=2 → count=1, has_more=false
GET /api/v1/priceops/plans?tenant_id=tenant-b → total=1
GET /api/v1/priceops/plans (no params)      → unchanged for existing callers
```

## The CI gate was verified by making it fail

A gate that has never failed is unproven. Each failure mode was injected and
observed, then reverted:

| Injected | Gate output |
| --- | --- |
| Added `GET /drift-probe` to the app, left the artifact alone | `ERROR: packages/openapi-client/openapi.json is stale — the API changed but the artifact was not regenerated.` → FAIL |
| Appended a hand-written type to `src/generated/types.ts` | `ERROR: …/generated/types.ts is stale — the OpenAPI artifact changed but the client was not regenerated.` → FAIL |
| Clean tree | `API contract gate: PASS` |

Breaking-change classification is covered by unit tests over synthetic
artifacts (`test_openapi_artifact_and_client.py`): operation removal, new
required field, type change, enum member removal and response removal are each
asserted **breaking**; new operation, new optional field, new enum member, new
response and description-only edits are each asserted **not** breaking. Two
guards protect the gate's credibility: diffing the real artifact against itself
yields zero changes (no crying wolf), and a self-referential schema does not
recurse forever.

## Bugs this task's own tests caught

1. **Envelope broke object `detail`.** Flattening `detail` to a string
   stringified `network_rebalance`'s `state` retry flag and
   `network_scoring`'s `missing` list. Fixed by passing `detail` through
   verbatim; now asserted by
   `test_object_detail_is_not_flattened_into_a_string`.
2. **One idempotency key could both approve and reject a plan.** Scoping by
   audit action (which is `body.decision` for approve) split APPROVE and REJECT
   into different scopes, so the conflict went undetected and both transitions
   applied. Fixed by scoping on the stable event type.
3. **`/jobs` and `/audit/events` were never versioned.** Caught by
   `test_every_product_operation_is_served_under_api_v1`.
4. **1.6s added to the first aliased request.** The deprecation middleware
   built the whole OpenAPI schema inside the request path, breaking the
   heatzone fixture performance target (2.24s vs a 0.95s budget). Fixed by
   walking the router tree at mount time: 1.633s → 0.087s.

## Bugs found by independent adversarial review

The diff was reviewed by a separate agent instructed to find correctness bugs.
It found seven real defects that this task's own tests did **not** cover. All
are fixed, and each now has a regression test.

1. **`IdempotencyStore.run` was not atomic.** `lookup` and `remember` took the
   lock separately, leaving a check-then-act window. Sync routes are served from
   a threadpool, so this was reachable: a concurrent double-submit of `approve`
   approved twice and wrote two audit events — the precise failure the store
   exists to prevent. Reproduced before the fix (4 threads → 4 executions).
   Fixed with a per-entry lock held across lookup→operate→remember; per-entry
   rather than global so distinct keys do not serialise behind a slow mutation.
   Guarded by `test_store_run_is_atomic_under_concurrency`, which was confirmed
   to fail against the old code (**8 executions, expected 1**) and pass after.
2. **`list_plans` silently truncated to 100 rows.** `Query(default=DEFAULT_LIMIT)`
   applied a page size the caller never asked for, contradicting this task's own
   documented compatibility guarantee. `limit=None` now means unbounded, so
   adopting the helper cannot cut an existing caller's results. Guarded by
   `test_no_limit_requested_returns_every_row` and an HTTP-level test.
3. **No `Exception` handler, but `500: ErrorResponse` was declared** on every
   versioned operation — the artifact and generated client were told a 500
   carries the envelope while the server returned plain text. Added a handler
   that logs the exception with its correlation ID and returns the envelope
   without echoing the raw exception text.
4. **`ODP_PERSISTENCE_MODE` was a typo** in the export script; the factory reads
   `ODP_PERSISTENCE`. With `ODP_PERSISTENCE=durable` set, exporting the schema
   would have created a real SQLite file, defeating the invariant the code
   claimed on the line above.
5. **Lexicographic sort of numeric fields** ordered 10 before 9. The sort key
   now compares numbers numerically, strings lexicographically, missing last.
6. **`lookup` conflated "absent" with "stored `None`"**, so an operation
   returning `None` would re-execute instead of replaying. Fixed with a
   `MISSING` sentinel.
7. **FIFO eviction fired when overwriting an existing key**, discarding a live
   record while the store was under its cap.

The review also confirmed several things were already correct and should not be
re-litigated: failed operations are not cached as successes,
`request.state.correlation_id` is reliably available inside the handlers
(middleware ordering), 401 `WWW-Authenticate` passes through, and the versioning
alias set is exactly symmetric with no `app.state` leak across `create_app()`.

## Acceptance criteria

| # | Criterion | State |
| --- | --- | --- |
| 1 | Every public operation versioned, tested compatibility aliases | **Met.** 152/152 versioned, alias parity asserted, only the 4 probes unversioned (by design). |
| 2 | Client generated from a checked-in artifact; hand-written DTO duplication removed or isolated | **Met for what the artifact describes** — request DTOs, envelopes and the path map are generated; the 6 duplicated payload types are now pinned to generated types. **Response DTOs remain hand-written and isolated** because the artifact carries no response shape; see the gap note in `implementation.md`. |
| 3 | One structured error envelope with code/message/next action/occurred time/details/correlation ID | **Met.** All 118 legacy raises normalised at the boundary; every field asserted. |
| 4 | Consistent pagination, filtering, sorting, tenant-safe lookup | **Partially met.** One helper is the contract and priceops adopts it; the remaining routers are not yet migrated, and tenant is filterable but not server-derived. Both gaps are stated in `implementation.md`. |
| 5 | Mutations declare idempotency policy with replay/conflict tests; authorization server-derived | **Partially met.** One policy with replay + conflict tests, adopted across the priceops state machine (previously wholly unguarded). ~40 mutations in other routers remain unguarded. Authorization was already server-derived via `require_permission` and is asserted here (403 → `forbidden`). |
| 6 | OpenAPI diff and client drift block unapproved breaking changes in CI | **Met.** `make api-contract` runs in the required `product` job; both drift modes verified to fail; breaking-change classifier unit-tested; approvals file requires a signature, reason and owning task. |

Criteria 4 and 5 are reported as partially met deliberately. The platform layer,
its tests and one full domain adoption are here; fleet-wide adoption is
mechanical but per-router reviewable work, and claiming it complete would
misrepresent the diff.
