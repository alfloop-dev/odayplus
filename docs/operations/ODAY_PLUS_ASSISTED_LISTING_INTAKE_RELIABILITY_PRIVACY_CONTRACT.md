---
doc_id: ODP-SD-INTAKE-OPS-001
title: ODay Plus Assisted Listing Intake Reliability, Privacy, and Evidence Contract
version: 1.0.0
status: proposed
owner: Platform/SRE / Security/Privacy / System Design
reviewers: Product / Security / Privacy / Data / Platform / Legal / QA
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake Reliability, Privacy, and Evidence Contract

Normative for `SDI-017` through `SDI-022`. Quantitative SLO/RPO/RTO values remain proposed until their named owners record approval. Until approval, production rollout is fail-closed.

## 1. Production topology

```text
User/API
  -> Cloud Run API (regional, min 2 instances, concurrency capped)
  -> Cloud SQL PostgreSQL 16 HA + PostGIS (regional primary/standby)
  -> Cloud Tasks (command/job delivery)
  -> Cloud Run workers (retrieval, parser, matcher, promotion, reconciliation)
  -> GCS regional buckets (raw/redacted snapshots and WORM evidence)
  -> transactional outbox in Cloud SQL
  -> Pub/Sub internal event topics
  -> Cloud Run event publisher/consumers
  -> OpenTelemetry logs/metrics/traces + alerting
```

### 1.1 Residency modes

| Mode | Default | SQL | GCS | Cross-region behavior |
|---|---:|---|---|---|
| `TW_ONLY` | Yes | HA primary/standby in approved Taiwan region | Regional Taiwan buckets | No cross-region data replication; encrypted backups remain in approved Taiwan region |
| `APPROVED_APAC_DR` | No | Taiwan primary plus approved APAC cross-region replica | Approved APAC DR bucket | Enabled only after Legal/Privacy/customer contract approval and residency update |

A tenant is assigned one residency mode. Moving modes is a C4 change with export inventory, replication evidence, and customer/legal approval. Requests to a destination outside the mode return `403 RESIDENCY_DENIED`.

### 1.2 Cloud SQL contract

- PostgreSQL 16, regional HA, automatic failover, private IP, IAM DB authentication, connection pooling.
- Primary write region is authoritative. Read replicas must never serve mutation preconditions or immediately consistent detail reads.
- PITR WAL retention: **35 days proposed**, daily backups: **35 days proposed**, monthly encrypted backup: **12 months proposed**, subject to Privacy/Legal approval.
- Backup and replica encryption use CMEK. Key owner: Security. Rotation every **90 days proposed**; emergency rotation follows the security incident runbook.
- Restore authority: Platform SRE incident commander plus Data owner approval; production restore requires a change/incident ID and an isolated validation environment before cutover.
- SQLite and in-memory adapters remain local/unit/fixture/Product-E2E only and are blocked by production configuration validation.

### 1.3 SQL/GCS consistency boundary

1. Retrieval writes a GCS object with generation precondition `0` and computes SHA-256.
2. Snapshot metadata is committed in Cloud SQL only after the object write succeeds.
3. Parser jobs reference the committed `source_snapshot_id`, never an arbitrary URI.
4. If GCS succeeds and SQL fails, an orphan marker is emitted to reconciliation; the object is inaccessible to product reads until metadata is committed.
5. If SQL metadata exists but GCS HEAD/checksum verification fails, the snapshot is quarantined, parser execution is blocked, and `snapshot.integrity_failed` is audited.
6. A scheduled reconciler verifies SQL metadata, object generation, size, checksum, residency, and legal-hold flags.

Reconciliation SLO: discrepancies detected within 15 minutes; P0 snapshot integrity alerts page Platform SRE and Data owner.

## 2. Asynchronous job contract

### 2.1 Queue and durable state

Cloud Tasks is delivery; `workflow.jobs` in Cloud SQL is authoritative state. Each task carries only `job_id`, `tenant_id`, and a signed attempt token. Worker payloads are loaded from SQL after authorization.

