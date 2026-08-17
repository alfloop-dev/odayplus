# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 27 independent review

- Reviewer: `CodexCoordinator`
- Reviewed exact pushed head: `30df945e44d938b0ab77512ae9097efd62d39386`
- Result: **CHANGES_REQUESTED / NO-GO**
- Scope: complete execution packet, with adversarial replay of the Round 26
  durable authority-store remediation

## Verified improvement

The fixed-name temporary-file race from Round 26 is closed. The store now uses
one OS-visible `flock` shared by independent instances/processes, collision-free
same-directory temporary files, file flush/fsync, atomic replace, and persistent
consumption state. The new two-instance and process-pool tests exercise the
original race rather than only one Python object.

## Blocking findings

### B27 — syntactically valid structural corruption escapes fail-closed handling

`_read_store_data()` checks only that the top level is a JSON object. It does
not validate that `records` is an object, `consumed` is a list of unique string
IDs, or that no incompatible schema is present. Both `get_authority_record()`
and `atomic_consume_if_valid()` then call `.get()` on `records` outside the
protected read block.

Exact-head mutation using the valid JSON document
`{"records": [], "consumed": []}` produced:

```text
shape get RAISED AttributeError 'list' object has no attribute 'get'
shape consume RAISED AttributeError 'list' object has no attribute 'get'
```

This violates the Round 26 requirement that corrupt/unreadable store state
produce an explicit fail-closed result with no unhandled exception. Invalid
`consumed` types and malformed record collections need the same treatment.

### B28 — failed directory durability is swallowed while returning `DELIVERED`

`_write_store_data_atomic()` catches and discards every `OSError` from opening
or fsyncing the parent directory. The consume transaction therefore returns
`DELIVERED` even when the required directory metadata durability step failed.

Exact-head mutation allowed the file fsync and injected failure on the second
fsync (the parent directory):

```text
dir_fsync returned (True, 'DELIVERED', None) fsync_calls 2
```

The method cannot claim a durable, restart-safe transition after swallowing
that failure. A write/replace/fsync failure must never escape as `DELIVERED`;
the result must remain explicitly pending/indeterminate and operationally
observable without enabling replay or double delivery.

## Required complete-batch remediation

1. Define and strictly validate the full durable-store schema before any use:
   exact container types, string delivery IDs, valid record objects, unique
   consumed IDs, and a version/migration policy. Every malformed valid-JSON
   shape must return an explicit fail-closed result, not raise or self-heal.
2. Cover `get_authority_record`, ingestion, initialization, and atomic consume;
   no path may silently replace a malformed store or leak an unhandled schema
   exception.
3. Treat temp creation, serialization, flush, file fsync, replace, directory
   open/fsync/close, and lock failures as delivery-state failures. Do not report
   `DELIVERED` unless the consumed transition is durably committed.
4. Specify and implement recovery for the uncertain boundary where replace
   succeeds but directory fsync fails. It must avoid both a false delivered
   claim and a second delivery; use a transactional durable backend/protocol if
   a single JSON replace cannot provide the required semantics.
5. Add permanent mutations for malformed top-level and nested shapes, wrong
   `records`/`consumed` types, duplicate/invalid consumed IDs, malformed record
   entries, every write/fsync/replace failure point, fresh-process recovery,
   and concurrent retry after an injected durability failure.
6. Re-run the complete observability packet, not only store tests. Preserve
   authentic provider, on-call, watch-window, exact-release, and Human/Ops
   evidence as pending and keep release `NO-GO`.

No PR, merge, runtime rollout, or deployment is authorized from this head. A
new exact pushed implementation head requires a fresh independent full-packet
review.

## Verification receipts

```text
python3 -m pytest -q tests/reliability/test_runtime_observability.py
58 passed; 5 warnings

ruff check modules/notifications/domain/authority.py \
  tests/reliability/test_runtime_observability.py
All checks passed

ruff format --check modules/notifications/domain/authority.py \
  tests/reliability/test_runtime_observability.py
FAILED: both files would be reformatted

git diff --check
PASS
```

The passing owner/focused suite does not cover either blocking mutation. The
format discrepancy is not the authority-store root cause, but it must also be
resolved or explicitly reconciled in the next exact-head verification report.
