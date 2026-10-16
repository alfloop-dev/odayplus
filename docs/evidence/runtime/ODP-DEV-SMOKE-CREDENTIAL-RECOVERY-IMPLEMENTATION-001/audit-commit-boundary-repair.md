# Audit commit/request namespace follow-up (auxiliary, unapproved)

Parent: `edc043938c2166473fa78829bdc3aac5b1bbfb7d`, independently **CHANGES_REQUIRED**.
No canonical acceptance, task ownership/churn/hold change, remote recovery or
production migration. Original PR1459 submission remains unchanged. Human/Ops
adjudication and operational prerequisites remain mandatory.

## Reproduced before production source repair

After retaining one fixture-resolution failure and correcting only fixture
imports, all15 disposable actual-PG cases failed at intended assertions:

- Five real authentication-denial HTTP requests appended `security.authentication`
  into client-selected server-owned provisioning/binding/recovery correlations.
- Five transitions (reserve, capability issue, rotation, intent, ACK), each with
  local append-only and intercepted/offline GCS sinks, left immutable success
  objects after a real deferred PG commit failure rolled back audit and identity
  state. No external network was contacted. The generic PG-only canonical decoder
  is NOT claimed to have accepted those orphan WORM objects.

## Repairs and limits

Request correlation normalization is shared by middleware, credentials parsing,
actual authentication (including directly constructed Credentials), authorization
and retry fallbacks. Ordinary trace IDs remain ordinary; all five server-owned
namespace prefixes are mapped deterministically to a separate `request-<digest>`
trace before authentication audit. Audit append has an additional static refusal
for incompatible event types under reserved prefixes. Existing ledger history is
not rewritten, filtered away or weakened; previously poisoned history still fails
strict validation and requires authorized adjudication.

DurableAuditLog now inserts its event and publication intent in the SAME SQL
transaction. Nested record calls register one after-commit callback; rollback or
commit failure discards it. Actual external publication occurs only after the
outer commit and returning the borrowed PG connection, including pool size1.
Expand-only SQLite/PG outbox migrations and Alembic0025 do not backfill historical
objects or claim old delivery. WORM-enabled constructors refuse missing migration
or unsupported committed-writer implementations; no implicit outbox DDL.

Publication is bounded (default16, explicit maximum64 events). A digest-bound row
claim commits before transport, is conditional across pools, and is never released
or retried after uncertain transport/receipt/ack. UNKNOWN/attempted rows survive
restart and require separately authorized investigation. Pending committed rows
can be explicitly flushed after callback/process loss. No background worker, CLI,
credential retry, native configuration change or operational admission is added.
SQL success is NOT a delivery receipt. Existing sink timeout semantics remain;
this work does not measure production timeout/custody/exclusion reserves.

Only the committed durable publisher uses version2 `transaction_state=committed`
envelopes; checksums bind that marker and payload. Legacy/unmarked WORM objects
are NOT retroactively committed. Bare replay of the five fixed ledger event types
is refused in durable and memory logs; committed-envelope replay validates the
whole batch/integrity before transactional restore. Its caller MUST authenticate
immutable-sink transport: an unkeyed checksum is NOT authentication/permission,
and a caller-provided JSON marker is not execution authority. Trusted maintenance
restore is not an exposed HTTP endpoint or a way to reconstruct a lost GitHub ACK.

Additional isolated controls exercise nested SQLite commit/rollback, replay
refusal/committed restore, pending-process-loss flush, lost publication reply and
restart, real PG pool-size1/witness visibility and competing publishers. An initial
circular-import failure was retained and fixed with a runtime import at the memory
append barrier. No existing assertions/budgets/skips are waived. Fresh exact-head
measurements/review, canonical continuation, hosted CI and whole-composition
coverage remain separate requirements, documented in the matching handoff.

The first immutable963fad454 comprehensive serial measurement passed in1085.302s,
but release/migration selection failed because its exact revision list still ended
at0024 while actual head includes0025. That failure and head are retained. A
follow-up extends the exact list and existing asset/hash/preserving-downgrade
control to0025; no assertions are removed and no migration/runtime source is
changed. The subsequent head requires fresh receipts, not transfer of963 results.
The initially ineffective Python-clock late-expiry control was also retained and
corrected to expire the actual PG original session after provisional audit/intent
insertion: the final fence uses authoritative DB time, not Python identity time.
