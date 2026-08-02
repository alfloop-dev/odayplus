# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 29 independent review

- Reviewer: `CodexCoordinator`
- Reviewed exact pushed head: `0ed5157447f3d5b5fb36afc75e4d14f6cad2e8e2`
- Result: **CHANGES_REQUESTED / NO-GO**
- Scope: complete-batch authority, namespace, path-safety, corruption, and crash-recovery audit of the Round 28 remediation

## Verified improvement

The reviewed head now rejects missing, boolean, and floating-point schema versions,
unknown record fields, and leading/trailing whitespace IDs. It also writes a journal
intent before replacing the primary store. These corrections remain required, but
the journal is not yet an isolated, authenticated, fail-closed recovery authority.

## Blocking findings

### B31 — delivery IDs are filesystem paths, enabling journal path traversal

`DeliveryAuthorityRecord.from_dict`, `_validate_store_schema`, ingestion, and
`atomic_consume_if_valid` only require a non-empty, whitespace-canonical delivery
ID. `_write_journal_intent` then interpolates that untrusted ID into both a temp-file
prefix and the final journal path. An authenticated record whose delivery ID is
`../escaped` writes the committed intent outside the store's journal directory.

Exact-head mutation result:

```text
TRAVERSAL_RESULT (True, 'DELIVERED', None)
OUTSIDE_INTENTS ['escaped.intent']
```

This is a path traversal and a cross-authority state-write primitive. A durable ID
must never be used as a filename without a fixed safe encoding or digest and a
post-resolution containment check.

### B32 — different authority stores share one journal namespace

The journal path is derived from `store_path.stem`. Consequently sibling stores
`authority.json` and `authority.yaml` share `authority_journal`. Consuming
`shared-id` in the first store causes a fresh read of the second independent store
to reconcile the first store's journal and reject its own record as already
consumed.

```text
COLLISION True
store1 (True, 'DELIVERED', None)
store2 (False, 'PENDING_VERIFICATION', "... already been consumed ...")
```

This violates store/tenant/source isolation. Lock and journal namespaces must bind
to the exact canonical store identity, not merely the filename stem, and that
identity must be included in authenticated journal content.

### B33 — corrupt durable intent is silently discarded and replay proceeds

`_reconcile_journal_intents` catches every exception and continues. A malformed
intent therefore becomes indistinguishable from no intent. The mutation placed a
corrupt `corrupt-id.intent` before retrying the same authority record; the store
overwrote it and returned delivery success:

```text
CORRUPT_INTENT (True, 'DELIVERED', None)
```

A corrupt/unreadable/unexpected intent must fail closed and surface an explicit
indeterminate authority state. It must not be skipped or overwritten by a retry.

### B34 — caller-writable JSON can fabricate authoritative consume state

Journal reconciliation accepts any JSON object with integer version `1`, a
non-empty ID, and status `CONSUMED`. It does not verify a signature, bind the
provider receipt/request/release/route/issuer/store identity, validate the intent
timestamp, or even require an exact set of fields. Creating such a local file for
an otherwise valid unconsumed record permanently changes the result:

```text
FORGED_INTENT
(False, 'PENDING_VERIFICATION', "... already been consumed ...")
```

The production acceptance explicitly requires an external durable authority and
rejects locally fabricated provider state. A local unauthenticated intent may be a
crash-recovery mechanism, but it cannot by itself become the authority for a
production delivery transition.

### B35 — failed reconciliation persistence is swallowed

`_read_store_data` catches and discards every exception from the atomic store write
after journal reconciliation. This hides a durability failure from callers, leaves
the primary store divergent from the journal, and reports an ordinary replay result
instead of a durable indeterminate/error state. No authority transition may claim a
successfully reconciled outcome after its required persistence boundary fails.

## Required complete-batch remediation

1. Replace raw ID filenames with a fixed safe encoding/digest, enforce exact ID
   canonicalization, and prove containment for temp and final paths. Add traversal,
   separator, dot-segment, absolute-path, Unicode-equivalent, and overlong-ID tests.
2. Give each exact store/source/tenant an isolated lock and journal namespace. Bind
   journal records to that exact identity and add same-stem/cross-directory and
   cross-tenant collision tests.
3. Define and enforce one canonical journal schema with exact keys and types. Treat
   malformed, unreadable, unknown-version, duplicate/conflicting, or noncanonical
   intents as explicit fail-closed indeterminate state; never silently skip or
   overwrite them.
4. Authenticate or independently reconcile every recovery intent against the
   external provider/authority. Bind delivery ID, provider receipt, request hash,
   release SHA, route, issuer/key identity, store/tenant identity, timestamp, and
   transition state. A caller-created local file must not fabricate authority.
5. Propagate journal and primary-store fsync/write/reconciliation failures. Model
   crash outcomes at every persistence boundary and prove a fresh process can
   neither double-deliver nor claim a transition whose authority is unproven.
6. Preserve all Round 26–28 concurrency, canonical-schema, signature, replay,
   provider-binding, route, project/release, and Human/Ops evidence gates. Run the
   entire packet and its negative matrix before the next handoff.
7. Keep external provider/on-call/watch-window/Human evidence pending and release
   `NO-GO`. Do not open/refresh a PR, merge, roll out runtime, or deploy a partial
   fix.

No approval, PR, merge, runtime rollout, or deployment is authorized from this
head.
