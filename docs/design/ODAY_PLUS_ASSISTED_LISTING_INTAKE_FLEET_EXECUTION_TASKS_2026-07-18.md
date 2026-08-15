---
doc_id: ODP-INTAKE-FLEET-EXEC-001
title: ODay Plus Assisted Listing Intake Fleet Execution Tasks
version: 1.0.0
status: approved-for-dispatch
owner: Product Platform Engineering
approved_response_commit: e644bd0e01a3f9134ee0230490577db4f67b0aa9
approval_review_commit: f646afca88292cab2c5276ff39d97baf02866f3c
target_branch: dev
updated_at: 2026-07-18
---

# ODay Plus Assisted Listing Intake Fleet Execution Tasks

## 1. Objective

Implement the approved Assisted Listing Intake system design as production
code, tests, migrations, generated contracts, runtime evidence, and governed
release artifacts. This packet materializes the 14 implementation handoff
boundaries in `ODP-SD-INTAKE-001` section 14.

The machine-readable assignment and full acceptance contract is:

`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_FLEET_EXECUTION_TASKS_2026-07-18.json`

## 2. Binding Baseline

| Fact | Value |
|---|---|
| Reviewed design SHA | `e644bd0e01a3f9134ee0230490577db4f67b0aa9` |
| Approval review SHA | `f646afca88292cab2c5276ff39d97baf02866f3c` |
| System Design merge | PR `#319`, merge `15e20b5a81cdfa23a1d241eff85dac9a5d799b7a` |
| Target | `dev` |
| Worker branch | `task/<task-id>` |
| Evidence | `docs/evidence/completion/<task-id>/` |

The response, schema stack, state contracts, OpenAPI bundle, authorization
matrix, event package, reliability/privacy contract, rollout runbook, and
review manifest are binding. A worker must raise a contract conflict instead
of inventing a different state, payload, permission, schema, or failure mode.

## 3. Delivery Contract

- One auto worker owns one complete task at a time.
- Every task starts from a fresh `origin/dev` and opens a PR to `dev`.
- Owner and reviewer must differ. Owners cannot self-finalize.
- Contract-first tasks must complete before dependent behavior is claimable.
- Documentation, fixtures, mocks, static token checks, and simulations cannot
  close a runtime task.
- Production behavior fails closed. External infrastructure blockers are
  recorded only after all locally implementable work is complete.
- Evidence names exact commands, results, source SHA, environment, limits, and
  independent reviewer findings.
- Existing implementation is composed and migrated, not silently replaced.

## 4. Dispatch Waves

### Wave A - Contract Foundation

These six tasks may run immediately and in parallel.

| Task | Owner | Reviewer | Complete responsibility |
|---|---|---|---|
| `ODP-INTAKE-SCHEMA-001` | Claude | Codex2 | PostgreSQL migration, constraints, RLS, schema tests |
| `ODP-INTAKE-STATES-001` | Claude2 | Codex | Intake/listing/decision/assignment/SLA state engines |
| `ODP-INTAKE-IDENTITY-001` | Codex2 | Claude2 | Immutable reversible identity graph |
| `ODP-INTAKE-API-001` | Codex | Claude | Effective OpenAPI, handlers, generated client, contract tests |
| `ODP-INTAKE-AUTH-001` | Antigravity | Claude2 | Deny-by-default RBAC/ABAC, masking, segregation |
| `ODP-INTAKE-EVENTS-001` | Antigravity2 | Claude | Event envelope, outbox, dedup, replay, DLQ |

### Wave B - Product And Runtime

These tasks are pre-assigned and become claimable only when their declared
Wave A dependencies are `done`.

| Task | Owner | Reviewer | Complete responsibility |
|---|---|---|---|
| `ODP-INTAKE-SNAPSHOT-001` | Antigravity3 | Claude2 | Snapshot object storage, provenance, residency, reconciliation |
| `ODP-INTAKE-JOBS-001` | Antigravity4 | Claude2 | Durable jobs, fencing, timeout, backpressure, cancellation |
| `ODP-INTAKE-PRIVACY-001` | Antigravity5 | Claude | Purge, legal hold, export manifests, WORM evidence |
| `ODP-INTAKE-PROMOTION-001` | Antigravity6 | Claude2 | Reviewed listing-to-candidate promotion saga |
| `ODP-INTAKE-UX-001` | Antigravity7 | Claude | Full assisted-intake user workflow and accessibility |