| Stage | Soft timeout | Hard timeout | Max attempts | Backoff | Checkpoint |
|---|---:|---:|---:|---|---|
| Identity check | 10 s | 30 s | 3 | 5/15/60 s | `CHECKING_IDENTITY` |
| Policy evaluation | 5 s | 15 s | 3 | 5/30/120 s | `CHECKING_SOURCE_POLICY` |
| Retrieval | 30 s | 120 s | 5 | 10/30/120/600/1800 s | `RETRIEVING` |
| Parsing | 60 s | 300 s | 4 | 30/120/600/1800 s | `PARSING` |
| Matching | 30 s | 120 s | 3 | 10/60/300 s | `MATCHING` |
| Candidate creation | 10 s | 30 s | 3 | 5/30/120 s | `CANDIDATE_CREATING` |
| SiteScore enqueue | 10 s | 30 s | 5 | 10/30/120/600/1800 s | `SCORE_QUEUED` |
| Outbox publish | 10 s | 60 s | 10 | 10/30/120/600/1800 s with jitter | outbox row |

### 2.2 Lease, fencing, and acknowledgement

- Worker claims by atomically changing SQL job state and incrementing `fence_token`.
- Every checkpoint/status write includes `job_id`, expected `version`, and current fence token. A stale worker returns `409 JOB_FENCE_REJECTED` and must stop.
- Heartbeat interval is 15 seconds for stages longer than 30 seconds; lease expires after 45 seconds without heartbeat.
- Cloud Task is acknowledged only after SQL status/checkpoint and transactional outbox changes commit.
- Cancellation is cooperative at defined checkpoints. A running external HTTP request is allowed to finish, but its result is discarded if cancellation version/fence changed.

### 2.3 Concurrency, rate, and backpressure

| Control | Proposed value | Owner |
|---|---:|---|
| Tenant URL submissions | 20/minute burst 40 | Product/Ops |
| CSV rows per batch | 1,000 | Product/Data |
| Global retrieval concurrency | 100 | Platform/SRE |
| Per-source retrieval concurrency | Source registry; default 5 | Source owner/Legal |
| Parser concurrency | 200 | Platform/SRE |
| Matching concurrency | 100 | Data/Platform |
| Promotion concurrency | 50 | Expansion/Platform |
| Outbox publisher batch | 200 | Platform/SRE |
| Queue-age warning | >2 minutes for 5 minutes | Platform/SRE |
| Queue-age critical | >10 minutes or oldest >20 minutes | Platform/SRE |
| DB pool warning | >80% for 5 minutes | Platform/SRE |
| DLQ warning | 1 message | Domain owner |
| DLQ critical | >10 messages or any restricted-data event | Platform/SRE + Security |

Backpressure behavior is fail-safe: submission APIs continue to persist validated intake records but may return `202` with delayed-processing status; once durable intake write capacity is threatened, APIs return `503 BACKPRESSURE_ACTIVE` with `Retry-After`. They must not accept data without a durable receipt.

### 2.4 Replay and dead-letter

- Replay authority: Expansion manager for own tenant retryable business jobs; Data steward for parser/matcher jobs; Platform SRE for platform incidents. Retry-budget override requires manager/steward plus risk acknowledgement.
- Replay requires original checkpoint, current source policy, current parser release compatibility, aggregate versions, reason, and change/incident ID if overriding budget.
- DLQ retains original signed task metadata and SQL job/error references for 30 days proposed. Raw source content is not copied into DLQ.
- Poison records are quarantined individually; one record never blocks a batch.
- Alert owner is the job domain owner with Platform SRE escalation.

## 3. Capacity and service levels

### 3.1 Proposed capacity envelope

