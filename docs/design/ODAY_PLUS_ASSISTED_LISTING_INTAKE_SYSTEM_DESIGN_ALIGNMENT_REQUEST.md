---
doc_id: ODP-SD-INTAKE-ALIGN-001
title: ODay Plus Assisted Listing Intake System Design Alignment Request
version: 0.1.0
status: awaiting-system-design-response
owner: Product / Expansion Operations
request_owner: Product Platform Engineering
response_owner: System Design
related_product_requirement: ODP-UXD-003-ADD-001
related_engineering_task: ODP-EXT-002
response_document: docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md
updated_at: 2026-07-16
---

# ODay Plus Assisted Listing Intake System Design Alignment Request

## 1. Assignment

The System Design team must define the target architecture and binding contracts
for the Assisted Listing Intake product flow before Product Design or
Engineering expands the current implementation. This request converts the open
product questions into explicit architecture decisions. It is not a request for
screen layouts, visual styling, or implementation code.

The response must be published as:

`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md`

The response may reference ADRs, schemas, OpenAPI artifacts, state diagrams, or
runtime topology documents, but those references must be committed and stable.

## 2. Source Context

Primary product requirement:

- `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_DESIGN_REQUIREMENTS.md`

Related product and interaction contracts:

- `docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`
- `docs/design/ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md`
- `docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md`
- `docs/evidence/fleet_dispatch/ODP-EXT-002-R5-ADDENDUM.md`
- `docs/evidence/fleet_dispatch/ODP-EXT-002.md`

Existing code and tests are implementation evidence, not architecture authority.
The System Design response may retain, revise, or replace existing contracts,
but it must state compatibility and migration effects when it does so.

## 3. Non-Negotiable Product Boundaries

The response must preserve these product constraints:

1. Intake begins from a user-submitted URL, manual entry, CSV, or an approved
   feed. It must not imply continuous crawling, result-page scraping, or
   automatic enumeration of third-party listing IDs.
2. Retrieval is allowed only after a source-policy decision. Unknown or
   prohibited sources fail closed.
3. The product never asks users to enter provider credentials, cookies, bearer
   tokens, or private API endpoints.
4. Ambiguous identity matches are never auto-merged.
5. Promotion to Candidate Site always requires an explicit human decision.
6. High-impact corrections, merge, split, unmerge, reject, quarantine, and
   promotion are non-optimistic, attributable, idempotent, and auditable.
7. Original source evidence, canonical identity, parser version, and human
   corrections remain distinguishable throughout the record lifetime.
8. Tenant, region, role, field-level sensitivity, and governance boundaries are
   enforced by backend policy, not by frontend visibility alone.

## 4. Required Response Method

For every decision ID in section 5, the System Design team must provide:

- `decision`: `ACCEPT`, `MODIFY`, `DEFER`, or `REJECT`.
- The selected architecture or contract, including exact states and ownership.
- Alternatives considered and why the selected option was chosen.
- Affected services, data stores, schemas, APIs, events, and jobs.
- Authorization, audit, privacy, failure, and recovery behavior.
- Backward compatibility and migration/backfill impact.
- UX-visible implications that Product Design must represent.
- Decision owner, dependent team, and unresolved external dependency.

`DEFER` is not acceptable for a P0 item unless the response names a fail-closed
interim behavior, a follow-up task, an owner, and a release gate that prevents
the incomplete capability from being presented as production-ready.

## 5. Architecture Decisions Required

### 5.1 Canonical Data and Identity

| ID | Priority | Decision required | Required answer |
|---|---|---|---|
| `SDI-001` | P0 | Canonical aggregates and ownership | Define `Property`, `Listing`, `ListingRevision`, `ListingObservation`, `Intake`, `SourceSnapshot`, `MatchDecision`, `CandidateSite`, and their aggregate/service ownership. Provide identifiers, cardinalities, tenant keys, and authoritative write owner. |
| `SDI-002` | P0 | Listing and observation lifecycle | Define how active, revised, removed, expired, relisted, stale, quarantined, and archived records differ. Specify which changes create a revision, observation, status transition, or new listing. |
| `SDI-003` | P0 | Identity graph and reversible resolution | Define exact duplicate groups, possible matches, canonical property identity, merge, split, unmerge, reversal, and supersession. State whether source records remain immutable and how references are redirected without losing lineage. |
| `SDI-004` | P0 | Ownership and tenancy | Define tenant, brand, region, assigned area, HeatZone, submitter, queue owner, and data-steward ownership. State the isolation keys required on every persisted intake, listing, snapshot, decision, and candidate reference. |

### 5.2 Workflow and State Machines

