# ODP-PGAP-API-001 — Implementation Record

Task: Mature versioned API and generated client contracts
Owner: Claude2 · Reviewer: Codex
Phase: Product Platform P1 Closure

## Root cause

`shared/` supplied auth, audit, jobs and observability primitives but nothing
for the HTTP boundary itself. Each of the 14 routers therefore reinvented its
own error shape, collection envelope, tenant handling and idempotency store,
and the operator sub-tree evolved a camelCase dialect that disagreed with the
domain routers' snake_case one.

Measured state of `dev` at the start of this task:

| Area | Before |
| --- | --- |
| Error contract | none; 118 `HTTPException(detail="…")` sites, 4 incompatible shapes, no error body carried a correlation ID |
| Versioning | 2 of 14 routers under `/api/v1`; no aliases; `operator.py` and `main.py` disagreed on who applies the prefix |
| Collections | 25+ endpoints hand-rolling `{items, count}`; 0 with offset/sort/total; `limit` on 2 endpoints with **opposite** meanings |
| Idempotency | 5 duplicated in-memory dicts; 34 of 86 mutations guarded; every approve/execute/rollback unguarded |
| Client | 1216 hand-written lines, no OpenAPI artifact anywhere in the repo |
| CI | no drift or breaking-change gate |

## What was built

### `shared/api/` — the missing boundary layer

- **`errors.py`** — one `ErrorEnvelope` (`code`, `message`, `next_action`,
  `occurred_at`, `details`, `correlation_id`) installed via exception handlers.
  Because it is a boundary concern, all 118 legacy raises are normalised
  **without editing a single call site**. Registered against Starlette's
  `HTTPException` so it also covers FastAPI's subclass and Starlette's own
  routing 404/405.
- **`pagination.py`** — `PageParams` / `paginate`, with bounds clamped
  server-side and a sort key that is total over missing/heterogeneous values.
- **`idempotency.py`** — one fingerprinted, scoped, bounded store replacing the
  five ad-hoc dicts, with a uniform `idempotent_replay` signal.
- **`versioning.py`** — `mount_versioned` mounts every router at `/api/v1`
  (in-schema) and again on its legacy path as a deprecated alias (out of
  schema, `Deprecation: true` + `Link` successor).

### Contract artifact, generated client, CI gate

- `scripts/openapi/export_openapi.py` → `packages/openapi-client/openapi.json`,
  exported from the live app, deterministic (`sort_keys`, release-SHA env
  cleared) so the drift check cannot flap across machines.
- `scripts/openapi/generate_client.py` → `src/generated/types.ts` (942 lines):
  all 81 component schemas plus the versioned path map.
- `scripts/openapi/openapi_diff.py` + `check_drift.py` + a reviewed
  `approved_breaking_changes.json` escape hatch, wired into the existing
  (already-required) `product` CI job via `make api-contract`.

## Decisions a reviewer should check

**The error envelope is additive; `detail` is passed through verbatim.**
An earlier revision summarised `detail` to a string and broke two real
consumers — `network_rebalance` returns a `state` retry flag and
`network_scoring` a `missing` list, both objects the console branches on — plus
15 tests. The contract suite caught it. `detail` now means exactly what it
meant before, and `error` is purely additive.

**Hand-written request DTOs are stricter than the generated ones, on purpose.**
`riskAcknowledged` is required in TypeScript but optional in the schema,
because the Pydantic default renders as optional even though the server rejects
the request without it. Replacing them with the generated types would have
silently discarded type safety on a risk-disclosure path. They now shadow their
generated namesakes and are pinned by `AssertAssignable`, so a server-side shape
change fails the build.

**Idempotency scope is the event type, not the audit action.** Scoping on the
audit action put `APPROVE` and `REJECT` in different scopes, letting one key
both approve *and* reject a plan. Caught by
`test_reusing_a_key_with_a_different_body_is_a_409_conflict`.

**Alias detection walks the router tree rather than the OpenAPI schema.**
Deriving aliases from `app.openapi()` is exact but costs ~1.5s to build; it
added 1.6s to the first aliased request (measured), which broke the heatzone
fixture performance target. The router walk handles FastAPI 0.138's nested
`_IncludedRouter`, which a naive `router.routes` scan misses (~57 operator
paths). First alias request: 1.633s → 0.087s.

## Deliberate contract change

`/jobs`, `/jobs/{job_id}` and `/audit/events` were declared inline on the app
and so were never versioned, despite being product operations. They now mount
through a router: `/api/v1/jobs` etc. is the documented contract and the
unversioned paths keep serving as deprecated aliases. Three existing tests
asserted the unversioned paths were in the schema and were updated to assert the
versioned contract plus alias absence. **No runtime caller breaks** — the alias
serves the request.

## Known gaps (follow-up, not done here)

**Response DTOs are not generated.** Every route is annotated
`-> dict[str, Any]`, so the artifact describes all 156 success responses as
`additionalProperties: true`. There is no response shape to generate from, so
those DTOs remain hand-written and are isolated in `src/index.ts` behind a
documented boundary (the acceptance criterion permits "removed **or
isolated**").

This is deliberately **not** fixed by a mechanical sweep: `response_model=`
*filters* the response to the declared fields, so an incomplete model silently
drops data the console renders. It must be done per route, with that route's
tests. Recommended follow-up: declare `response_model` domain by domain,
smallest surface first, asserting the full response body before and after.

**Pagination and idempotency are applied to priceops, not yet fleet-wide.**
The helpers are the contract and priceops is the worked example — it owned the
largest state machine and every one of its transitions was unguarded. The
remaining routers still hand-roll `{items, count}` and 40-odd mutations remain
unguarded. Each router's adoption is a small, independently testable change;
doing them all here would have meant a diff too large to review honestly.

**Tenant scoping is filterable, not enforced.** `list_plans` accepts a
`tenant_id` filter, but tenant remains a client-supplied body field on the
domain routers rather than a server-derived scope, and `get_plan` does not check
it. Making tenant server-derived is an auth-boundary change that belongs with
the identity work, not with the API-contract layer.
