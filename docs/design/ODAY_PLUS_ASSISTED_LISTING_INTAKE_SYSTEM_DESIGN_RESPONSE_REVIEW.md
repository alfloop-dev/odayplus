---
doc_id: ODP-SD-INTAKE-REVIEW-001
title: ODay Plus Assisted Listing Intake System Design Response Review
version: 0.1.0
status: changes-requested
owner: Product Platform Engineering
reviewers: Product / Security / Data / Platform / Engineering / QA
reviews: ODP-SD-INTAKE-001
responds_to: ODP-SD-INTAKE-ALIGN-001
reviewed_commit: ffe14c77f7d4f1ae97d301db3a8177cd3effeed6
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake System Design Response Review

## 1. Review Decision

The response is a substantial architecture proposal, but it is not yet an
approved, implementation-binding system design response. The review decision is
`CHANGES_REQUESTED`.

All 24 decision IDs have an `ACCEPT` or `MODIFY` disposition. The response also
selects a production topology, establishes canonical aggregate ownership,
defines quantitative capacity/SLO/RPO/RTO targets, provides migration and
rollout direction, and identifies 14 implementation work packages.

Approval is blocked because several artifacts explicitly required by
ODP-SD-INTAKE-ALIGN-001 are still represented by summaries or future work
packages instead of binding contracts. Engineering may use the response for
estimation and contract-test planning, but must not treat the missing contracts
as permission to invent state transitions, persistence constraints, API
payloads, authorization, or event behavior.

## 2. Required Deliverable Assessment

| Required deliverable | Result | Review note |
|---|---|---|
| Canonical ERD and ownership | PASS | Aggregate ownership, stores, scope keys, and ERD are present. |
| Five required state diagrams | CHANGES REQUIRED | Only the intake stage machine is diagrammed. Listing lifecycle, identity resolution, assignment/SLA, and promotion lack binding diagrams and transition matrices. |
| Source/parser/snapshot/correction/decision schemas | CHANGES REQUIRED | Source and parser fields are listed; snapshot is prose; correction and decision schemas and relational constraints are absent. |
| Versioned API contract | CHANGES REQUIRED | The response contains route inventory and common headers, not request/response/status/security schemas or a committed OpenAPI artifact. |
| Event catalog and delivery contract | CHANGES REQUIRED | Event names and generic outbox behavior exist; envelope, producer/consumer catalog, per-event partitioning, compatibility, replay, retention, and webhook signing are absent. |
| Authorization matrix | CHANGES REQUIRED | Role summaries are not a role/action/resource/field/scope/workflow-state matrix. |
| Production runtime topology | PASS | Cloud Run, Cloud SQL/PostGIS, GCS, Cloud Tasks, Pub/Sub, KMS/Secret Manager boundaries are selected. |
| Capacity, SLO, retry, RPO/RTO | CONDITIONAL | Quantitative values exist but still require the named Product/Ops/SRE, Security, Privacy, and Legal approvals. |
| Migration and rollout | CONDITIONAL | Direction and gates exist; executable mapping, reconciliation schema, ownership, and rollback runbook remain implementation handoff artifacts. |
| UX-binding system facts | PASS | Required visible states, facts, actions, errors, freshness, and deep-link behavior are present. |

## 3. Blocking Findings

### SDR-001 - State models are not binding transition contracts

Affected decisions: `SDI-002`, `SDI-003`, `SDI-005`, `SDI-006`, `SDI-007`,
and `SDI-008`.

Section 4.1 provides one intake diagram, while section 4.2 only enumerates
states. The response must add diagrams and transition tables for listing
lifecycle, identity resolution including merge/split/unmerge/reversal,
assignment plus SLA, decision review/execution/reversal, and candidate
promotion/SiteScore compensation.

Every transition row must name source state, target state, initiating actor,
backend permission, preconditions, idempotency behavior, concurrency token,
persisted evidence, audit event, emitted domain event, retry/failure result, and
whether it is terminal or reopenable. `QUARANTINED` currently has both outgoing
reopen transitions and a terminal transition to `[*]`; that ambiguity must be
removed.

### SDR-002 - Reversible identity resolution is underspecified

Affected decision: `SDI-003`.

Match thresholds and decision names do not define the effective identity graph.
The response must specify immutable edge records, effective/superseded edge
selection, canonical property redirects, reference behavior during merge,
split and unmerge, cycle prevention, concurrent resolution, rollback of a
partially applied operation, and lineage queries. Source records must remain
immutable and every downstream listing/candidate reference must have a defined
resolution rule.

### SDR-003 - Persistence schemas are incomplete

Affected decisions: `SDI-001` through `SDI-004`, `SDI-009` through `SDI-011`,
`SDI-014`, and `SDI-018`.

