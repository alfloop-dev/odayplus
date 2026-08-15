# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 28 independent review

- Reviewer: `CodexCoordinator`
- Reviewed exact pushed head: `ee4d0e7d3325c7365829bfb1bd9532b681681add`
- Result: **CHANGES_REQUESTED / NO-GO**
- Scope: complete packet and crash/replay audit of the Round 27 remediation

## Verified improvement

The reviewed code now rejects the Round 27 `records: []` reproducer without an
unhandled exception and no longer reports `DELIVERED` directly when a write or
fsync call raises. The focused owner suite passes. Those corrections remain
required.

## Blocking findings

### B29 — the claimed strict/versioned schema still accepts ambiguous shapes

The schema version is optional and checked with
`isinstance(v, (int, float)) and v == 1`. In Python/JSON mapping this accepts a
missing version, boolean `true`, and floating-point `1.0` as schema v1. Record
objects also accept unknown fields. Consumed IDs are only tested with
`item.strip()` for non-emptiness but are stored/compared without canonicalizing
or requiring equality with the stripped value.

Exact-head results:

```text
schema_ACCEPTED <missing>
schema_ACCEPTED True
schema_ACCEPTED 1.0
whitespace_consume (True, 'DELIVERED', None)
```

The whitespace mutation used a record for delivery ID `d` and a purported
consumed marker `" d "`. Validation accepted the store and consuming `d`
returned `DELIVERED`, bypassing replay state. This is not an exact schema or a
safe migration policy.

### B30 — post-replace directory-fsync failure remains an unresolved crash ambiguity

The implementation performs replace before directory fsync. When the latter
fails, it returns `PENDING_VERIFICATION`, but the only recovery test immediately
re-reads the still-running filesystem and observes the new entry as consumed.
That does not establish restart/power-loss durability. A failed directory fsync
means the implementation has not excluded the valid crash outcome in which the
old directory entry survives.

Exact-head crash-outcome mutation:

```text
first_after_dir_fsync_failure =
  (False, 'PENDING_VERIFICATION', '... directory fsync failure')
fresh_after_allowed_rollback = (True, 'DELIVERED', None)
```

The mutation restored the pre-transition bytes to represent the directory-entry
rollback that the failed fsync did not rule out, then opened a fresh store. The
same delivery was accepted as `DELIVERED`. There is no independently durable
intent/indeterminate record, transaction log, database transaction, or authority
idempotency key that can distinguish/reconcile this state after a crash.

## Required complete-batch remediation

1. Make the schema canonical: require an integer (not boolean) exact version,
   reject floats/missing versions except through an explicit atomic migration,
   reject unknown record fields, require IDs to equal their canonical form, and
   validate all cross-field/set invariants.
2. Add permanent mutations for missing/boolean/float versions, whitespace and
   Unicode-equivalent IDs, unknown fields, orphan consumed IDs, and replay
   bypass. Every noncanonical state must fail closed without mutation.
3. Replace the single-file protocol or add a real transactional recovery
   protocol. A post-replace/pre-directory-fsync failure must be represented as
   durable indeterminate state and reconciled with an authoritative idempotent
   delivery/readback source; immediate same-process reread is insufficient.
4. Test fresh-process and modeled crash recovery at every persistence boundary,
   including old-entry survival after failed directory fsync. Prove no execution
   can produce a second delivery and no unproven transition is reported as
   delivered.
5. Keep external provider/on-call/watch-window/Human evidence pending and the
   release `NO-GO`; do not convert local fault-injection results into live proof.
6. Re-run the entire execution packet and obtain a new exact-head independent
   review. Do not open/refresh a PR or deploy after only patching one mutation.

No PR, merge, runtime rollout, or deployment is authorized from this head.
