---
doc_id: ODP-SD-INTAKE-REVIEW-002
title: ODay Plus Assisted Listing Intake System Design Response Review
version: 0.2.1
status: approved-with-conditions
owner: Architecture Review
reviewers: Product Platform Engineering / Security / Privacy / Data / Platform-SRE / Expansion Engineering / QA
reviews: ODP-SD-INTAKE-001
responds_to: ODP-SD-INTAKE-ALIGN-001
response_version: 0.2.1
reviewed_commit: a5a9a2be88e20ffff8719eaaba3c7eba263abc31
base_branch: dev
base_commit: e2ef2156375c733747d968346fd85ca54cc751c1
artifact_manifest: docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml
supersedes_review: ODP-SD-INTAKE-REVIEW-001
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake System Design Response Review

## 1. Review Decision

The review decision for `ODP-SD-INTAKE-001` version `0.2.1`, commit
`a5a9a2be88e20ffff8719eaaba3c7eba263abc31`, is:

**`APPROVED_WITH_CONDITIONS`**

The System Design package is sufficiently complete and internally consistent to
serve as the implementation-binding architecture baseline for:

- engineering task decomposition;
- schema, API, authorization, event, and state contract tests;
- feature-flagged implementation;
- migration dry runs and staging validation;
- Product/UX implementation against the published system facts.

This decision does **not** declare the runtime production-ready. Production
cutover remains blocked by the conditions and release gates in section 7.

The previous `ODP-SD-INTAKE-REVIEW-001`, which reviewed commit
`ffe14c77f7d4f1ae97d301db3a8177cd3effeed6`, is historical evidence only and is
superseded for the current response.

## 2. Review Target and Evidence

| Item | Reviewed value |
|---|---|
| Response | `ODP-SD-INTAKE-001` |
| Response version | `0.2.1` |
| Exact reviewed commit | `a5a9a2be88e20ffff8719eaaba3c7eba263abc31` |
| Base branch | `dev` |
| Base commit | `e2ef2156375c733747d968346fd85ca54cc751c1` |
| Review manifest | `ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml` |
| Design contract CI | Passed |
| Repository CI | Passed |

The response branch is ahead of `dev` without divergence. The committed
pre-review validator covers artifact presence, decision coverage, transition
contracts, command API coverage, promotion semantics, canonical error codes,
event and payload schema coverage, tenant isolation, history, migration,
promotion-state constraints, lineage-safe uniqueness, exact review target, and
artifact precedence.

A review result is valid only for the exact SHA above. Any change to a normative
artifact invalidates this decision and requires a new exact-head review.

## 3. Required Deliverable Assessment

| Required deliverable | Result | Binding artifact |
|---|---|---|
| Canonical ERD and aggregate ownership | PASS | `ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md` |
| Intake, listing, identity, assignment/SLA, decision, promotion state contracts | PASS | `ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md`; v0.2.1 correction pack |
| Source/parser/snapshot/correction/decision persistence schemas | PASS | base SQL schema + patches `0002` and `0003` |
| Versioned query and command API contract | PASS | OpenAPI v1 + v1.1 + v1.1.1 overlays |
| Event catalog, envelope, payloads, outbox, replay and DLQ | PASS | event v1 + v1.1 addendum + payload registry |
| Role/action/resource/field/scope/state/risk authorization matrix | PASS | `ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md` |
| Production storage, jobs, evidence and observability topology | PASS | main response + reliability/privacy contract |
| Capacity, SLO, retry, RPO and RTO | PASS-CONDITIONAL | reliability/privacy contract; owner ratification still required |
| Migration, reconciliation, canary, cutover and rollback | PASS | migration/rollout runbook |
| UX-binding system facts | PASS | main response, state contracts, OpenAPI errors and authorization reason codes |

## 4. Resolution of Previous Blocking Findings

### SDR-001 — State models

**RESOLVED.**

The package now provides binding transition contracts for:

- intake processing;
- listing lifecycle;
- identity resolution and reversal;
- assignment and SLA;
- decision review, execution and reversal;
- candidate promotion and SiteScore compensation.

Each high-impact transition binds actor, backend permission, preconditions,
idempotency, concurrency/version token, evidence, audit/domain event, failure
result, and terminal/reopen behavior. `QUARANTINED` is explicitly reopenable and
is not represented as an unconditional terminal state.

### SDR-002 — Reversible identity resolution

**RESOLVED.**

The effective identity graph now uses immutable edges plus effective/superseded
selection, property redirects, cycle prevention, merge/split/unmerge review,
compensating reversal, dependency checking, and lineage-preserving downstream
reference resolution. Source records and snapshots remain immutable.

### SDR-003 — Persistence schemas

**RESOLVED.**

The committed PostgreSQL/PostGIS schema and patches define types, nullability,
foreign keys, tenant-qualified constraints, versions, indexes, history tables,
pause intervals, reconciliation findings, retention/legal hold, RLS, and
authoritative timestamps.

The consistency patches also preserve URL/revision history and per-intake
snapshot evidence, validate `LEGACY_RECONCILED`, and admit the reviewed
promotion `PENDING_REVIEW` lifecycle.

### SDR-004 — Versioned API contract

**RESOLVED.**

The OpenAPI bundle defines intake query/detail, URL and batch intake,
corrections, identity decisions, merge/split/unmerge, assignment claim/transfer/
complete, SLA pause/resume, job retry, promotion request/review/detail, saved
views, cursor behavior, masking, validation, conflict, authorization,
idempotency, `If-Match`, and replay semantics.

