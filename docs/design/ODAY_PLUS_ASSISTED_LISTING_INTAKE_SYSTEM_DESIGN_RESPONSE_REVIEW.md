---
doc_id: ODP-SD-INTAKE-REVIEW-001
title: ODay Plus Assisted Listing Intake System Design Response Review
version: 0.2.0
status: changes-requested
owner: Product Platform Engineering
reviewers: Product / Security / Privacy / Data / Platform-SRE / Engineering / QA
reviews: ODP-SD-INTAKE-001 v0.2.0
responds_to: ODP-SD-INTAKE-ALIGN-001
reviewed_commit: 0635a45584380e1cc08093cdd10537fd64b93938
prior_reviewed_commit: ffe14c77f7d4f1ae97d301db3a8177cd3effeed6
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake System Design Response Review

## 1. Re-review Decision

System Design response `0.2.0` substantially addresses the first review. It
adds committed state, SQL, OpenAPI, authorization, event, reliability/privacy,
and migration artifacts and preserves the alignment request on the response
branch.

The second review decision remains `CHANGES_REQUESTED`. The remaining blockers
are cross-contract and executable-contract defects, not missing narrative.
They would permit cross-tenant references, block later observations/revisions,
leave required review workflows without API operations, or produce events and
errors that do not match the binding state contracts.

Engineering may prepare tests and implementation estimates from the artifacts.
Schema migrations, generated clients, event consumers, and product behavior
must not become authoritative until the P0 findings below are corrected and the
approval gates in response section 13 are recorded.

## 2. First Review Closure

| Prior finding | Result in 0.2.0 | Review note |
|---|---|---|
| `SDR-001` state contracts | PARTIAL | Required diagrams and most transition tables exist. The generic decision machine still lacks its per-transition table. |
| `SDR-002` reversible identity | SUBSTANTIALLY ADDRESSED | Immutable edges, redirects, cycle prevention, as-of reads, and reversal are defined. SQL tenant/lineage constraints still block acceptance. |
| `SDR-003` persistence schemas | NOT CLOSED | A substantial SQL artifact exists, but tenant isolation, provenance uniqueness, missing durable models, and lineage FKs remain defective. |
| `SDR-004` versioned API | NOT CLOSED | OpenAPI exists and internal refs resolve, but lint/spec errors and missing assisted-entry/reviewer operations prevent workflow execution. |
| `SDR-005` authorization matrix | SUBSTANTIALLY ADDRESSED | Deny-by-default role/scope/field/risk rules exist. OpenAPI does not expose the approval flows or all denial codes they require. |
| `SDR-006` event contract | NOT CLOSED | Envelope, delivery, replay, consumers, and webhook exclusion exist. The catalog does not cover the transition events and payload schemas are incomplete. |
| `SDR-007` privacy/evidence | ADDRESSED, APPROVAL PENDING | Purge, hold, residency, watermark/export, WORM, and failure behavior are defined; named approvals remain pending. |
| `SDR-008` reliability | ADDRESSED, APPROVAL PENDING | HA, backups, KMS, jobs, backpressure, recovery, and drills are defined; quantitative commitments and owners remain pending. |

## 3. Validation Evidence

The re-review performed the following checks against commit `0635a455`:

- all seven normative artifacts listed by the response exist in the same Git
  history as the alignment request;
- both YAML artifacts parse successfully;
- all 135 internal OpenAPI `$ref` values resolve;
- 14 API operations have unique `operationId` values;
- Redocly recommended lint fails with 19 errors and 10 warnings, including five
  Response Objects missing the required `description` field;
- the state contract references 53 versioned transition event types while the
  event catalog defines 18; 38 referenced events are absent and three catalog
  events are not referenced by the state contract;
- the SQL artifact defines 26 tables, including 24 tenant-bearing tables, but
  enables RLS on only seven, defines zero RLS policies, and defines no
  tenant-inclusive composite foreign keys; and
- all nine response approval owners remain `PENDING` with fail-closed behavior.

## 4. Blocking Findings

### SDR2-001 - P0: SQL does not enforce tenant-consistent relationships

Affected decisions: `SDI-001`, `SDI-003`, `SDI-004`, `SDI-016`, and `SDI-019`.

The schema carries `tenant_id`, but child relations reference only opaque IDs.
Examples include intake transitions, snapshots, parser runs, listing revisions,
identity edges, match records, corrections, assignments, promotion decisions,
and candidates. There is no composite foreign key of the form
`(tenant_id, parent_id) -> (tenant_id, parent_id)`.

