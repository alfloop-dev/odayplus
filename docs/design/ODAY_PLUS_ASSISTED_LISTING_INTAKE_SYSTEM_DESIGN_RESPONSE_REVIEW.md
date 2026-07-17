---
doc_id: ODP-SD-INTAKE-REVIEW-002
title: ODay Plus Assisted Listing Intake System Design Response v0.2.0 Re-review
version: 0.2.0
status: changes-requested
verdict: CHANGES_REQUESTED
owner: Product Platform Engineering
reviewers: Product / Security / Data / Platform / Engineering / QA
reviews: ODP-SD-INTAKE-001
response_version: 0.2.0
reviewed_commit: 0635a45584380e1cc08093cdd10537fd64b93938
supersedes_review: ODP-SD-INTAKE-REVIEW-001
responds_to: ODP-SD-INTAKE-ALIGN-001
repository: alfloop-dev/odayplus
base_branch: dev
pull_request: 319
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake System Design Response v0.2.0 Re-review

## 1. Target Verification and Scope

This is a fresh review of `ODP-SD-INTAKE-001` version `0.2.0`. It does not use
the findings or conclusions from `ODP-SD-INTAKE-REVIEW-001` as review evidence.

Review began from a detached worktree. Before any artifact was inspected:

```text
$ git rev-parse HEAD
0635a45584380e1cc08093cdd10537fd64b93938
```

The verified commit matches PR #319's required head. All nine requested
artifacts are tracked at that commit and were inspected:

| Artifact | Review use |
|---|---|
| `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_ALIGNMENT_REQUEST.md` | Required decisions, deliverables, and acceptance criteria |
| `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md` | v0.2.0 decisions, ownership, artifact register, and approval gates |
| `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md` | State diagrams, transitions, identity resolution, and promotion saga |
| `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md` | Backend authorization, masking, scope, risk, and segregation |
| `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql` | PostgreSQL types, constraints, relationships, tenancy, and evidence records |
| `docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml` | Versioned API operations, payloads, responses, concurrency, and errors |
| `docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml` | Envelope, catalog, payload schemas, delivery, and webhook decision |
| `docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_RELIABILITY_PRIVACY_CONTRACT.md` | Topology, jobs, privacy, SLO, recovery, and evidence operations |
| `docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md` | Migration, reconciliation, canary, cutover, and rollback |

## 2. Review Decision

The v0.2.0 resubmission is materially more complete. It supplies every requested
artifact, six state diagrams, a deny-by-default authorization matrix, a
production topology, privacy/reliability contracts, and a governed rollout
runbook. External webhooks are explicitly excluded from v1.

The decision is nevertheless `CHANGES_REQUESTED`. The committed contracts still
permit cross-tenant relationships, prevent legitimate revision/observation
captures, omit executable human-review API transitions, contain an invalid
OpenAPI Response shape, and leave most transition events outside the event
catalog. These are implementation-binding defects in the reviewed commit, not
unverified carryovers from the superseded review.

## 3. SDR-001 Through SDR-008 Reassessment

| Original finding | Result at v0.2.0 | Verified evidence |
|---|---|---|
| `SDR-001` state transition contracts | PARTIAL | State contracts sections 2, 3, 4, 5, and 7 contain diagrams and transition tables. Section 6 has a decision diagram but no per-transition table. |
| `SDR-002` reversible identity resolution | CONTRACT PRESENT | State contracts section 4 defines immutable/effective/superseded edges, redirects, merge/split/unmerge, recursive cycle rejection, SQL rollback, compensating reversal, effective reads, and as-of lineage. Database tenancy and lineage-reference enforcement remain blocked by `SDR2-001` and `SDR2-006`. |
| `SDR-003` persistence schemas | CHANGES REQUIRED | The SQL artifact contains the requested record types and many constraints, but tenant-consistent FKs/RLS, repeat observation semantics, source-policy decisions, and API-backed batch/view persistence remain incomplete. |
| `SDR-004` versioned API | CHANGES REQUIRED | Fourteen operations exist, but the API cannot execute several binding state transitions or independent approvals. Recommended OpenAPI validation reports structural errors. |
| `SDR-005` authorization and segregation | RESOLVED IN DOCUMENT | Authorization matrix sections 1 through 8 are deny-by-default and cover principal, action/resource, scope, workflow state, field class, risk, second actor, self-review, emergency access, and reason codes. API error and workflow coverage remain inconsistent under `SDR2-003` and `SDR2-004`. |
| `SDR-006` events and external delivery | CHANGES REQUIRED | Envelope/delivery/webhook-exclusion contracts exist, but the catalog and payload schemas do not cover the events required by the binding state transitions. |
| `SDR-007` privacy lifecycle and evidence export | RESOLVED WITH APPROVAL GATE | Reliability/privacy sections 5 and 6 define purge ordering, legal hold, residency, export scope, watermark, manifest, verification, conflicts, and audit failure behavior. Privacy/Legal/Security approvals remain pending and fail closed. |
| `SDR-008` production reliability | RESOLVED WITH APPROVAL GATE | Reliability sections 1 through 4 define Cloud SQL HA/PITR/CMEK, SQL/GCS reconciliation, fencing, timeout, backpressure, replay, cancellation, DLQ, alerts, SLO, RPO/RTO, restore order, and drill cadence. Named approvals remain pending. |