| ID | Priority | Decision required | Required answer |
|---|---|---|---|
| `SDI-005` | P0 | Intake processing state machine | Confirm or revise the 11 product stages. Define legal transitions, terminal and retryable states, timeout behavior, cancellation, replay, concurrent actor behavior, and how stage history is persisted. |
| `SDI-006` | P0 | Decision state machine | Define create, revise, duplicate, quarantine, reject, reopen, merge, split, unmerge, and promote preconditions. Identify which decisions require reason, risk acknowledgement, independent reviewer, or segregation of duties. |
| `SDI-007` | P0 | Assignment and SLA workflow | Define unassigned, assigned, claimed, transferred, overdue, escalated, and completed states; ownership concurrency; due-time calculation; queue routing; reminders; handoff; and escalation events. |
| `SDI-008` | P0 | Candidate promotion transaction | Define the atomic or saga boundary for `Intake -> Listing -> Candidate Site -> SiteScore`. Include idempotency, partial failure, compensation, retry, duplicate candidate prevention, and authoritative success response. |

### 5.3 Source and Parser Control Plane

| ID | Priority | Decision required | Required answer |
|---|---|---|---|
| `SDI-009` | P0 | Source registry and policy ownership | Define source identity, allowed hosts, canonicalization rules, retrieval mode, legal/license approval, owner, review expiry, rate limits, kill switch, and fail-closed policy evaluation. |
| `SDI-010` | P0 | Parser registry and release lifecycle | Define parser package identity, schema compatibility, parser version, test corpus, validation gate, canary, rollback, deprecation, source binding, and reprocessing rules. |
| `SDI-011` | P0 | Snapshot and provenance contract | Define raw/redacted snapshot storage, immutable identity, checksum, observed/captured times, retention class, parser input reference, correction lineage, and access/export restrictions. |

### 5.4 API, Query, and Event Contracts

| ID | Priority | Decision required | Required answer |
|---|---|---|---|
| `SDI-012` | P0 | Listing Inbox query contract | Define server pagination, stable ordering, cursor semantics, filters, search, saved views, result counts, field masking, freshness, and consistency guarantees. |
| `SDI-013` | P0 | Unified intake contract | Define the common envelope and source-specific extensions for URL, manual, CSV, and approved-feed intake. Specify batch identity, per-row result, partial success, validation, and replay semantics. |
| `SDI-014` | P0 | Mutation concurrency and idempotency | Define idempotency-key scope, actor/tenant binding, key lifetime, request fingerprinting, response replay, optimistic concurrency token or version, conflict response, and lost-response recovery. |
| `SDI-015` | P1 | Domain events and external integration | Define event names, versions, partition keys, ordering, delivery guarantees, outbox ownership, deduplication, replay, webhook signing, and consumers for listing, candidate, task, audit, and analytics integrations. |

### 5.5 Security, Privacy, and Governance

| ID | Priority | Decision required | Required answer |
|---|---|---|---|
| `SDI-016` | P0 | Authorization and segregation matrix | Define backend permissions for Expansion staff, Expansion manager, Data steward, Governance reviewer, service identities, and administrators across view, submit, correct, assign, decide, merge, split, promote, export, and purge actions. |
| `SDI-017` | P0 | Sensitive data policy | Classify broker/owner contact data, addresses, source evidence, commercial terms, credentials, and private notes. Define collection minimization, masking, purpose binding, retention, deletion, legal hold, residency, and export watermark rules. |
| `SDI-018` | P0 | Decision and evidence integrity | Define audit event schema, before/after representation, source and parser references, immutable/WORM boundary, chain or signature verification, legal-hold governance, evidence export, and audit failure behavior. |

### 5.6 Persistence, Jobs, and Reliability

| ID | Priority | Decision required | Required answer |
|---|---|---|---|
| `SDI-019` | P0 | Production storage topology | Select the production relational store, object/snapshot store, search/index requirement, transaction boundaries, tenant isolation method, replication, backup, PITR, and data-encryption ownership. State the role of SQLite and in-memory modes after rollout. |
| `SDI-020` | P0 | Asynchronous job contract | Define queue technology, job identity, lease/fencing, heartbeat, retry/backoff, timeout, dead-letter handling, poison record isolation, operator replay, cancellation, and backpressure. |
| `SDI-021` | P0 | Capacity and service levels | Ratify quantitative intake volume, batch size, peak concurrency, queue-age, submit/read/write latency, parser completion, review SLA, availability, and error-budget targets. Include measurement source and owner. |
| `SDI-022` | P0 | Recovery objectives | Define RPO/RTO for intake records, snapshots, decisions, listing revisions, candidate promotion, queues, and audit evidence. Specify restore ordering, reconciliation, replay boundaries, and required drills. |

### 5.7 Migration and Rollout

