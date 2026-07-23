---
task_id: ODP-INTAKE-FCL-EVIDENCE-001
status: ready-for-integration
baseline_commit: c900e906f96cb3750274c24e1a8f2922999f9048
implementation_commit: 8efcf4600a66183a8933f79e91863485c4386721
branch: task/ODP-INTAKE-FCL-EVIDENCE-001
updated_at: 2026-07-23
---

# ODP-INTAKE-FCL-EVIDENCE-001 Completion Summary

## Scope delivered

The task-owned evidence slice now exports four authoritative integration
surfaces:

```text
apps/web/features/operator/network/intake/DurableReceiptPanel.tsx
apps/web/features/operator/network/intake/EvidencePanel.tsx
apps/web/features/operator/network/intake/StructuredAuditTimeline.tsx
apps/web/features/operator/network/intake/IntakeErrorRecovery.tsx
```

The local response contracts used by those surfaces are exported from:

```text
apps/web/features/operator/network/intake/evidenceContracts.ts
```

Delivered behavior:

1. `DurableReceiptPanel` renders no panel when no server receipt exists;
2. submission, assignment, SLA, correction, decision, identity, promotion,
   job, source-evidence, export, and verification receipts render only from
   supplied response objects;
3. the panel no longer derives or defaults correlation IDs, audit IDs,
   Listing/Candidate IDs, receipt IDs, versions, statuses, hashes,
   verification results, signatures, parser runs, or WORM state;
4. copy/export serializes only the supplied authoritative receipt bundle;
5. `EvidencePanel` exposes original/canonical URL, source snapshot, parser run
   and version, observed/captured times, policy version/expiry, correlation,
   purpose binding, classification, expiry, masking, legal hold, export result,
   checksum/signature verification, and audit references;
6. masked fields never render parsed, normalized, corrected, or effective
   values;
7. no field classification, confidence, effective value, parser run, ETag, or
   human decision is inferred by the UI;
8. `StructuredAuditTimeline` renders actor, role, time, action, result, reason,
   before/after, snapshot/parser lineage, related entity IDs, correlation,
   version, evidence state, audit ID, and message;
9. `IntakeErrorRecovery` renders exact server code and summary, correlation,
   occurred time, retryability, state/version, operation, current server value,
   preserved input, field errors, retry metadata, ETag/owner conflict metadata,
   and next action;
10. missing error metadata is identified as absent instead of being filled
    with a synthetic code, timestamp, correlation, state, version, input, or
    recovery action;
11. nested credential, token, password, cookie, authorization, and secret
    values are redacted from preserved-input display.

## Anti-fabrication proof

Focused render tests prove that a record alone produces no receipt surface and
that none of the previous fallback patterns are emitted:

```text
CORR-<intake>
AUD-<intake>
AUD-DEC-99
LISTING-<intake>
SITE-<target>
PR-RUN-88412
sha256:e3b0c442...
Verified Valid
SECURE WORM LOGGED
```

The checksum, signature, signer key version, WORM sink receipt, evidence state,
audit event, and correlation assertions use an explicit API-response fixture.
The UI performs no digest or signature generation.

The tests also render all named section 10 error families with the full
recovery context:

```text
PRECONDITION_REQUIRED
VERSION_CONFLICT
IDEMPOTENCY_KEY_REUSED
OWNER_CONFLICT
REVIEW_CONFLICT
WORK_INCOMPLETE
LEGAL_HOLD_CONFLICT
SELF_REVIEW_DENIED
SOURCE_POLICY_DENIED
SCOPE_DENIED
OWNERSHIP_REQUIRED
CORRECTION_INVALID
RISK_ACKNOWLEDGEMENT_REQUIRED
RETRIEVAL_TIMEOUT
PAGE_REMOVED
AUTH_WALL
BOT_CHALLENGE
PARSER_PARTIAL
PARSER_RETRYABLE
PARSER_PERMANENT
STALE_SNAPSHOT
QUARANTINED
RETRY_BUDGET_EXHAUSTED
DEAD_LETTER
```

## Verification

Executed against implementation commit
`8efcf4600a66183a8933f79e91863485c4386721`:

```text
node_modules/.bin/vitest run \
  apps/web/features/operator/network/intake/__tests__/EvidenceSurfaces.test.tsx \
  --config apps/web/vitest.config.ts
PASS - 1 file, 34 tests

node_modules/.bin/vitest run apps/web --config apps/web/vitest.config.ts
PASS - 9 files, 119 tests

node node_modules/typescript/bin/tsc --noEmit -p apps/web/tsconfig.json
PASS

node_modules/.bin/eslint \
  apps/web/features/operator/network/intake/DurableReceiptPanel.tsx \
  apps/web/features/operator/network/intake/EvidencePanel.tsx \
  apps/web/features/operator/network/intake/IntakeErrorRecovery.tsx \
  apps/web/features/operator/network/intake/StructuredAuditTimeline.tsx \
  apps/web/features/operator/network/intake/evidenceContracts.ts \
  apps/web/features/operator/network/intake/__tests__/EvidenceSurfaces.test.tsx
PASS - no warnings or errors

git diff --check
PASS
```

The dependency directories used for verification were temporary symlinks to an
existing installation. They were removed before both commits and are not task
artifacts.

## Production route and integration boundary

This Wave 1 task was explicitly prohibited from editing Shell composition.
Accordingly, it does not claim production-route or persisted-readback proof.

`DurableReceiptPanel` remains compatible with the existing detail composition,
but it now hides itself unless that composition supplies a real receipt.
`EvidencePanel`, `StructuredAuditTimeline`, and `IntakeErrorRecovery` are clean
exports for the Integration task.

The Integration task must mount all four surfaces on:

```text
/w/expansion/listings/intake/:intakeId
```

and map the canonical detail/readback response into the exported contracts.

## Requirement rows advanced

The implementation boundary closes the component-level portions of:

- section 8.7: no optimistic/fabricated durable receipt presentation;
- section 8.9: complete audit, source, purpose, classification, expiry,
  masking, legal-hold/export, and verification presentation;
- section 10: complete, non-fabricated error and recovery presentation.

These requirement rows are not marked product-level `PASS` until the
production route supplies persisted API responses and browser E2E proves the
mounted behavior.

## Remaining cross-task proof

The following work is intentionally not claimed by this isolated task:

1. Runtime must persist and return submission, correction, decision, identity,
   promotion, job, evidence/export, verification, and audit response fields.
2. Shell/Integration must replace the legacy compact timeline with
   `StructuredAuditTimeline` and mount the evidence/recovery surfaces on the
   durable route.
3. Integration must preserve draft input while each named error is rendered.
4. Integration must add browser E2E for every named section 10 error family.
5. Integration must prove persisted readback for receipt, audit, source,
   legal-hold/export, checksum/signature, and WORM fields.
6. An independent Fleet must inspect the production import graph and rerun the
   route/readback/E2E evidence at the integrated commit.

Until those dependencies land, this slice is `ready-for-integration`, not proof
that the complete Assisted Listing Intake workflow is functionally complete.
