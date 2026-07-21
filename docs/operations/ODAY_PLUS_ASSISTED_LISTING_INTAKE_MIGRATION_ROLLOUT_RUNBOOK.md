---
doc_id: ODP-SD-INTAKE-MIGRATION-001
title: ODay Plus Assisted Listing Intake Migration, Reconciliation, and Rollout Runbook
version: 1.0.0
status: proposed
owner: Data Platform / Expansion Engineering / Platform SRE
reviewers: System Design / Product / Security / QA / Release Authority
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake Migration, Reconciliation, and Rollout Runbook

Normative for `SDI-023` and `SDI-024`. This runbook migrates the `dev` implementation—where listing ingestion may directly create `CandidateSiteDraft` and uses memory/SQLite adapters—to the explicit Intake, Listing, Identity, Decision, Assignment, Promotion, Cloud SQL/GCS/Cloud Tasks/Pub/Sub contracts.

## 1. Ownership

| Work | Responsible | Approver |
|---|---|---|
| Schema migration and backfill | Data Platform | Data owner + System Design |
| Intake/listing application compatibility | Expansion Engineering | Expansion Product + QA |
| Identity reconciliation | Data Steward lead | Expansion Manager + Data owner |
| Snapshot replay and object checks | External Data | Security + Data owner |
| Cloud SQL/GCS/Tasks/Pub/Sub rollout | Platform SRE | Release authority + Security |
| Authorization/policy rollout | Security Engineering | Security/Privacy |
| UAT and canary acceptance | Product/Expansion Ops | Release authority |
| Irreconcilable record disposition | Data Steward | Governance reviewer |

## 2. Current-to-target mapping

| Current representation | Target representation | Mapping rule |
|---|---|---|
| `ListingPipelineRecord.source_record` | `Intake` + `SourceSnapshot` or assisted-entry evidence | One current row creates one intake; source fields remain immutable evidence |
| `ListingPipelineStatus.RAW` | Intake `NEEDS_REVIEW` or `FAILED` | Validation errors -> `NEEDS_REVIEW`; platform/parser failure -> `FAILED` |
| `PARSED` / `GEOCODED` | Intake `PARSING`/`MATCHING` plus `ParserRun` | Persist parser release/input/output/field confidence |
| `DUPLICATE` | `MatchCase(EXACT_DUPLICATE)` and decision receipt | No automatic merge; exact source-key can be system-approved, ambiguous stays review |
| `FAILED_HARD_RULE` | Listing/intake `NEEDS_REVIEW` or `QUARANTINED` | Hard-rule reason codes preserved; no candidate |
| `CANDIDATE` created automatically | Listing `ACTIVE` + synthetic historical `PromotionDecision` + Candidate only after review | Existing candidates are grandfathered through reconciliation, not silently treated as compliant |
| `ListingDedupKey.property_key` including rent | Property identity excluding rent | Rent-only differences become listing revisions |
| In-memory identity mapping | `Property`, immutable `SourceIdentityEdge`, optional `PropertyRedirect` | Deterministic exact edges backfilled; ambiguous groups become cases |
| In-memory/SQLite audit/jobs | Cloud SQL target rows; WORM/outbox verification | Preserve original IDs where stable; otherwise record legacy IDs in migration metadata |

## 3. Migration artifacts

Production migration must add:

- Alembic migration implementing `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql`;
- mapping manifest `migration_id`, source table/adapter, target table, key mapping, transformation version;
- backfill receipt per partition;
- reconciliation findings table or document store record;
- rollback checkpoint and schema compatibility version;
- source replay manifest including snapshot/checksum/parser release;
- UAT and canary evidence packet.

### 3.1 Reconciliation finding schema

```json
{
  "finding_id": "uuid",
  "migration_id": "string",
  "tenant_id": "uuid",
  "source_kind": "legacy_listing|legacy_candidate|snapshot|identity|audit|job",
  "source_id": "string",
  "target_ids": ["uuid"],
  "finding_type": "COUNT_MISMATCH|CHECKSUM_MISMATCH|AMBIGUOUS_IDENTITY|DUPLICATE_CANDIDATE|MISSING_EVIDENCE|INVALID_SCOPE|ORPHAN_REFERENCE|STATE_MAPPING_CONFLICT",
  "severity": "INFO|WARNING|BLOCKING",
  "expected": {},
  "actual": {},
  "owner_role": "DATA_STEWARD|EXPANSION_MANAGER|PLATFORM_SRE|SECURITY",
  "status": "OPEN|RESOLVED|QUARANTINED|WAIVED",
  "resolution_reason": "string",
  "created_at": "date-time",
  "resolved_at": "date-time|null"
}
```