| ID | Priority | Decision required | Required answer |
|---|---|---|---|
| `SDI-023` | P0 | Migration and reconciliation | Define migration from current intake/listing representations to the target model, schema versioning, backfill, source replay, identity reconciliation, dry run, checksum/count proof, rollback, and ownership of irreconcilable records. |
| `SDI-024` | P0 | Feature rollout and cutover | Define feature flags, tenant/source canary, shadow processing, dual read/write policy if any, acceptance metrics, kill switch, rollback trigger, release authority, and removal of legacy/fixture fallback paths. |

## 6. Required System Design Deliverables

The response is incomplete without all of the following:

1. Canonical ERD with aggregate and service ownership.
2. Intake, listing lifecycle, identity-resolution, assignment/SLA, and
   promotion state diagrams.
3. Source registry, parser registry, snapshot, correction, and decision schemas.
4. Versioned API contract for detail, query, bulk intake, correction, decision,
   identity resolution, assignment, retry, and promotion.
5. Event catalog and outbox/webhook delivery contract.
6. Role/action/field/tenant authorization matrix.
7. Production storage, queue, worker, evidence, observability, and external
   provider topology.
8. Capacity model, SLO/error-budget table, retry budgets, and RPO/RTO table.
9. Migration, backfill, reconciliation, canary, cutover, and rollback plan.
10. A list of system facts Product/UX Design must expose, including exact
    states, actions, conflicts, permissions, timestamps, freshness, and errors.

## 7. Response Template

The System Design response should use this structure:

```markdown
---
doc_id: ODP-SD-INTAKE-001
title: ODay Plus Assisted Listing Intake System Design Response
version: 0.1.0
status: proposed
owner: System Design
reviewers: Product / Security / Data / Platform / Engineering
responds_to: ODP-SD-INTAKE-ALIGN-001
updated_at: YYYY-MM-DD
---

# ODay Plus Assisted Listing Intake System Design Response

## 1. Executive Decision
## 2. Context and Constraints
## 3. Canonical Domain and ERD
## 4. State Machines
## 5. Source and Parser Control Plane
## 6. APIs, Events, and Jobs
## 7. Security, Privacy, and Evidence
## 8. Persistence and Runtime Topology
## 9. SLO, Capacity, and Recovery
## 10. Migration and Rollout
## 11. UX-Binding System Facts
## 12. Decision Matrix

| Decision ID | Decision | Contract / reference | Rationale | Migration impact | Owner | Open dependency |
|---|---|---|---|---|---|---|
| SDI-001 | ACCEPT / MODIFY / DEFER / REJECT | ... | ... | ... | ... | ... |

## 13. Open Questions and Required Approvals
## 14. Implementation Handoff Boundaries
```

## 8. Response Acceptance Criteria

- All P0 decision IDs have a concrete answer or a governed fail-closed defer
  plan with task, owner, date/gate, and release impact.
- Every state machine names legal transitions, initiating actor, permission,
  precondition, idempotency behavior, persisted evidence, and failure result.
- Every high-impact mutation defines concurrency, lost-response replay,
  independent-review requirements, and audit behavior.
- ERD and API contracts distinguish source evidence, normalized data, manual
  correction, listing revision, and canonical property identity.
- Storage and queue selections are explicit; `memory`, local files, or SQLite
  are not presented as production topology unless the response proves the
  required capacity, HA, isolation, and recovery properties.
- SLO, capacity, RPO, and RTO values are quantitative and have an approving
  owner. Test-selected numbers are not treated as product requirements.
- Privacy and source-license decisions cover both stored data and exports.
- Migration and rollout preserve audit lineage and provide reconciliation and
  rollback evidence.
- Product/UX Design can proceed without inventing states, permissions, error
  semantics, lifecycle rules, or data ownership.
- Engineering can split implementation into independently testable tasks with
  stable service and schema boundaries.

## 9. Handoff Sequence

```text
Product requirement accepted
-> System Design response proposed
-> Product + Security + Data + Platform review
-> System Design response approved
-> UX flow and visual handoff updated
-> Engineering execution tasks generated
-> Contract/schema tests land before feature implementation
-> Staging migration and product acceptance
-> Governed rollout
```

Visual design must not begin final high-impact action composition until
`SDI-005`, `SDI-006`, `SDI-007`, `SDI-008`, `SDI-016`, and `SDI-017` are
approved. Engineering must not productionize storage or async processing until
`SDI-019` through `SDI-024` are approved.

## 10. Out of Scope for This Response

- Pixel-level layout, visual tokens, typography, spacing, or final interface
  copy.
- Automatic crawling or recurring external result-page polling.
- Provider credential entry through the product UI.
- Selection of a specific third-party listing provider without commercial and
  legal approval.
- Claiming production readiness from deterministic fixture or mock-provider
  evidence.