### Wave C - Migration And Runtime Proof

| Task | Owner | Reviewer | Dependencies |
|---|---|---|---|
| `ODP-INTAKE-MIGRATION-001` | Antigravity3 | Claude | Schema, identity, snapshot |
| `ODP-INTAKE-LOAD-001` | Antigravity4 | Claude2 | API, events, jobs, promotion |

### Wave D - Governed Release

| Task | Owner | Reviewer | Complete responsibility |
|---|---|---|---|
| `ODP-INTAKE-RELEASE-001` | Antigravity6 | Claude | Canary, UAT, restore, rollback, cutover gates after all implementation tasks |

## 5. Task Acceptance Summary

### ODP-INTAKE-SCHEMA-001

Ship an ordered production migration for the complete four-file DDL stack.
Prove PostgreSQL 16 install/upgrade, tenant-qualified FKs, constraints,
indexes, retention/legal hold, FORCE RLS, fail-closed policies, and rollback.

### ODP-INTAKE-STATES-001

Implement exhaustive intake, listing, decision, assignment/SLA, and promotion
orchestration transitions. Every transition enforces its approved actor,
permission, precondition, idempotency, concurrency, evidence, event, retry,
terminal, and reopen contract.

### ODP-INTAKE-IDENTITY-001

Implement immutable effective/superseded identity edges, redirects, merge,
split, unmerge, reversal, cycle prevention, concurrency, rollback, and lineage
queries without mutating source evidence.

### ODP-INTAKE-API-001

Generate the effective OpenAPI 1.1.3 contract and client, then implement every
query and command operation with cursor, masking, idempotency, If-Match,
partial-success, conflict, authorization, replay, and error-schema tests.

### ODP-INTAKE-AUTH-001

Enforce the deny-by-default role/action/resource/scope/state/field/risk matrix,
including tenant and area scope, ownership, segregation of duties,
self-review prohibition, emergency limits, masking, and exact reason codes.

### ODP-INTAKE-EVENTS-001

Implement the approved envelope, typed catalog, transactional outbox,
partition ordering, consumer deduplication, retry/DLQ, retention, replay, and
compatibility. External webhooks remain unsupported in v1.

### ODP-INTAKE-SNAPSHOT-001

Implement policy-gated immutable snapshots with generation/checksum,
provenance, TW_ONLY residency, retention, legal hold, access restriction, and
SQL/GCS reconciliation.

### ODP-INTAKE-JOBS-001

Implement durable Cloud Tasks semantics with job identity, lease/fence,
heartbeat, timeout, retry, acknowledgement, backpressure, poison isolation,
DLQ, replay, cancellation, and stale-worker protection.

### ODP-INTAKE-PRIVACY-001

Implement purge and deletion conflict behavior, legal hold placement/release,
residency enforcement, watermarked evidence exports, manifests, verification,
WORM integrity, and authorization/audit evidence.

### ODP-INTAKE-PROMOTION-001

Remove automatic candidate creation. Implement explicit request, independent
review, idempotent candidate creation, SiteScore execution, compensation,
retry, reversal, and authoritative success evidence.

### ODP-INTAKE-UX-001

Implement URL intake, durable status detail, parsed correction,
duplicate/revision comparison, assisted entry, Listing Inbox integration, all
required states, responsive behavior, keyboard operation, and accessible
change summaries from approved contracts only.

### ODP-INTAKE-MIGRATION-001

Implement mapping, backfill, resume, dry run, reconciliation, lineage
preservation, irreconcilable-record ownership, count/checksum proof, rollback,
and forward recovery against staging.

### ODP-INTAKE-LOAD-001

Measure approved volume, latency, queue age, parser, review SLA, availability,
retry, failover, backlog, DLQ, RPO/RTO, and error-budget targets using
production-like runtime evidence.

### ODP-INTAKE-RELEASE-001

Execute shadow, tenant/source canary, UAT, migration reconciliation, restore,
kill switch, rollback, and cutover. All production flags remain disabled until
the section 12 owner approvals and runtime evidence are recorded.

## 6. Completion Rule

A task is complete only after implementation, focused tests, evidence, PR,
independent review, merge to `dev`, and canonical `review_approved -> done`
closeout. A queued, claimed, coded, or locally passing task is not complete.
