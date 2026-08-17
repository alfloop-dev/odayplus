---
task_id: ODP-P10-CAN-001-R3D
title: Package 10 retryable intake preserved-input remediation
status: dispatched
owner: Claude2
reviewer: Antigravity4
source_branch: origin/fix/package10-final-20260725
updated_at: 2026-07-26
---

# ODP-P10-CAN-001-R3D

## Finding

`ODP-P10-CAN-003-R3A` found a deterministic product failure in the canonical
Package 10 assisted-listing detail:

1. Submit `https://www.synthetic.example/detail-50000001.html`.
2. The durable intake reaches retryable `FAILED` with
   `ODP-INTAKE-RETRIEVAL-TIMEOUT`.
3. Open `展開保留輸入參數 (Preserved Input)`.
4. The canonical detail renders `{}` instead of the submitted URL or other
   durable input.

The failing gate is:

```text
operator-network-assisted-intake.spec.ts
retryable failure shows code, correlation and next action, and retry preserves input
Expected: https://www.synthetic.example/detail-50000001.html
Received: {}
```

`IntakeProcessingDetail` currently builds `preservedInput` only from
`record.parsedFields`. Retrieval can fail before parsing, so the record still
has durable `originalUrl` and related submission context while the rendered
preserved-input object is empty.

This violates `ODAY_PLUS_ASSISTED_LISTING_INTAKE_DESIGN_REQUIREMENTS.md`
section 7: user-entered corrections and input must survive retryable failures.

## Assignment

Fix the canonical product boundary. Build the preserved-input view from the
durable API-backed intake record, including the submitted original URL even
when parsing never starts. Preserve canonical/normalized URL and corrected
field values when available.

Do not store a second browser-only copy, inject fixtures, weaken the Package 10
E2E assertion, or restore an alternate intake visual. Sensitive keys must
remain redacted by the existing purpose-binding control.

## Writable Paths

- `apps/web/features/operator/network/intake/IntakeProcessingDetail.tsx`
- `apps/web/features/operator/network/intake/IntakeErrorRecovery.tsx`
- `apps/web/features/operator/network/intake/__tests__/IntakeProcessingDetail.test.tsx`
- `apps/web/features/operator/network/intake/__tests__/IntakeErrorRecovery.test.tsx`
- `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3D.json`

## Forbidden Paths

- `tests/e2e/**`
- `apps/api/**`
- `apps/web/features/operator/network/intake/AssistedIntakeSection.tsx`
- Package 10 canonical HTML and archived design evidence
- legacy or retired Operator visual implementations

## Acceptance

1. A retryable failure before parsing exposes the durable `originalUrl` in the
   preserved-input drawer instead of `{}`.
2. `canonicalUrl`, HeatZone context, and parsed/normalized/corrected values are
   included when the API-backed intake record supplies them.
3. Missing values remain absent or explicitly unavailable; no business value
   is fabricated.
4. Keys containing token, password, secret, or credential data remain redacted.
5. Page reload and retry use the durable intake record; no local fixture or
   session-only shadow state is introduced.
6. Unit coverage proves pre-parse failure, corrected-field preservation, and
   sensitive-key redaction behavior.
7. Web unit tests, web typecheck, root typecheck, build, and `git diff --check`
   pass.
8. The final diff does not touch any of the 16 `ODP-P10-CAN-003-R3A` specs.
9. After this task merges, R3A reruns the blocked retry test, the complete
   25-test assisted-intake spec, and later the 16-spec/107-test gate.
10. Independent Antigravity review is required. If quota remains unavailable,
    leave the task in review and report the blocker.

## Conflict Gate

- `ODP-P10-CAN-003-R3A` owns the 16 canonical E2E specs and must not edit this
  task's product paths.
- `ODP-P10-CAN-001-R3C` owns Governance product files and has no writable-path
  overlap with this task.
- Any need to change API behavior, an E2E assertion, or a retired visual is an
  immediate coordinator NO-GO before editing.

## Verification

```bash
npm run test --workspace=@oday-plus/web -- IntakeProcessingDetail
npm run test --workspace=@oday-plus/web -- IntakeErrorRecovery
npm run test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run typecheck
npm run build
git diff --check
```