A `BLOCKING` finding prevents tenant cutover. Waiver requires Data owner and System Design approval; Security/Privacy approval is additionally required for evidence/scope/residency findings.

## 4. Execution phases

### Phase 0 — Contract gate

- Merge approved v0.2.0 design artifacts.
- Contract/schema/auth/event tests land before behavior changes.
- Feature flags default off:
  - `assisted_intake_v1_read`
  - `assisted_intake_v1_write`
  - `assisted_intake_v1_shadow`
  - `assisted_intake_v1_promotion`
  - `assisted_intake_v1_events`
- Production configuration rejects memory/SQLite mode.

Exit: OpenAPI, DDL, transition, authorization, and event compatibility tests green.

### Phase 1 — Schema and read-compatible deploy

1. Apply migration to staging using dry-run checksum manifest.
2. Create target tables/indexes/RLS policies; no production writes.
3. Deploy code capable of reading legacy and target data through a compatibility adapter.
4. Record query parity for Listing Inbox and detail views.

Exit: schema checks, RLS/ABAC tests, backup/PITR proof, no destructive change.

### Phase 2 — Backfill and shadow identity

Partition by tenant then source/month:

1. Create Intake for each legacy source record/import row.
2. Create snapshots or evidence placeholders only where source evidence exists; never fabricate raw snapshots.
3. Create Listing and immutable revision/observation rows.
4. Build exact source edges.
5. Run target matcher in shadow. Do not change live identity/candidate behavior.
6. Create `MatchCase(POSSIBLE_MATCH)` for ambiguous groups.
7. Backfill assignments/SLA only for active review work.
8. Backfill audit references to legacy evidence.

Proof per partition:

- source count = mapped + quarantined + documented excluded;
- target IDs unique per tenant;
- revision counts and fingerprints;
- no identity redirect cycles;
- candidate uniqueness;
- snapshot object count/size/checksum;
- audit count/hash continuity;
- unresolved blocking findings = 0 before canary.

### Phase 3 — Historical candidate reconciliation

Existing automatically created candidates are classified:

| Condition | Disposition |
|---|---|
| Exact listing/property identity, required gate data present, no duplicate candidate | Create `PromotionDecision(status=COMPLETED, decision_type=LEGACY_RECONCILED)` with migration actor and evidence; retain candidate |
| Required fields missing | Candidate `SCREENED`/blocked; create review task and finding |
| Duplicate active candidate for same tenant/property/format | Quarantine all but no automatic deletion; manager decides |
| Ambiguous identity | Candidate remains bound to historical property reference; create match/reassignment review |
| Missing source evidence | Retain candidate if operationally required but mark lineage `PARTIAL`; block claims of fully verified promotion |

No migration script invents a human approver. `LEGACY_RECONCILED` records explicitly identify migration authority and remain distinguishable from post-cutover approvals.

### Phase 4 — Shadow processing canary

- Enable `assisted_intake_v1_shadow` for internal test tenants and approved sources.
- Legacy path remains authoritative; target path runs side by side without external events or candidate creation.
- Compare source-policy outcome, parser values/confidence, identity outcome, listing revision, queue routing, and API projection.

Acceptance metrics over at least 7 days or 10,000 rows, whichever is later:

- 100% tenant/scope isolation tests pass;
- 100% unknown/blocked sources fail closed;
- 0 ambiguous auto-merges;
- 0 automatic candidate promotions;
- exact duplicate agreement >=99.9%; remaining differences reviewed;
- material field parity >=99.5%; all address/rent/area differences explained;
- snapshot checksum reconciliation 100%;
- audit/outbox loss 0;
- p95 latency and queue-age within proposed SLO;
- blocking findings 0.

### Phase 5 — Write canary

Tenant/source rollout units:

1. Internal tenant, assisted-entry-only source.
2. Internal tenant, one approved retrieval source.
3. One low-volume production tenant.
4. 5%, 25%, 50%, 100% eligible tenants.