Field lists and the conceptual ERD are insufficient for contract/schema tests.
Provide committed schema artifacts for source registry, parser release/run,
snapshot, human correction, match case/candidate/decision, identity edge,
promotion decision, idempotency record, job, outbox, and audit event. Include
types, nullability, enums, foreign keys, tenant-inclusive unique constraints,
check constraints, version columns, indexes, retention/legal-hold fields, and
authoritative timestamps.

The correction and decision schemas must explicitly preserve parsed,
normalized, corrected, before/after, reason, reviewer, source snapshot, parser
run, and supersession/reversal lineage.

### SDR-004 - The API section is an endpoint inventory, not a versioned contract

Affected decisions: `SDI-012`, `SDI-013`, and `SDI-014`.

Commit an OpenAPI artifact with component schemas and endpoint-specific request,
success, partial-success, conflict, validation, authorization, retry, and
idempotent-replay responses. It must define cursor encoding/expiry, saved-view
ownership, batch row receipts, correction/decision payloads, merge/split/
unmerge inputs, assignment concurrency, retry checkpoints, promotion receipts,
field masking, and all declared error codes.

Generated contract tests and clients must be based on this artifact. Endpoint
handlers must not invent payloads while `ODP-INTAKE-API-001` is running.

### SDR-005 - Authorization and segregation are not testable as written

Affected decisions: `SDI-004`, `SDI-006`, `SDI-016`, `SDI-017`, and `SDI-018`.

Replace the role summary with a deny-by-default matrix covering each role and
service identity against resource, action, tenant/brand/region/area/HeatZone,
workflow state, field classification, ownership relation, and decision risk.
The matrix must name first actor/second actor combinations, self-review
prohibitions, emergency administration, export/purge/legal-hold permissions,
and the backend reason code returned for each denied or masked result.

### SDR-006 - Event and external delivery contracts are incomplete

Affected decision: `SDI-015`.

Add a versioned event envelope and event catalog with producer, transaction
owner, aggregate and partition key, ordering scope, payload schema, sensitive
field policy, consumer list, deduplication key/lifetime, retry/dead-letter,
retention, replay authority, schema compatibility, and deprecation behavior.
If webhooks are supported, define endpoint registration, HMAC/signature format,
timestamp/replay protection, retry policy, suspension, secret rotation, and
delivery evidence. If webhooks are not supported in this release, state that
explicitly and remove them from the required integration surface through an
approved alignment change.

### SDR-007 - Privacy lifecycle and evidence export remain incomplete

Affected decisions: `SDI-017` and `SDI-018`.

Retention periods are present, but deletion/purge execution, legal-hold
placement and release, data residency enforcement, subject/export scope,
watermarking, export manifests, evidence verification, and deletion conflict
behavior are not. Name the system of record, approving role, audit events, and
fail-closed behavior for each operation.

### SDR-008 - Production reliability selections need operational contracts

Affected decisions: `SDI-019`, `SDI-020`, `SDI-021`, and `SDI-022`.

Specify Cloud SQL HA/replica topology, backup/PITR retention, cross-region and
residency mode, KMS/key ownership and rotation, restore authority, and the
consistency/reconciliation boundary between SQL and GCS. The job contract must
add stage timeouts, queue concurrency/rate limits, backpressure thresholds,
operator replay authorization, cancellation checkpoints, task acknowledgement,
dead-letter retention, and alert ownership.

## 4. Publication and Approval Findings

The System Design response was committed and pushed at
`ffe14c77f7d4f1ae97d301db3a8177cd3effeed6` on
`origin/agent/assisted-listing-intake-system-design`.

That branch did not contain the alignment request it names as a normative
source. This review branch composes the response with alignment commit
`6ae5c6a97828c69382fbd79793dc9a7d70f03ba6`; the final integration branch or PR
must preserve both documents.

The response correctly remains `proposed`. Product, Security/Privacy, Data,
Platform/SRE, Expansion Engineering, QA, Legal where applicable, and the named
release authority must record approval before status changes to `approved`.
Capacity/SLO values must not be presented as contractual commitments before
their listed owners approve them.

## 5. Required Resubmission

System Design should publish version `0.2.0` of the response with:

1. the missing diagrams and per-transition contract tables;
2. exact persistence schemas and constraints;
3. a committed OpenAPI contract and examples;
4. the authorization/segregation matrix;
5. the event envelope, catalog, consumer and webhook decision;
6. privacy lifecycle, evidence export, and operational reliability contracts;
7. links to every committed artifact from the applicable decision row; and
8. explicit approval records or remaining fail-closed gates.

Re-review may approve individual contract groups, but product readiness remains
blocked until every P0 decision is both answered and backed by its required
artifact. Engineering execution tasks may then be generated from stable schema,
state, API, authorization, event, and runtime boundaries.
