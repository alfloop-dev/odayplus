# ODP-INTAKE-API-001 implementation evidence

The task implements the approved Assisted Listing Intake v1 HTTP surface at
`/api/v1`. The existing Operator Console compatibility surface remains at
`/api/v1/operator/network-listings`.

## Delivered

- Effective OpenAPI 1.1.3 is built from the base plus all five overlays in the
  review-manifest order.
- A committed effective JSON artifact and generated TypeScript schema/path
  namespace are produced by `scripts/generate_assisted_listing_intake_client.py`.
- Runtime routes cover all 27 approved operations: query, URL and batch intake,
  correction, match/identity decisions, merge/split/unmerge, assignment, retry,
  saved views, promotion, lifecycle actions, SLA actions, and review/reversal.
- The HTTP boundary validates contract UUID, date-time, URI, enum, array,
  idempotency-key, and exact weak `If-Match` constraints before route logic.
- Tenant isolation fails closed for intake, job, assignment, saved-view,
  promotion, and identity-decision resources. Read paths retain role/ownership
  authorization and field masking. An unassigned record has no implicit staff
  owner: only its actual submitter or assignee satisfies the ownership gate.
- List pagination uses a 24-hour HMAC-signed keyset cursor bound to tenant,
  filters, sort, snapshot, sort tuple, and last resource identifier. Deployed
  replicas configure `ODP_INTAKE_CURSOR_SIGNING_KEY`; local/test processes use
  an unpredictable process-local fallback rather than a repository secret.
  Mismatched, expired, or differently signed cursors fail closed, and records
  inserted after the snapshot cannot shift an offset or duplicate a row.
- Lifecycle guards enforce the approved cancel, quarantine, reopen, correction,
  assignment, SLA, promotion-review, identity-review, and reversal transitions.
- Idempotent mutation replay stores an immutable receipt snapshot and returns
  the original status/body and stable ETag without re-running a now-invalid
  state transition. Later assignment claim/transfer/complete mutations cannot
  rewrite the cached assignment receipt. Reuse with a changed payload returns
  the declared conflict error.
- Batch intake reports schema-valid partial success without hiding rejected-row
  errors, and all API errors use the effective `ApiError` wire schema.
- Contract tests compare parameters, request bodies, and every declared response
  schema for all 27 operations. Exercised runtime responses are independently
  validated against the effective bundle with UUID/date-time format checking.
- Effective artifact and generated-client drift remain byte-for-byte pinned.

## Composition boundary

This lane owns the API contract boundary, effective artifact/client generation,
in-memory reference runtime, authorization/state guards at that boundary, and
contract tests. Durable PostgreSQL storage, worker execution, event publication,
migration rollout, and release infrastructure remain owned by sibling Assisted
Intake tasks. The `AssistedIntakeStore` constructor remains injectable so a
durable adapter can compose without changing the approved HTTP surface.