`assisted_intake_v1_write` makes target Intake/Listing authoritative. Legacy writes are disabled for that tenant/source; do not use indefinite dual-write. A temporary change-data capture/compatibility projection may feed legacy readers, but only target is authoritative.

Promotion remains separately gated by `assisted_intake_v1_promotion` after identity/review UAT.

### Phase 6 — Event and promotion enablement

- Enable transactional outbox and Pub/Sub consumers after replay/DLQ tests.
- Enable promotion for a canary tenant after two-person review, duplicate-candidate constraint, lost-response replay, and SiteScore compensation tests pass.
- Remove automatic `ListingPipeline` candidate creation.

### Phase 7 — Cutover and legacy removal

- Target read/write/events/promotion enabled for all eligible tenants/sources.
- Legacy fixture/source-stub remains CI/test-only and cannot be selected by production config.
- Remove legacy runtime write paths only after 30-day stable watch window and rollback checkpoint expiry.
- Retain migration mappings, reconciliation, and legacy evidence per retention policy.

## 5. Rollback

### 5.1 Trigger

Immediate kill-switch/rollback if any occurs:

- cross-tenant or field-classification breach;
- unknown/prohibited source retrieval;
- ambiguous auto-merge or automatic promotion;
- audit/WORM loss for a high-impact decision;
- duplicate active candidate invariant violation;
- unreconciled SQL/GCS checksum mismatch;
- error budget exhausted or queue age critical for 30 minutes;
- data-loss or restore failure.

### 5.2 Mechanism

- Disable per-tenant/source flags and stop new Cloud Tasks.
- Keep target data read-only; do not delete evidence.
- Drain/park in-flight tasks at checkpoints using cancellation/fence version.
- Disable event publication; retain outbox rows.
- For pre-authoritative shadow/canary, resume legacy authoritative path.
- After target authority, rollback uses the last compatible application version against target schema; it does not reverse committed business decisions automatically.
- Identity/promotion changes are reversed only through their approved reversal state machines.
- If database restore is required, follow the reliability contract restore order and reconcile from WORM/outbox/snapshots.

Rollback evidence: trigger, actor, flag versions, task counts, last committed aggregate versions, outbox range, snapshot manifest, reconciliation results, customer/tenant impact, and release-authority approval.

## 6. Release authority and approvals

| Gate | Approval required |
|---|---|
| Contract artifacts | System Design, Product, Security, Data, Platform, QA |
| Source retrieval | Source owner, Legal/Commercial, Security |
| Staging migration | Data owner, Platform SRE, QA |
| Shadow canary | Product, Expansion Ops, Data, QA |
| Production write canary | Release authority, Platform SRE, Security, Product |
| Candidate promotion | Expansion Product, Security, QA, Release authority |
| 100% cutover | Release authority plus all P0 contract-group approvals |
| Legacy removal | Product, Architecture, Data, Platform, QA |

## 7. Required tests/evidence

- DDL migration up/down or forward-compensation test and checksum manifest;
- tenant RLS + backend ABAC matrix tests;
- source fail-closed tests;
- first submission, exact duplicate, changed-rent revision, ambiguous match, malformed row, timeout/retry, quarantine, correction lineage;
- merge/split/unmerge cycle/concurrency/rollback tests;
- assignment/SLA concurrency and escalation tests;
- promotion idempotency, duplicate prevention, lost-response replay, SiteScore failure/retry tests;
- outbox at-least-once/dedup/replay/DLQ tests;
- SQL/GCS reconciliation and WORM verification;
- restore and rollback drill;
- role-based UAT with screenshots/receipts and no hidden backend assumptions.

## 8. Deployment Configuration

The following environment configurations are required to wire the production GCS object store for snapshot persistence and residency validation:

| Variable | Description | Production Value (Example) |
| --- | --- | --- |
| `ODP_OBJECT_STORE` | Selects backend object store runtime. Must be set to `gcs` in production. Falls back to `in_memory` if unset or credentials are missing. | `gcs` |
| `ODP_RESIDENCY_APPROVED_BUCKETS` | Comma-separated list of approved GCS buckets matching residency rules (e.g. `TW_ONLY` residency). | `taiwan-snapshots,tw-intake-snapshots` |
| `GOOGLE_OAUTH_ACCESS_TOKEN` / `ODP_AUDIT_WORM_GCS_TOKEN` | OAuth token required for WORM GCS API writes. Must be provided by runtime environment/service account. | `<auth-token-material>` |

