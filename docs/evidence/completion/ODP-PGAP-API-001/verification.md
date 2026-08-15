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

New tests added by this task: **63** (`tests/contract/test_api_error_envelope.py`
9, `test_api_versioning.py` 7, `test_openapi_artifact_and_client.py` 17,
`test_api_idempotency_and_pagination.py` 30).

Round 2 (the reviewer rejection below) added the last 2: the concurrent-HTTP
idempotency tests. The table above was re-run in full against the round-2 fix,
not carried over from round 1.

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

## Bug found by reviewer rejection (round 2)

Codex rejected the first review round with a runtime probe: 8 concurrent
`POST /api/v1/priceops/plans` carrying one `Idempotency-Key` returned **2
distinct `plan_id`s and two `created: true` responses**. The rejection was
correct and the finding is reproduced and fixed here.

**What was wrong.** Round 1 (above) made `IdempotencyStore.run` atomic — but
`create_plan` never called `run`. It open-coded the policy as three separate
steps, `lookup` → `service.create_plan` → `remember`, so it reconstructed
exactly the check-then-act window that `run` had just been fixed to close. The
store was atomic; the route bypassed the store.

`create_optimizer_job` had the same shape via a router-local
`_idempotency_index` dict, but leaked something different, and the difference
matters. `PriceOpsJobStore.put` dedupes on the key *after* the batch has run,
so every caller still received the first `job_id` and the response contract
looked correct. What escaped was the execution: all 8 requests missed the
pre-check and all 8 ran the optimizer, doing the work and writing to the
repository 8 times for one key. The first version of this test asserted
`job_id` uniqueness, passed against the broken code, and was rewritten to
assert the executed count — the property that was actually broken:

```
E AssertionError: the optimizer batch ran 8 times for one key; exactly 1 may
```

**Why every check stayed green.** The suite asserted atomicity only at the
store (`test_store_run_is_atomic_under_concurrency`) and asserted the route only
*sequentially* (`test_create_plan_replay_keeps_the_legacy_created_flag`). No
test drove concurrent traffic through the HTTP boundary, which is the layer that
actually failed. A green suite proved the primitive was correct, not that the
caller used it. This is the lesson from round 1 restated: test at the layer that
fails.

**Fix.** Both creations now run their whole body — resolve, create, audit,
payload — inside `IdempotencyStore.run` via one `_guard_creation` helper, so the
lookup and the write share the per-entry lock. `lookup`/`remember` now have no
callers outside the store anywhere in the repo (`grep -rn '\.lookup(\|\.remember('
apps/api shared` returns only the store itself), so the open-coded pattern cannot
recur by copy.

**Reproduced before fixing.** The two new tests were first run against the
pre-fix route and failed as Codex described:

```
tests/contract/test_api_idempotency_and_pagination.py
  test_concurrent_create_plan_with_one_key_creates_exactly_one_plan
E AssertionError: one key produced 8 distinct plans: {'pricing-plan-d06aa058…',
  'pricing-plan-0c384721…', 'pricing-plan-3f99a78d…', … 8 total}
```

A first draft of these tests **passed against the broken code** and was
discarded: a client-side `threading.Barrier` alone syncs only the request
threads, and the unguarded window is short enough that the handler completes
inside one GIL switch interval (5ms default). Codex's probe hit it because real
uvicorn I/O sits inside the window. The committed tests widen the window
deterministically by slowing the service call (`time.sleep(0.05)`, the same
technique the store-level test already used), so they fail reliably pre-fix.
They also omit `plan_id` so the server mints one per execution — a
client-supplied id would have hidden the double-create behind a shared id.

Post-fix both tests pass, and the sleep is paid once rather than 8 times because
only the winner executes. Confirmed non-flaky over 15 consecutive runs after the
warmup fix described next (0/15 failures, from 3/12 before it).

## Discovered while testing: a FastAPI route-resolution race (not this task's bug)

Writing the concurrency tests surfaced an unrelated defect that is worth
recording because it will bite anyone else who drives this app concurrently.

**Symptom.** Under 8 concurrent requests to a freshly built app, one request
intermittently came back **404** on a route that plainly exists:

```
POST http://testserver/api/v1/priceops/optimizer-jobs -> 404
{"error":{"code":"not_found","message":"Not Found", …}}
```

**It is not the harness and not the route.** `GET /healthz` — a route declared
directly on the app rather than via `include_router` — never failed under the
identical 8-thread pattern (0 bad in 25×8 requests). Only routes reached
through an included router failed.