The obsolete direct final promotion receipt route is explicitly removed.
Candidate and SiteScore job identifiers are not exposed before the approved
execution transaction commits.

### SDR-005 — Authorization and segregation

**RESOLVED.**

The authorization contract is deny-by-default and binds role, service identity,
resource, action, tenant, brand, region, assigned area, HeatZone, workflow
state, field classification, ownership relation, and decision risk.

It defines proposer/reviewer separation, self-review denial, second-actor
requirements, emergency administration limits, restricted export/purge/legal
hold permissions, and canonical backend denial/masking codes.

### SDR-006 — Event and external delivery contracts

**RESOLVED.**

The event package defines a versioned envelope, producer and transaction owner,
aggregate/partition key, ordering scope, payload schema, sensitive-field policy,
consumers, deduplication, retry/DLQ, retention, replay authority, compatibility,
and deprecation.

Every declared `schema_ref` resolves to a complete typed payload schema. External
webhooks are explicitly unsupported in v1 and require a separate future
alignment/ADR.

### SDR-007 — Privacy lifecycle and evidence export

**RESOLVED.**

The reliability/privacy contract defines minimization, masking, purpose binding,
retention, purge, legal hold placement/release, residency, export scope,
watermark, manifest, checksum verification, approving roles, audit evidence, and
fail-closed conflict behavior.

### SDR-008 — Production reliability

**RESOLVED FOR DESIGN; OWNER RATIFICATION REMAINS.**

The package selects Cloud SQL PostgreSQL/PostGIS regional HA, Cloud Storage for
snapshots/WORM evidence, Cloud Tasks for jobs, Pub/Sub transactional outbox
events, Cloud Run workloads, and Secret Manager credentials. It defines backup/
PITR, KMS ownership, SQL/GCS consistency and reconciliation, job lease/fencing,
heartbeat, timeout, backpressure, cancellation, replay, DLQ, alert ownership,
SLOs, RPO/RTO, restore ordering, and drills.

Quantitative service commitments remain proposed until their named Product,
Security/Privacy, Platform-SRE and Legal owners ratify them.

## 5. Binding Architecture Decisions Confirmed

The following decisions are implementation-binding:

1. Unknown, expired, unauthorized, unlicensed, kill-switched or prohibited
   sources fail closed.
2. Intake begins only from URL, manual entry, CSV, approved feed or approved
   operator snapshot; continuous result-page crawling and provider-ID
   enumeration are outside the product boundary.
3. Provider credentials, cookies, bearer tokens and private API endpoints are
   never collected through product APIs or UI.
4. Property identity excludes rent; rent-only change creates a listing revision.
5. `POSSIBLE_MATCH` is never automatically merged.
6. Candidate promotion is `request -> independent review -> execution`.
7. Candidate creation and SiteScore job creation occur only after approved
   execution commits; automatic candidate creation in the legacy pipeline must
   be removed.
8. High-impact mutations are non-optimistic, idempotent, version-checked,
   attributable and audited.
9. Tenant, region, area, role, field sensitivity, workflow state and risk are
   backend-enforced.
10. Memory and SQLite remain local/test/Product-E2E adapters and are not the
    production topology.

## 6. Conditions of Approval

The design is approved with the following conditions:

1. **Artifact order is normative.** Client generation and contract tests must
   apply OpenAPI in manifest order; database migration must apply base schema,
   patch `0002`, then patch `0003`.
2. **Exact-SHA review remains mandatory.** Any change to the response, state,
   schema, API, authorization, event, reliability or migration artifacts voids
   this review until revalidated and re-reviewed.
3. **Named owner approvals remain required.** Product, Security/Privacy, Data,
   Platform-SRE, Expansion Engineering, QA, Legal where applicable, and Release
   Authority must record the approvals assigned to them before production
   release.
4. **Runtime evidence is separate.** Contract/schema/auth/event tests,
   migration dry runs, restore drills, canary results, monitoring and operational
   evidence must land in implementation PRs; this document package alone does
   not establish production readiness.
5. **No provider-specific retrieval without approval.** Commercial/legal source
   approval, registry expiry, kill switch, rate limit and source-policy tests
   remain release gates.
6. **No hidden fallback.** Legacy automatic candidate creation, indefinite dual
   write, mock-provider fallback and local durable adapters must not remain on
   the production path after cutover.

## 7. Handoff and Release Gates

### Allowed after this review

- generate engineering execution tasks;
- land contract and schema tests;
- generate typed clients from the bundled OpenAPI;
- implement feature-flagged API, workers and UI;
- run backfill, reconciliation and migration dry runs;
- perform staging and tenant/source canaries;
- update Product/UX against the binding state, permission, freshness and error
  facts.

### Still blocked

- production storage cutover;
- production provider retrieval without source approval;
- automatic or unreviewed identity merge or candidate promotion;
- restricted export, purge or legal-hold release without required approvals;
- external webhooks in v1;
- cross-region residency mode without Security/Privacy/Legal approval;
- production-readiness claims based only on documentation, fixtures or CI.

## 8. Final Disposition

`ODP-SD-INTAKE-001` version `0.2.1` at commit
`a5a9a2be88e20ffff8719eaaba3c7eba263abc31` is accepted as the stable,
implementation-binding System Design baseline, subject to section 6 conditions.

There are **no remaining System Design changes requested** against this exact
commit. Subsequent findings belong to implementation validation unless they
change a normative architecture contract.
