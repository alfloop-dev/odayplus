# Shared logout / pre-filter audit repair — source only

Follow-up to independent STATIC CHANGES_REQUIRED on auxiliary fbb514c84.
Original a1294dbe/PR1459 rejection, auxiliary7c/fbb history and receipts remain.
Owner Antigravity2/reviewer Codex, six-reopen Human/Ops hold and unconditional
operational preflight HOLD are NOT changed by foreground source work.

## Executed reproductions, not just reviewer reasoning

Against unchanged fbb production source, new8 actual-canonical-evaluator + dual
owner cases reproduced logout twice for one original/target cookie echoed by
JSON/form diagnostic responses, including a lost first logout reply. New15
actual PostgreSQL/authenticated-route cases reproduced HTTP200 partial history
instead of refusal after valid ACK when any of the three fixed recovery types
had missing/wrong tenant, JSONB null/list metadata, or a digest-invalid wrong
metadata tenant. All23 failed on the intended assertions, not setup errors.
These are synthetic/disposable source tests, NOT live recovery or independent
reviewer executions. Diagnostic logs are retained outside the source checkout.

## Repair

- Gate canonical logouts, gate cleanup and original/target owner cleanup claim
  ONE cookie-keyed intent registry before transport. Atomic setdefault permits
  only one claimant; no failed reply or receipt releases the intent. Private
  digest-only keys outlive owner.close while a gate references the owner, without
  retaining an extra plaintext cookie or claiming zeroization. Once-only cleanup
  and shared UNKNOWN remain, with existing durable-start/time/I/O boundaries.
- Verified incident-tenant audit reads first query all THREE fixed correlations,
  independently of metadata tenant SQL selection. Existing audit reader verifies
  row integrity; exact metadata keys, event type/correlation, pinned tenant and
  redaction flag are checked before tenant/subject projection. Wrong/null/unreadable
  metadata and integrity errors become only RECOVERY_AUDIT_LINEAGE_INVALID, never
  a surviving ACK. Same IDs from correlation and tenant queries are projected
  once. Other tenants do not receive global correlation reads; spoofed fixed rows
  cannot expose recovery metadata to another tenant either. Unrelated signed
  other-tenant rows remain isolated and do not poison normal reads.

Two further positive/canonical logout tests and one actual PG tenant-isolation
control accompany the23 reproduced cases. Existing projection/evaluator mock now
honors the actual correlation/tenant selector instead of returning every row for
any query; no production predicate or assertion is waived. JSONB null cannot be
parsed by the existing generic audit reader: the new recovery boundary explicitly
normalizes its TypeError to static403. Tests preserve that generic-parser coverage
limitation rather than falsely declaring a successful full-chain read.

Focused repaired gate/owner and PG selections passed. Initial repaired null-parser
and inaccurate mock-control failures were retained and addressed, not erased.
Immutable-head full verification and new independent review are required AFTER
this source/documentation is committed; no previous receipt is transferred.

## Acceptance limits

The extra indexed correlation reads and digest registry are software fences, not
writer exclusion or execution approval. Canonical continuation/integration and
scope declaration still require authorized adjudication; then canonical receipts,
hosted CI and independent whole-composition review on the resulting exact head.
Native custody/configuration, measured production reserves, current source/admission
and reviewed execution plan remain separate unapproved operational prerequisites.
No task resubmission/approval, churn reset, owner reenrollment, merge, competing PR,
live account/secret/session/cloud/deployment/IAM/business/model effect or F11/full
acceptance claim. Original quarantine/bindingNULL/incident cleanupUNKNOWN remain.