Only seven of 24 tenant-bearing tables enable RLS, no `CREATE POLICY` or
`FORCE ROW LEVEL SECURITY` statement is committed, and the remaining tables
include the most sensitive identity, decision, correction, job, outbox, legal
hold, and export records. See
`docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql:89` and `:566`.

This permits a row to carry tenant A while referencing a parent from tenant B
if application checks fail. Opaque UUIDs and backend ABAC do not replace
database referential isolation.

Required correction:

1. Add tenant-inclusive unique keys and composite FKs for every tenant-owned
   relationship, including self-references and current pointers.
2. Enable and force RLS for every tenant table or explicitly document and test
   an approved non-RLS boundary.
3. Commit actual deny-by-default tenant policies, not migration comments.
4. Add schema tests that attempt cross-tenant inserts, updates, redirects,
   decision links, candidate links, and source/snapshot links.

### SDR2-002 - P0: Duplicate constraints prevent revision and observation flow

Affected decisions: `SDI-002`, `SDI-011`, `SDI-013`, and `SDI-023`.

`ux_intakes_exact_url_active` makes canonical URL unique for every intake whose
state is not `CANCELLED` (`SCHEMA.sql:85`). A prior `READY`, `FAILED`, or
`QUARANTINED` intake therefore prevents a later user submission that should
produce a new observation or rent/status/content revision. The intake machine
also treats `READY` as terminal, so the existing intake cannot perform that
new observation.

`source_snapshots` is unique by `(tenant_id, content_sha256, source_id)` while
each snapshot row owns one `intake_id` and one captured/observed time
(`SCHEMA.sql:115`). Re-observing identical content for another intake collides
with the first observation and loses the new provenance time/relationship.

Required correction:

1. Separate exact-match detection from uniqueness that blocks a new durable
   intake receipt.
2. Define the accepted duplicate response with existing intake/listing IDs and
   the rule for when a new observation/revision intake is allowed.
3. Separate deduplicated immutable object content from per-intake snapshot or
   observation metadata, or change uniqueness so repeated observations retain
   their own captured/observed lineage.
4. Add tests for same URL/same content, same URL/changed rent, page removal,
   relisting, and retry after failed/quarantined intake.

### SDR2-003 - P0: OpenAPI cannot execute the binding human-review workflows

Affected decisions: `SDI-006`, `SDI-008`, `SDI-013`, `SDI-014`, and `SDI-016`.

The state and authorization contracts require proposal, independent review,
approval/rejection, execution, and reversal. OpenAPI exposes proposal-style
correction/decision requests and a promotion endpoint that returns a completed
candidate receipt, but it has no decision-resource read, approve, reject,
supersede, or reversal-review operations. It also lacks an assisted-entry
completion/finalization operation that commits required fields and transitions
`AWAITING_ASSISTED_ENTRY -> PARSING`.

Consequently, identity-affecting corrections, manager-approved promotion,
merge/split/unmerge review, quarantine release, and lost-response decision
lookup cannot follow the required two-actor state machines without inventing
handlers outside the contract.

The artifact also fails Redocly recommended lint. Response Objects at OpenAPI
lines 131, 153, 177, 255, and 294 omit mandatory descriptions. All operations
lack summaries, and saved-view listing has no declared 4xx response.

Required correction:

1. Add durable decision/correction/promotion resource operations for get,
   approve, reject, execute/retry, supersede, and reversal approval as required
   by each state machine.
2. Add assisted-entry submit/finalize semantics with correction preservation.
3. Model `202 PENDING_REVIEW` separately from authoritative `201` execution
   receipts and define idempotent lookup after a lost response.
4. Remove client authority over `tenant_id` in mutation scope, or state and
   test that token-derived tenant is authoritative and mismatches are denied.
5. Make the OpenAPI artifact pass the repository-selected validation/lint gate.

### SDR2-004 - P0: API errors and enums conflict with state and authorization contracts

Affected decisions: `SDI-005`, `SDI-006`, `SDI-012`, `SDI-014`, `SDI-016`, and
`SDI-017`.

`ApiError` at OpenAPI line 692 requires only code, message, retryability, and
correlation ID. Product requirements require occurred time and next action on
every error. The enum omits binding codes used by state, authorization, and
reliability contracts, including `CANNOT_CANCEL`, `CORRECTION_INVALID`,
`REVIEW_CONFLICT`, `WORK_INCOMPLETE`, `SECOND_ACTOR_REQUIRED`,
`PROMOTION_APPROVAL_REQUIRED`, `TENANT_SCOPE_DENIED`, `RESIDENCY_DENIED`, and
`BACKPRESSURE_ACTIVE`.