| Metric | Proposed design capacity | Measurement source | Approver |
|---|---:|---|---|
| Tenant count | 500 active tenants | tenant registry | Product/Platform |
| Daily intake rows | 100,000/day | intake counters | Product/Data |
| Peak submissions | 50 requests/s, 1,000 rows/request | API metrics | Platform/SRE |
| Concurrent human reviewers | 500 | auth/session metrics | Product/Platform |
| Snapshot payload | 5 MB typical, 25 MB hard maximum | GCS metadata | Data/Security |
| Listing Inbox rows | 100 million retained rows with cursor pagination | Cloud SQL sizing/load test | Data/Platform |
| Audit events | 10 million/month | audit metrics | Security/Platform |

### 3.2 Proposed SLOs

| SLI | Target | Error budget / window | Owner |
|---|---:|---|---|
| API availability | 99.95% monthly | 21.6 min/month | Platform/SRE |
| URL submission durable receipt | p95 <500 ms, p99 <1.5 s | 1% over target/30d | API owner |
| Listing Inbox first page | p95 <1 s, p99 <2.5 s | 1%/30d | API/Data |
| Human mutation response | p95 <1.5 s excluding async completion | 1%/30d | API owner |
| Queue age | p95 <2 min, p99 <10 min | 1%/30d | Platform/SRE |
| Approved-source parse completion | p95 <5 min, p99 <15 min | 2%/30d | External Data |
| Review routing | p95 <1 min after `NEEDS_REVIEW` | 1%/30d | Workflow |
| Review completion | 90% within 1 business day; 99% within 3 | operational KPI | Expansion Ops |
| Outbox publication | p99 <60 s | 0.1%/30d | Platform/SRE |
| Audit WORM receipt | 99.99% successful; high-risk mutation fails closed if absent | zero silent loss | Security/Platform |

SLO values are not contractual commitments until Product/Ops/SRE/Security/Privacy record approval in the response document.

## 4. RPO/RTO and restore order

| Data class | Proposed RPO | Proposed RTO | Restore/replay source |
|---|---:|---:|---|
| Intake/listing/revision/identity/decisions | 15 min | 4 h | Cloud SQL PITR + outbox reconciliation |
| Assignments/SLA/jobs/idempotency | 15 min | 4 h | Cloud SQL PITR + Cloud Tasks/job reconciliation |
| Candidate promotion | 15 min | 4 h | SQL decision/candidate transaction + SiteScore replay |
| Source snapshots | 1 h | 8 h | Versioned GCS object inventory/checksum manifest |
| Audit SQL index | 15 min | 4 h | SQL PITR + WORM re-index |
| WORM evidence | 0 for accepted receipt | 24 h query restoration | GCS object inventory; immutable copies |
| Pub/Sub projections | 1 h | 8 h | retained outbox/audit replay |

Restore order:

1. IAM/KMS/Secret Manager and approved residency configuration.
2. Cloud SQL isolated PITR restore; validate schema/checksums/tenant counts.
3. Audit chain and WORM object verification.
4. Snapshot metadata-to-GCS reconciliation.
5. Identity effective-edge/redirect integrity and cycle check.
6. Listing/current revision pointers and candidate uniqueness check.
7. Job/idempotency/outbox reconciliation; recreate only missing Cloud Tasks.
8. Rebuild projections/search from outbox/audit.
9. Read-only product validation, then controlled write enablement.

Required drills: quarterly SQL restore, semiannual regional failover, quarterly WORM verification, and per-release job/outbox replay test. Evidence includes timestamps, counts, checksums, unresolved differences, owners, and approval.

## 5. Sensitive data lifecycle

### 5.1 Data minimization and systems of record