**Root cause, from the framework source.** FastAPI 0.138 resolves an included
router's routes lazily into a memoized cache,
`_IncludedRouter.effective_candidates` (`fastapi/routing.py:1530`), and builds
that cache without a lock:

```python
routes_version = self.original_router._get_routes_version()
if routes_version == self._effective_candidates_version:
    return self._effective_candidates
self._effective_candidates = []          # cleared first
...                                      # repopulated
self._effective_candidates_version = routes_version   # stamped last
```

A thread that arrives while another is between the clear and the stamp matches
against an empty or partially built candidate list, matches nothing, and gets a
404. It is self-healing: once the cache is warm the race is gone forever.

**Evidence.** Cold app, 8 concurrent POSTs × 30 attempts → 9 spurious 404s. Same
run with one request issued before the concurrent burst → **0**. Warming
`/healthz` alone did *not* fix it (the middleware stack was not the cause);
warming the priceops router did.

**Scope.** Pre-existing framework behaviour, unrelated to idempotency, not
introduced by this task, and not fixed here — patching FastAPI internals is well
outside this task. It is invisible to Codex's uvicorn probe and to normal
traffic because the first request warms the cache. The tests warm the app before
the barrier, which is why they are deterministic.

**Recommended follow-up (not done here).** A cold pod that receives concurrent
traffic before its first request completes can serve spurious 404s. That is a
narrow but real production exposure at rollout/scale-out. The cheap mitigation
is to resolve the route tree once at startup (a warmup request in the readiness
probe, or touching `effective_candidates` during lifespan) rather than leaving
the first concurrent burst to race. Worth its own task; flagged rather than
silently absorbed.

## Acceptance criteria

| # | Criterion | State |
| --- | --- | --- |
| 1 | Every public operation versioned, tested compatibility aliases | **Met.** 152/152 versioned, alias parity asserted, only the 4 probes unversioned (by design). |
| 2 | Client generated from a checked-in artifact; hand-written DTO duplication removed or isolated | **Met for what the artifact describes** — request DTOs, envelopes and the path map are generated; the 6 duplicated payload types are now pinned to generated types. **Response DTOs remain hand-written and isolated** because the artifact carries no response shape; see the gap note in `implementation.md`. |
| 3 | One structured error envelope with code/message/next action/occurred time/details/correlation ID | **Met.** All 118 legacy raises normalised at the boundary; every field asserted. |
| 4 | Consistent pagination, filtering, sorting, tenant-safe lookup | **Partially met.** One helper is the contract and priceops adopts it; the remaining routers are not yet migrated, and tenant is filterable but not server-derived. Both gaps are stated in `implementation.md`. |
| 5 | Mutations declare idempotency policy with replay/conflict tests; authorization server-derived | **Partially met.** One policy with replay + conflict tests, adopted across the priceops state machine (previously wholly unguarded), and atomic **at the route** — asserted by concurrent HTTP tests, not only at the store (round 2 above). ~40 mutations in other routers remain unguarded. Authorization was already server-derived via `require_permission` and is asserted here (403 → `forbidden`). |
| 6 | OpenAPI diff and client drift block unapproved breaking changes in CI | **Met.** `make api-contract` runs in the required `product` job; both drift modes verified to fail; breaking-change classifier unit-tested; approvals file requires a signature, reason and owning task. |

Criteria 4 and 5 are reported as partially met deliberately. The platform layer,
its tests and one full domain adoption are here; fleet-wide adoption is
mechanical but per-router reviewable work, and claiming it complete would
misrepresent the diff.

### Known gap carried forward, stated explicitly

The round-2 defect is **not unique to priceops**. The unmigrated job endpoints
still open-code the same check-then-act pattern against their own
`_idempotency_index` dict, and a concurrent probe would fail them the same way:

- `apps/api/app/routes/forecastops.py:106` — `create_forecast_job`
- `apps/api/app/routes/adlift.py:72` — `create_incrementality_job`

They are left unchanged here because they are pre-existing `dev` behaviour
outside this task's reviewed scope (priceops is the agreed worked example), and
migrating them is per-router reviewable work. They are recorded so the next
reviewer is not surprised by them and so the follow-up task inherits a concrete
list rather than a rediscovery. Migrating them is a mechanical application of
`_guard_creation` plus the concurrent test pattern proven above.