`CorrectionReceipt` permits `PENDING_REVIEW`, while SQL correction status does
not. SQL permits `REJECTED`, `SUPERSEDED`, and `REVERSED`, while the receipt
does not expose those states. This prevents generated clients and state tests
from sharing one enum.

Required correction: define one canonical error-code registry and one canonical
enum source for every cross-artifact state. Generate or mechanically validate
OpenAPI, SQL checks, state tables, frontend facts, and tests against those
registries. Add `occurred_at`, `next_action`, and endpoint-specific response
codes to the error contract.

### SDR2-005 - P0: Transition events and event schemas are not closed contracts

Affected decisions: `SDI-005`, `SDI-007`, `SDI-008`, `SDI-015`, and `SDI-020`.

The state artifact names 53 emitted event types. The catalog defines 18 and
omits 38 of the named events, including intake review/quarantine/reopen,
identity execution/reversal, assignment claim/complete/escalation, promotion
validation/approval/failure, SiteScore request/failure, SLA state/completion,
and job replay. The catalog instead contains generic events such as
`intake.state_changed.v1` that the transition rows do not name.

The 15 payload entries at event YAML lines 296-326 contain only `required`
property-name arrays. They do not define `type`, `properties`, field types,
classification, nullability, or `additionalProperties`, so they are not
sufficient schemas for producers, consumers, compatibility checks, masking, or
generated types.

Required correction: either bind transition rows to the generic catalog events
with exact payload mappings or add every named event. Commit full JSON Schema
payloads, validate each catalog `schema_ref`, and add a test proving every state
event resolves to exactly one versioned catalog entry and consumer contract.

### SDR2-006 - P0: Source-policy, batch/view, and lineage persistence remain incomplete

Affected decisions: `SDI-009`, `SDI-011`, `SDI-012`, `SDI-013`, and `SDI-018`.

The source registry table lacks allowed path patterns, approved intake/retrieval
methods, explicit legal/license approval status, downstream-use/export policy,
and immutable source-policy decision records. The state contract requires a
policy decision ID, but the transition schema stores only a policy version and
has no FK to a decision record.

OpenAPI exposes durable intake batches and saved views, but the normative SQL
artifact defines neither batch/row receipts nor saved-view ownership records.
Several lineage pointers are columns without FKs, including resolved listing,
current observation, transition snapshot/match/job, and identity decision
references. See `SCHEMA.sql:68`, `:107`, `:108`, `:187`, `:244`, and `:263`.

Required correction: add the missing control-plane and API-owned durable
records, exact ownership/retention/version constraints, and all deferred lineage
FKs. If an existing platform table owns a record, name that stable contract and
its tenant/authorization boundary instead of leaving it implicit.

### SDR2-007 - P1: Generic decision machine lacks its binding transition table

Affected decision: `SDI-006`.

State contract section 6 provides a decision diagram and segregation prose but
no table naming initiator, permission, preconditions, idempotency/concurrency,
evidence, event, failure, and terminal/reopen behavior for each transition.
This is the only declared binding state machine without that table.

Add the table and cross-reference the exact OpenAPI operations, SQL states,
authorization rows, audit actions, and event types.

## 5. Approval Status

The response correctly remains `proposed`. Product/Expansion Ops, Security,
Privacy, Legal/Commercial, Data, Platform/SRE, Expansion Engineering, QA, and
Release Authority are all still `PENDING`.

This is not a documentation defect, but it prevents `approved` status and keeps
all listed production flags fail closed. No execution task may report product
or production completion from document-only artifacts or pending approvals.

## 6. Required Resubmission

System Design should publish response `0.2.1` or higher and update the affected
artifact versions with:

1. tenant-consistent composite FKs, complete RLS policies, and cross-tenant
   negative tests;
2. corrected intake/snapshot provenance and duplicate/revision semantics;
3. executable assisted-entry and two-person review API workflows;
4. a lint-valid OpenAPI contract with canonical errors and enums;
5. a complete transition-event catalog with typed payload schemas;
6. source-policy decisions, batch/view persistence, and complete lineage FKs;
7. the generic decision transition table; and
8. updated validation evidence plus remaining owner approvals/fail-closed gates.

Re-review can approve contract groups independently, but execution-task rollout
must remain contract-first: schema/OpenAPI/state/auth/event tests land before
feature behavior, migration, UX binding, or production enablement.
