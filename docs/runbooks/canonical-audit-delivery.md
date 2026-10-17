# Canonical Intake audit delivery

Task trace: `ODP-SYS-ACCEPT-261011`.
Source contract: `ODAY_PLUS_ASSISTED_LISTING_INTAKE_RELIABILITY_PRIVACY_CONTRACT.md` §6.
This describes the candidate implementation and remaining production admission.

## Actual transaction and worker path

The PostgreSQL persistence factory connects `CanonicalIntakeAuditSink` to the
physical `audit.audit_events` writer, the approved retention resolver, the same
durable job queue and the configured WORM sink. The sink requires an active
business transaction before inserting the canonical event. It enqueues one
`canonical-audit-delivery` job with tenant ID, event ID, immutable event hash
and configured sink ID in that transaction. An audit, queue, document, outbox or
replay failure rolls the business operation back.

The default `ODayWorker` registry consumes the committed job. It rechecks the
actual persisted job and canonical row, tenant, event hash, idempotency binding
and configured sink. The WORM payload uses the exact immutable JSON contract
that produced `event_sha256`, including the tenant sequence and preceding hash.
It does not convert a runtime audit chain into the canonical chain or rehash
legacy rows.

After the configured sink writes or verifies the immutable object, the writer
checks the receipt type, event ID, full checksum, sink and exact deterministic
object path. Only `worm_object_uri` is updated. Immutable audit values and
`event_sha256` remain unchanged. An existing different URI cannot be replaced.
Queue success follows the verified receipt update.

Database or external failure leaves the URI pending. On an interruption after
object creation, the existing local immutable-object readback or GCS 412
recovery verifies the object and retries the same receipt update. The worker
does not rerun the business mutation.

## Permission boundary

The producer needs the existing canonical INSERT/SELECT access. The worker also
needs UPDATE on `audit.audit_events.worm_object_uri` only; it does not need
UPDATE on immutable audit fields or DELETE. The isolated PostgreSQL test proves
that the receipt update fails without that column grant and succeeds with it,
while immutable updates and deletion remain denied. No production grant is
applied by this code.

GCS readback authority, residency, retention locking, credential rotation,
worker availability and retry/dead-letter operation follow
`store-ops-audit-delivery.md`. The configured sink is an approved deployment
input; job payloads cannot choose another target.

## Local validation

```bash
# INTAKE_TEST_DATABASE_URL must point to an isolated disposable PostgreSQL 16.
.venv/bin/python -m pytest \
  tests/integration/test_canonical_audit_delivery.py \
  tests/integration/test_intake_canonical_audit_wiring.py \
  tests/integration/test_canonical_intake_audit.py \
  tests/integration/test_store_ops_scoped_postgresql.py -q
```

The delivery cases cover actual HTTP business commands, a fresh database
connection and default worker registry, immutable payload/chain verification,
queue rollback, external outage, receipt-save interruption, wrong tenant/hash/
record/sink/receipt and minimal column permission. The GCS case uses a loopback
JSON API protocol server with synthetic credentials; it does not certify live
Google Cloud permissions or storage policy.

## Remaining production gates

The v1 Intake mutation runner now checks persisted canonical backlog after
authorized idempotency replay and before a new high-risk command executes.
Identity corrections/reviews, match decisions, merge/split/unmerge, promotion
requests/reviews, reversal requests and quarantine release fail closed when the
oldest delivery intent exceeds 300 seconds or its backlog cannot be verified.
Ordinary corrections, reads and already-committed authorized receipts remain
available. The privacy service checks the same dependency before hold release,
actual purge and confidential/restricted export.

The live PostgreSQL Operator Network Listing BFF injects the same tenant-scoped
backlog guard into its application service. It checks new listing conversions,
merges, Intake decisions/promotions and corrections to the existing server-owned
identity fields after the actor/request-bound replay check. Route authorization
and resource scope checks still precede the service call. Authorized cached
receipts, ordinary corrections and reads remain available; changed replay bodies
remain conflicts. A quarantined retry is checked without expanding the existing
retry workflow's allowed states. Backpressure returns 503 with the BFF's existing
`BACKPRESSURE_ACTIVE` detail and `Retry-After: 30`.

This closes backlog admission for these legacy BFF endpoints. It does not
establish canonical legacy-to-UUID reconciliation, canonical audit ownership or
the BFF's complete SQL business/audit/outbox atomicity.

Backlog age comes from the durable delivery job's creation time, not historical
business occurrence time. Missing or mismatched intent is unavailable, never a
healthy zero. Failed/dead-letter jobs remain pending until a verified object URI
is attached. The worker samples all provisioned/queued tenant scopes and emits
bounded pending-count, oldest-age and observation-failure metrics through the
existing release-bound exporter.

Canonical, Store Ops and durable audit jobs have first priority at the queue's next
eligible lease/claim. Receipt rows remain nonexecutable; ordinary jobs retain
their chronological order. This does not interrupt an already-running job or
prove adequate dedicated-publisher capacity.

## Dedicated publisher runtime

The existing worker image entrypoint now has an `audit-publisher` command:

```bash
python product_ops/deployment/cloud_run_job_entrypoint.py audit-publisher \
  --run-for-seconds 60 --max-jobs 100
```

The runtime composition sets `audit_only=True`. Memory, SQLite and the actual
PostgreSQL `FOR UPDATE SKIP LOCKED` claim select only canonical or Store Ops audit
delivery rows before changing a lease, attempt or fence. They do not claim an
ordinary job just to reject it afterward. Expired ordinary jobs remain untouched;
expired audit jobs use the same heartbeat/fencing recovery path as other jobs.

The command waits for late audit arrivals within its bounded observation window,
exports metrics even on idle polls and emits an `audit_publisher` execution
receipt. Claim or metric-export failure is a failed execution; delivery retry or
dead-letter is never a success. `observation_window_complete` records that the
poll window ended, not that every audit was delivered or any team was paged.
The general long-running worker loop also exports on idle iterations now.

An isolated PostgreSQL test holds a general worker in a synthetic long job,
then a second persistence connection and audit-only worker deliver all three
real canonical events through the default registry while that job remains
running. Another case reclaims an expired audit lease, rejects the stale fence,
and leaves both queued and expired ordinary work unchanged. These prove the
runtime selection/isolation path, not production capacity or provider latency.

Before release, Platform/SRE must reserve separate publisher resources, bind the
same admitted worker image/database/WORM configuration, and record schedule
overlap/gaps, pool limits, timeout, batch bounds, backlog deadlines and capacity.
The example bounds are not approved SLO values. A canary must read back the actual
job configuration and execution receipts, exercise busy general workers and
WORM interruption/recovery, and verify metrics freshness and Security/Platform
paging. No resource, schedule, IAM grant or production flag is created/enabled
by this code change. The nine-owner release register remains unchanged.

This implementation does not close the whole §6 contract. Live dedicated
publisher capacity, actual Security/Platform
paging and verified provider receipts still need integration/admission evidence.
The current generic P1 receiver is ops-lead; it is not evidence that both required
teams were paged. Synchronously required WORM actions such as
purge, legal-hold release and restricted export must retain their own policy
and execution gates; this asynchronous delivery path cannot substitute for them.

Live staging/recovery, approved grants and bucket policy, independent
exact-source/task review, authorized real-account UAT and human release
authority remain required. The existing nine-owner register and feature flags
are unchanged.

The OpsBoard module requires platform audit events. Its Store Ops physical
schema/retention binding is a separate domain review; an Intake retention
policy is not silently applied to Store Ops records.