| Data | System of record | Collection rule | Default retention proposed |
|---|---|---|---:|
| Public listing URL/provider ID | Cloud SQL + snapshot metadata | Required for lineage | 5 years after archive |
| Raw snapshot | GCS restricted bucket | Only approved retrieval/operator evidence | 2 years after last decision, then redact/purge unless hold |
| Redacted snapshot | GCS evidence bucket | Derived by approved redaction pipeline | 5 years after archive |
| Exact address/coordinates | Cloud SQL confidential | Expansion decision purpose only | Listing lifetime + 5 years |
| Rent/area/commercial terms | Cloud SQL internal/confidential | Expansion decision purpose | Listing lifetime + 5 years |
| Broker/owner personal contact | Cloud SQL restricted, separate table/vault | Optional, explicit purpose, minimum fields | 180 days after case closure unless consent/contract requires longer |
| Private notes | Cloud SQL restricted | Prohibited from containing credentials; purpose bound | 1 year after case closure |
| Corrections/decisions/audit | Cloud SQL + WORM | Required for accountability | 7 years proposed or legal requirement |
| Provider credentials | Secret Manager only | Never through product API/UI | Until rotation/revocation policy |

### 5.2 Deletion and purge

1. A purge request identifies tenant, subject type/ID, legal basis, scope, and dry-run count.
2. Backend checks retention, active legal holds, dependent decisions/candidates, source-license obligations, residency, and immutable-evidence requirements.
3. High-risk purge requires requester plus independent Privacy/Governance approval.
4. Purge executes in dependency order and records per-object result. Immutable audit/WORM evidence is not erased; payload is minimized/tokenized where legally permissible and the purge decision is appended.
5. Conflicts return `409 LEGAL_HOLD_CONFLICT`, `409 RETENTION_NOT_REACHED`, or `409 DEPENDENCY_CONFLICT`; nothing is partially hidden as success.
6. GCS deletion uses generation match and the manifest records generation/checksum. SQL marks `deleted_at` only after object deletion receipt or an approved tombstone strategy.

### 5.3 Legal hold

- Placement requires requester and independent Privacy/Governance approver; system of record is `audit.legal_holds`.
- Hold propagates by subject graph to intake, listings/revisions, snapshots, decisions, corrections, exports, audit, and candidates identified in the approved hold scope.
- Release requires a different authorized actor and a release reason; release never triggers automatic purge.
- Events: `legal_hold.placed`, `legal_hold.released`; actions are WORM-audited.

### 5.4 Evidence export

Every export has an `audit.export_manifests` record containing requester, approver, purpose, tenant/scope, query/filter, field mask, object IDs, snapshot/audit IDs, row/file counts, checksum, watermark, expiry, destination residency, and WORM receipt.

- Internal-class export: manager or steward request; independent approval when bulk or cross-area.
- Confidential/restricted export: independent Governance/Privacy approval, short-lived signed download, visible watermark with export ID/requester/time, no public link.
- Credentials and unredacted provider secrets are never exportable.
- Verification recomputes file checksum and validates every referenced snapshot/audit receipt. A mismatch blocks download and raises a Security incident.

## 6. Audit failure behavior

- High-impact actions do not commit unless the SQL audit event and outbox row commit in the same transaction.
- When WORM is synchronously required by policy (purge, legal hold release, restricted export), failure to obtain the WORM receipt aborts the operation.
- For other actions, SQL audit commits with `worm_pending`; a priority publisher retries. If pending exceeds 5 minutes, high-risk mutations are disabled and Security/Platform are paged.
- Audit chain uses tenant sequence, previous hash, canonical JSON SHA-256, and GCS object-create semantics. Re-indexing from WORM must reproduce the chain.

## 7. Required approvals and fail-closed gates

| Contract group | Required approvers | Gate before approval |
|---|---|---|
| Capacity/SLO | Product, Expansion Ops, Platform/SRE | Values labeled proposed; no contractual claim |
| RPO/RTO/topology | Platform/SRE, Data, Security | Production storage/async rollout disabled |
| Retention/purge/legal hold/residency | Privacy, Legal, Security, Data | Restricted export/purge APIs disabled |
| KMS/key rotation | Security | Production credentials/snapshots disabled |
| Source retrieval | Source owner, Legal/Commercial | Source remains assisted-entry-only or blocked |
