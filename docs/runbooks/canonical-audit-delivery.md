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

This implementation does not close the whole §6 contract. The 5-minute pending
backlog breaker, priority scheduling and Security/Platform paging still need
runtime integration and evidence. Synchronously required WORM actions such as
purge, legal-hold release and restricted export must retain their own policy
and execution gates; this asynchronous delivery path cannot substitute for them.

Live staging/recovery, approved grants and bucket policy, independent
exact-source/task review, authorized real-account UAT and human release
authority remain required. The existing nine-owner register and feature flags
are unchanged.

The OpsBoard module requires platform audit events. Its Store Ops physical
schema/retention binding is a separate domain review; an Intake retention
policy is not silently applied to Store Ops records.