## 4. Blocking Findings

### SDR2-001 - P0: SQL does not enforce tenant-consistent relationships

`ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md` section 3.1 says
tenant-inclusive uniqueness and backend ABAC are mandatory. Authorization matrix
section 3 requires `principal.tenant_id == resource.tenant_id`.

The SQL artifact contains 26 tables, 24 of which carry `tenant_id`, but its final
RLS section enables RLS on only seven tables. It defines no `CREATE POLICY` and
no `FORCE ROW LEVEL SECURITY`. Foreign keys use only the referenced UUID, for
example:

- SQL section `expansion.listings`: `property_id -> properties(property_id)`;
- SQL section `listing_revisions`: `listing_id -> listings(listing_id)`;
- SQL section `match_candidates`: match, property, and listing references;
- SQL section `promotion_decisions`: intake, listing, and property references;
- SQL section `candidate_sites`: property, listing, and promotion references.

No tenant-bearing child uses a composite `(tenant_id, referenced_id)` foreign
key. A row can therefore claim tenant A while referencing an aggregate owned by
tenant B, and most child tables have no database RLS barrier.

Required correction: add tenant-inclusive candidate keys and composite foreign
keys for every tenant-owned relationship, enable and force RLS with committed
policies on every tenant table, and add negative schema tests for cross-tenant
insert/update/read paths. If a table is intentionally global, name and constrain
that exception explicitly.

### SDR2-002 - P0: uniqueness rules block revision and observation intake

In SQL `intake.intakes`, `ux_intakes_exact_url_active` is unique across every
state except `CANCELLED`. A terminal `READY` intake therefore prevents any later
intake for the same tenant/source/canonical URL. State contracts section 3,
however, permits listing revision, relisting, and fresh observations. The
current intake state machine has no transition that reprocesses a `READY`
intake, so a changed listing at the same URL cannot enter that lifecycle.

SQL `intake.source_snapshots` also makes
`(tenant_id, content_sha256, source_id)` unique while storing one `intake_id`,
`captured_at`, and `observed_at`. A later unchanged observation collides instead
of preserving a new observation time and provenance record.

Required correction: separate request idempotency/exact-match lookup from
historical intake identity, and separate immutable content-object deduplication
from capture/observation records. Add contract tests for same-URL revision,
unchanged recapture, relisting, and lost-response replay.

### SDR2-003 - P0: OpenAPI cannot execute the binding human-review workflows

OpenAPI paths section defines 14 operations, but state contracts sections 2, 5,
6, and 7 require transitions for assisted-entry completion, cancellation,
reopen, assignment claim/transfer/accept/escalate/complete, correction review,
decision approval/rejection/supersession/reversal, promotion approval, job
inspection, and cancellation. The API contains only correction proposal, one
match-decision creation operation, one assignment update shape, retry, and one
promotion operation. It has no durable get/approve/reject endpoints for the
correction and promotion decisions that require independent actors.

The promotion path returns `201 PromotionReceipt` with candidate and SiteScore
job IDs directly, while authorization matrix sections 4 through 6 require a
staff proposal and manager approval, or two managers when a manager proposed.
The API does not represent that two-step durable decision lifecycle.

Recommended Redocly validation also fails with 19 errors and 10 warnings. Five
Response Objects at OpenAPI paths lines 131, 153, 177, 255, and 294 omit the
required `description` field. All 14 operations omit `summary`; the saved-view
list operation has no 4xx response.

Required correction: add state-oriented command/read contracts for every human
and operator transition, use durable decision resources for proposal and
independent approval, represent pending receipts separately from completed
promotion receipts, and make the OpenAPI artifact pass the repository-selected
lint/validation gate.

### SDR2-004 - P0: API errors and correction states conflict with other contracts

System design response section 11 requires every error to expose summary, next
action, code, correlation ID, occurred time, retryability, and applicable
state/version. OpenAPI `components.schemas.ApiError` requires only `code`,
`message`, `retryable`, and `correlation_id`; it has no `next_action` or
`occurred_at`.

Its error enum also omits codes required by the committed state, authorization,
and reliability artifacts, including `CANNOT_CANCEL`, `CORRECTION_INVALID`,
`REVIEW_CONFLICT`, `WORK_INCOMPLETE`, `JOB_FENCE_REJECTED`,
`SECOND_ACTOR_REQUIRED`, `PROMOTION_APPROVAL_REQUIRED`,
`TENANT_SCOPE_DENIED`, `RESIDENCY_DENIED`, and `BACKPRESSURE_ACTIVE`.

OpenAPI `CorrectionReceipt.status` permits `PENDING_REVIEW`, while SQL
`intake.human_corrections.status` does not. SQL permits `REJECTED`,
`SUPERSEDED`, and `REVERSED`, while the receipt cannot return them.

Required correction: establish one canonical error-code registry and one
canonical enum source used by SQL, OpenAPI, state tests, and generated clients.
Add `occurred_at`, `next_action`, current state/version, and retry guidance to
the error schema where required.

