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

The System Design team must define the target architecture and binding contracts for the Assisted Listing Intake product flow before Product Design or Engineering expands the current implementation. This request converts open product questions into explicit architecture decisions. It is not a request for screen layouts, visual styling, or implementation code.

The response must be published as:

`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md`

Referenced ADRs, schemas, OpenAPI artifacts, state diagrams, and runtime topology documents must be committed and stable.

## 2. Source Context

Primary product requirement:

- `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_DESIGN_REQUIREMENTS.md`

Related contracts:

- `docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`
- `docs/design/ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md`
- `docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md`
- `docs/evidence/fleet_dispatch/ODP-EXT-002-R5-ADDENDUM.md`
- `docs/evidence/fleet_dispatch/ODP-EXT-002.md`

Existing code and tests are implementation evidence, not architecture authority. The response may retain, revise, or replace existing contracts, but must state compatibility and migration effects.

## 3. Non-Negotiable Product Boundaries

1. Intake begins from a user-submitted URL, manual entry, CSV, or approved feed; no continuous crawling, result-page scraping, or automatic enumeration of third-party listing IDs.
2. Retrieval is allowed only after a source-policy decision; unknown or prohibited sources fail closed.
3. The product never requests provider credentials, cookies, bearer tokens, or private API endpoints.
4. Ambiguous identity matches are never auto-merged.
5. Promotion to Candidate Site always requires an explicit human decision.
6. High-impact corrections, merge, split, unmerge, reject, quarantine, and promotion are non-optimistic, attributable, idempotent, and auditable.
7. Original source evidence, canonical identity, parser version, and human corrections remain distinguishable for the record lifetime.
8. Tenant, region, role, field sensitivity, and governance boundaries are enforced by backend policy.

## 4. Required Response Method

For every decision ID, provide `ACCEPT`, `MODIFY`, `DEFER`, or `REJECT`; selected contract and exact ownership; alternatives; affected services/stores/schemas/APIs/events/jobs; authorization/audit/privacy/failure/recovery; migration impact; UX-visible implications; owner, dependent team, and external dependency. A P0 `DEFER` requires fail-closed interim behavior, task, owner, gate, and release impact.

## 5. Architecture Decisions Required

| ID | Priority | Decision required |
|---|---|---|
| SDI-001 | P0 | Canonical aggregates and ownership |
| SDI-002 | P0 | Listing and observation lifecycle |
| SDI-003 | P0 | Identity graph and reversible resolution |
| SDI-004 | P0 | Ownership and tenancy |
| SDI-005 | P0 | Intake processing state machine |
| SDI-006 | P0 | Decision state machine |
| SDI-007 | P0 | Assignment and SLA workflow |
| SDI-008 | P0 | Candidate promotion transaction |
| SDI-009 | P0 | Source registry and policy ownership |
| SDI-010 | P0 | Parser registry and release lifecycle |
| SDI-011 | P0 | Snapshot and provenance contract |
| SDI-012 | P0 | Listing Inbox query contract |
| SDI-013 | P0 | Unified intake contract |
| SDI-014 | P0 | Mutation concurrency and idempotency |
| SDI-015 | P1 | Domain events and external integration |
| SDI-016 | P0 | Authorization and segregation matrix |
| SDI-017 | P0 | Sensitive data policy |
| SDI-018 | P0 | Decision and evidence integrity |
| SDI-019 | P0 | Production storage topology |
| SDI-020 | P0 | Asynchronous job contract |
| SDI-021 | P0 | Capacity and service levels |
| SDI-022 | P0 | Recovery objectives |
| SDI-023 | P0 | Migration and reconciliation |
| SDI-024 | P0 | Feature rollout and cutover |

## 6. Required Deliverables

1. Canonical ERD with aggregate and service ownership.
2. Intake, listing lifecycle, identity-resolution, assignment/SLA, and promotion state diagrams.
3. Source registry, parser registry, snapshot, correction, and decision schemas.
4. Versioned API contract for detail, query, bulk intake, correction, decision, identity resolution, assignment, retry, and promotion.
5. Event catalog and outbox/webhook delivery contract.
6. Role/action/field/tenant authorization matrix.
7. Production storage, queue, worker, evidence, observability, and external-provider topology.
8. Capacity model, SLO/error-budget, retry budgets, and RPO/RTO.
9. Migration, backfill, reconciliation, canary, cutover, and rollback plan.
10. UX-binding facts including exact states, actions, conflicts, permissions, timestamps, freshness, and errors.

## 7. Acceptance Criteria

- Every P0 has a concrete answer or governed fail-closed defer.
- Every transition names actor, permission, precondition, idempotency, evidence, audit, event, and failure result.
- High-impact mutations define concurrency, lost-response replay, independent review, and audit.
- ERD/API distinguish source evidence, normalized data, corrections, revisions, and canonical property identity.
- Production storage and queue are explicit; memory/files/SQLite are not production topology.
- SLO, capacity, RPO, and RTO are quantitative and owner-approved before becoming commitments.
- Privacy/source-license rules cover storage and exports.
- Migration preserves audit lineage and has reconciliation and rollback evidence.
- Product/UX and Engineering do not invent states, payloads, permissions, or ownership.

## 8. Handoff Sequence

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

Visual design must not finalize high-impact action composition until SDI-005/006/007/008/016/017 are approved. Engineering must not productionize storage or async processing until SDI-019 through SDI-024 are approved.

## 9. Out of Scope

- Pixel-level design and final copy.
- Automatic crawling or recurring result-page polling.
- Provider credential entry in the product UI.
- Selecting a third-party provider without commercial/legal approval.
- Claiming production readiness from fixture or mock-provider evidence.
