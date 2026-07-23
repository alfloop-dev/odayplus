---
task_id: ODP-INTAKE-FCL-REVIEW-001
title: Field lineage and durable assisted entry completion evidence
baseline_commit: c900e906f96cb3750274c24e1a8f2922999f9048
branch: task/ODP-INTAKE-FCL-REVIEW-001
status: ready-for-integration
updated_at: 2026-07-23
---

# ODP-INTAKE-FCL-REVIEW-001 Completion Evidence

## Owned implementation

- `AssistedEntryForm.tsx`
  - exposes all five field groups;
  - submits only in `ASSISTED_ENTRY_ONLY`;
  - has no retrieval or credential input path;
  - sends `retrievalAllowed: false`;
  - preserves one operation ID through failure/conflict/retry;
  - clears a draft only after a `COMMITTED` result.
- `ParsedDataReview.tsx`
  - renders all five groups as semantic tables;
  - renders parsed, normalized, corrected, and effective columns;
  - exports canonical and legacy adapters plus a screen-reader change summary.
- `FieldLineageRow.tsx`
  - renders missing, low-confidence, and masked states with text;
  - does not disclose masked values;
  - renders correction actor/role/time/reason, before/after, snapshot, parser
    run, reviewer, version, supersedes, and reversal lineage;
  - exports the material-correction review contract.
- `useCorrectionDraft.ts`
  - stores a versioned draft by tenant, intake, actor, purpose, and field;
  - survives close/unmount/reload;
  - preserves failure and conflict state;
  - normalizes an interrupted submission to `SUBMISSION_RESULT_UNKNOWN`;
  - retains the stable operation ID for idempotent retry;
  - never makes a draft authoritative and only exposes an explicit
    `clearAfterCommit`.
- `IntakeFieldFixDialog.tsx`
  - uses the durable draft controller when an integration scope is provided;
  - displays all four value layers and available lineage;
  - requires reason and risk acknowledgement;
  - marks material changes for independent review;
  - exposes operation ID, base version, and review requirement to its caller.

## Clean integration boundary

The Wave 2 integration task should:

1. use `buildCanonicalFieldReview(detail.fields, lineageContext)` with
   server-returned `effective` values and correction lineage;
2. mount `ParsedDataReview` and `AssistedEntryForm` on the durable detail page;
3. pass `tenantId`, `intakeId`, and authenticated `actorSubjectId` as the draft
   identity;
4. bind `AssistedEntryForm.onCommit` only to the manual/correction command,
   never to retrieval;
5. return `COMMITTED` only after correction receipt plus refreshed detail
   readback;
6. return `CONFLICT` with the server's current version without clearing the
   draft;
7. pass `submissionState="COMMITTED"` to `IntakeFieldFixDialog` only after
   authoritative readback.

The owned components intentionally do not edit or mount themselves inside
`IntakeDetailDialog`, `IntakeProcessingDetail`, or route composition.

## Focused proof

Command:

```text
npm run test --workspace=@oday-plus/web -- --run \
  features/operator/network/intake/__tests__/FieldLineageReview.test.tsx \
  features/operator/network/intake/__tests__/AssistedEntryForm.test.tsx \
  features/operator/network/intake/__tests__/IntakeFieldFixDialog.test.tsx
```

Result:

```text
Test Files  3 passed (3)
Tests       13 passed (13)
```

Covered behaviors:

- all five groups and all four value layers;
- complete correction/snapshot/parser/supersession lineage;
- masked-value non-disclosure;
- explicit missing and low-confidence states;
- screen-reader aggregate and per-row summaries;
- material reason/risk/independent-review gates;
- close/reload persistence;
- network-result-unknown persistence;
- version-conflict preservation and rebase;
- same-operation retry;
- numeric field serialization;
- server-confirmed-commit-only draft clearing;
- `ASSISTED_ENTRY_ONLY` fail-closed policy guard and no credential inputs.

Regression command:

```text
npm run test --workspace=@oday-plus/web -- --run \
  features/operator/network/intake/__tests__
```

Result:

```text
Test Files  11 passed (11)
Tests       98 passed (98)
```

Static checks:

```text
npm run typecheck --workspace=@oday-plus/web
npx eslint <all task-owned TypeScript and test files>
npm run build --workspace=@oday-plus/web
```

Result: passed. ESLint emitted only the repository's pages-directory discovery
notice and no lint errors. The production build completed all 19 static pages;
its only warnings were pre-existing autoprefixer `start`/`end` compatibility
warnings in unrelated operator CSS modules.

## Integration-stage proof still required

These checks belong to `ODP-INTAKE-FCL-INTEGRATION-001`, not this task's owned
composition paths:

- production import graph from the durable intake route;
- real API readback of correction receipt, corrected/effective values, and
  lineage;
- browser reload/conflict/retry E2E on the durable detail route;
- confirmation that runtime returns authoritative `effective`, snapshot,
  parser-run, and correction-chain fields.

The integration task must not substitute the legacy adapter's absent effective
value with a client-derived value; the component intentionally renders
`有效值尚未提供` until authoritative data exists.