### SDR2-005 - P0: transition events are absent from the binding catalog

State contracts sections 2 through 7 reference 53 unique versioned event names.
Event YAML `catalog` defines 18. Cross-file comparison leaves 38 state events
without catalog entries and payload schemas. Missing groups include:

- 13 intake events, including assisted entry, policy, retrieval, review,
  quarantine, reopen, resolution, failure, and cancellation;
- 5 identity execution/reversal events and 2 match-case events;
- 4 listing lifecycle events;
- 4 assignment events and 2 SLA events;
- 5 candidate events and 2 SiteScore events;
- `job.replay_requested.v1`.

The catalog additionally contains three generic events not named by state
transitions: `intake.state_changed.v1`, `job.dead_lettered.v1`, and
`audit.event_recorded.v1`. They do not define whether they replace the missing
specific events.

Event YAML `payloads` section lists only `required` field names. It supplies no
payload `type`, `properties`, field types/nullability, classification, or
`additionalProperties` policy, despite transport section naming committed JSON
Schema as the release contract. Reliability/privacy section 5.3 also requires
legal-hold events that are absent from the catalog.

The v1 external-webhook exclusion itself passes review: event YAML `webhooks`
sets `supported_in_release: false`, disables registration/outbound delivery,
and requires a separate alignment/ADR for future support.

Required correction: either catalog every emitted state event or formally map
specific transition names to an approved generic event contract. Commit typed
payload schemas for every catalog entry and add state-to-catalog completeness,
schema compatibility, sensitive-field, and consumer replay tests.

### SDR2-006 - P0: source-policy, batch/view, and lineage persistence is incomplete

SQL `intake.source_registry` may be set to `APPROVED_RETRIEVAL` and production
enabled while legal/license references and `review_expires_at` are null. No
constraint binds approved retrieval to current approval evidence. State
contracts section 2 requires an immutable policy decision ID for retrieval and
quarantine transitions, but the SQL artifact has no source-policy decision
record; a stage transition stores only `source_policy_version`.

OpenAPI defines durable batch receipts and saved views, but the SQL artifact has
no batch, per-row receipt, or saved-view record. Several declared lineage
references also have no FK: intake resolved listing, listing current
observation, transition snapshot/match/job, identity edge/redirect decision,
promotion SiteScore job, and audit snapshot/parser/decision references.

Required correction: persist immutable source-policy decisions with approval,
expiry, selected rule version, reason, actor/service, and evidence. Add durable
batch/row and saved-view ownership records, or explicitly select another
authoritative store. Close lineage references with tenant-consistent FKs or a
documented polymorphic-integrity mechanism and reconciliation contract.

### SDR2-007 - P1: generic decision state machine has no transition contract

State contracts section 6 diagrams `DRAFT`, `PENDING_REVIEW`, `APPROVED`,
`REJECTED`, `EXECUTING`, `EXECUTED`, `FAILED`, `REVERSAL_PENDING`, `REVERSED`,
and `SUPERSEDED`, but supplies only a prose segregation paragraph. Across the
file there are six diagrams and five per-transition tables; section 6 is the
missing table.

Required correction: add one row per legal decision transition with initiator,
permission, precondition, idempotency, concurrency token, persisted evidence,
audit event, domain event, failure result, and terminal/reopen behavior. Reconcile
those rows with correction, match, promotion, purge, legal-hold, and API enums.

## 5. Validation Evidence

| Check | Result |
|---|---|
| Exact target | PASS: `git rev-parse HEAD` returned the required full SHA |
| Requested artifacts | PASS: 9/9 tracked at the reviewed commit |
| State diagrams / transition tables | 6 diagrams / 5 transition tables |
| OpenAPI operation IDs | 14 unique operation IDs |
| OpenAPI recommended lint | FAIL: 19 errors, 10 warnings |
| Event YAML syntax | PASS |
| State-event/catalog comparison | 53 referenced / 18 cataloged / 38 missing / 3 catalog-only |
| SQL tenancy structure | 26 tables / 24 tenant tables / 7 RLS enabled / 0 policies / 0 forced RLS / 0 tenant-composite FKs |
| Approval records | 9 owners remain `PENDING` with documented fail-closed gates |

PostgreSQL execution validation was not run because `psql` is unavailable in
the review environment. This does not affect the static relationship and
constraint findings above.

## 6. Required Resubmission

System Design should submit `0.2.1` or later with:

1. tenant-consistent keys, foreign keys, RLS policies, and tests;
2. repeat intake/snapshot observation semantics that permit revision history;
3. complete proposal, approval, reversal, assignment, cancellation, and replay APIs;
4. valid OpenAPI responses and unified errors/enums;
5. complete typed event catalog or an explicit generic-event mapping;
6. immutable source-policy decisions, batch/view persistence, and closed lineage;
7. the missing generic decision transition table; and
8. updated validation evidence and still-pending approval gates.

The response may remain `proposed` while named organizational approvals are
pending, but implementation must not treat the current SQL, API, or event files
as complete production contracts.

## 7. Final Verdict

`CHANGES_REQUESTED`
