# Package 10 Intake Detail Canonicalization Execution Addendum

- Addendum ID: `ODP-P10-INTAKE-CANON-001`
- Current status: `no_go_pending_CAN001_R3`
- Owner wave: `ODP-P10-CAN-001-R3B`
- Predecessor: `ODP-P10-CAN-001-R3A`
- Program next: `ODP-P10-CAN-001-R3A`
- Persistence state: `coordinator_checkpoint_complete`
- Canonical source: Package 10
- Canonical HTML detail landmark:
  `data-screen-label="Intake 收件處理詳情頁"`

## Binding Decision

Production must expose one intake detail UI. Package 10 requires:

- VDR-003: at 390px, `POSSIBLE_MATCH` shows an inline
  `DESKTOP_REQUIRED` state; tablet keeps the complete compare/correction flow;
- VDR-005: detail is durable and survives direct open, reload, browser
  back/forward, and return to the same inbox context;
- VDR-009: Package 10 cannot contain two intake UIs;
- canonical HTML lines 2359-2360: detail is a layer below Top Navigation,
  scrolls as one page, and constrains content to `max-width: 1160px`;
- canonical HTML line 2519: the mobile `DESKTOP_REQUIRED` result is explicit.

The current
`IntakeProcessingDetail -> IntakeDialogShell -> Timeline/Evidence/Receipts/Error`
tabbed modal is not the Package 10 detail composition.

## Verified Production Graph

```text
apps/web/src/app/operator/page.tsx
-> apps/web/features/operator/OperatorConsole.tsx
-> apps/web/features/operator/NetworkFindAreasWorkspace.tsx
-> apps/web/features/operator/network/ListingRadarPanel.tsx
-> apps/web/features/operator/network/intake/AssistedIntakeSection.tsx
-> apps/web/features/operator/network/intake/ListingInboxIntakeView.tsx
-> apps/web/features/operator/network/intake/IntakeProcessingDetail.tsx
```

The direct
`apps/web/src/app/intake/[intakeId]/page.tsx`
route must enter that same production composition. No route may enter an
alternate detail component.

## Continuous Production Composition

Refactor
`apps/web/features/operator/network/intake/IntakeProcessingDetail.tsx`
into the only detail composition. Render these regions in one continuous
document, in this order:

1. Return/deep-link header: intake ID, stage, outcome, policy, version, and
   preserved inbox context.
2. Submission summary: original/canonical URL, source, submitter, submitted
   time, owner, HeatZone/area.
3. Assignment/SLA: queue, claim, transfer, pause/resume, due state, conflict
   version, handoff reason, and assignment receipts.
4. Real processing stages:
   `SUBMITTED`, `CHECKING_IDENTITY`, `CHECKING_SOURCE_POLICY`,
   `AWAITING_ASSISTED_ENTRY`, `RETRIEVING`, `PARSING`, `MATCHING`,
   `NEEDS_REVIEW`, `READY`, `QUARANTINED`, and `FAILED`; never fabricate a
   percentage.
5. Source policy and evidence: original/canonical URL, captured/observed time,
   snapshot ID/hash, parser version, correlation ID, policy decision/version/
   expiry, WORM state, purpose binding, classification, retention/legal hold,
   masking/export state, actor/role/time, and evidence receipt.
6. Error/recovery: policy block, auth wall, source removal, bot challenge,
   retryable/non-retryable parser failure, partial result, stale snapshot,
   quarantine, error code, correlation ID, occurred time, next action, and
   preserved corrections.
7. Assisted entry when policy is `ASSISTED_ENTRY_ONLY`; never request raw
   credentials, cookies, tokens, or private endpoints.
8. Parsed-data lineage grouped by identity, location, commercial, property, and
   provenance. Every field distinguishes parsed, normalized, manually
   corrected, missing, and low-confidence values. Identity/address/rent/area/
   matching corrections require a reason.
9. Match result: `NEW`, `EXACT_DUPLICATE`, `REVISION`, `POSSIBLE_MATCH`, or
   `QUARANTINED`; include confidence, target, source ID, canonical URL,
   normalized address, area, floor, listing type, price/rent, agreeing signals,
   contradictory signals, and a screen-reader change summary.
10. Desktop/tablet comparison: existing versus submitted values side by side,
    with changed fields marked using text/icon/pattern in addition to color.
11. Human decisions: clearly separate `建立新物件`, `加入既有物件版本`,
    `標記重複`, and `送交資料管理員`; `POSSIBLE_MATCH` never auto-merges.
    Identity-impacting decisions require reason, before/after summary, actor,
    timestamp, version, and second-actor/risk safeguards.
12. Promotion/SiteScore: explicit review summary, non-optimistic write,
    downstream job state, candidate/report IDs, and durable receipt.
13. Receipts, timeline, and audit references as continuous sections, not tabs.

## Responsive Contract

- Desktop `>=1160`: full continuous detail and side-by-side comparison.
- Tablet `760-1159`: full submission, status, correction, comparison,
  decision, receipt, and promotion functionality.
- Mobile `<760`: URL submission, queue/status, claim, simple unambiguous
  confirmation, receipt, return, and deep link remain operable.
- Mobile `POSSIBLE_MATCH`: replace side-by-side comparison and identity
  decision controls with an inline `DESKTOP_REQUIRED` state. Keep all typed
  values, explain that the ambiguous comparison requires desktop, and expose
  the durable `/intake/{intakeId}` link.
- Mobile non-ambiguous outcomes may use a stacked change summary.
- 390, 1024, and 1440 must have no page-level horizontal overflow.
- The detail begins below Package 10 Top Navigation and uses
  `max-width: 1160px`.

## Exact Migrate-Before-Delete Contract

| Symbol/composition | Source | Target/action |
|---|---|---|
| `AssistedEntryForm` | `apps/web/features/operator/network/intake/IntakeDetailDialog.tsx` | Move to `apps/web/features/operator/network/intake/AssistedEntryForm.tsx`; production detail imports only the new file |
| `isSnapshotStale` | `apps/web/features/operator/network/intake/IntakeDetailDialog.tsx` | Move to `apps/web/features/operator/network/intake/intakeFreshness.ts` |
| `ListingCompareTable` | `apps/web/features/operator/network/intake/ListingCompareTable.tsx` | Keep the file and integrate it into production `IntakeProcessingDetail.tsx` |
| `MatchEvidencePanel` | `apps/web/features/operator/network/intake/MatchEvidencePanel.tsx` | Keep the file and integrate it into production `IntakeProcessingDetail.tsx` |
| assignment/SLA display | `apps/web/features/operator/network/intake/IntakeAssignmentSlaDialog.tsx` | Preserve any unique readable state in `AssignmentSlaSummary.tsx` or production detail; do not retain the alternate dialog |
| direct-route context | `apps/web/src/app/intake/[intakeId]/page.tsx` and `urlState.ts` | Keep one route contract and restore inbox filters/selection/return |

After migration, delete exactly:

```text
apps/web/features/operator/network/intake/IntakeDetailDialog.tsx
apps/web/features/operator/network/intake/IdentityDecisionPanel.tsx
apps/web/features/operator/network/intake/AssistedIntakeQueuePanel.tsx
apps/web/features/operator/network/intake/IntakeAssignmentSlaDialog.tsx
```

After migrating its assertions, also delete exactly:

```text
apps/web/features/operator/network/intake/__tests__/IdentityDecisionPanel.test.tsx
```

`IntakeDialogShell.tsx` may remain for true modal actions such as add, field
fix, transfer, pause, and decision confirmation. Production
`IntakeProcessingDetail.tsx` must not import or render it.

## Exact Unit-Test Migration

- Rewrite
  `apps/web/features/operator/network/intake/__tests__/Package10VisualP1.test.tsx`
  to mount `AssistedIntakeSection` or production `IntakeProcessingDetail`
  through real props and assert integrated compare/signals. A direct orphan
  `ListingCompareTable` mount is not runtime evidence.
- Delete
  `apps/web/features/operator/network/intake/__tests__/IdentityDecisionPanel.test.tsx`
  after moving needed compare/signal assertions into the production-detail
  suite.
- Remove `IntakeDetailDialog` mounts from
  `apps/web/features/operator/network/intake/__tests__/AssignmentSlaSummary.test.tsx`
  and assert the production assignment/SLA section.
- Change
  `apps/web/features/operator/network/intake/__tests__/IntakeProcessingDetail.test.tsx`
  to import `isSnapshotStale` from `intakeFreshness.ts` and add:
  continuous-section order, no tabs, real compare/signals, mobile
  `DESKTOP_REQUIRED`, tablet full compare, durable-route, and return-context
  coverage.

## API and Security Preservation

R3B may consume only existing authoritative intake/listing responses. It may
adapt existing response fields in `intakeClient.ts`, `intakeTypes.ts`, or
`types.ts`, but it must not:

- invent target listing or match-signal values;
- add fixture fallback in production;
- change endpoint, tenant, actor, source-policy, permission, self-review,
  second-actor, idempotency, ETag/version-conflict, WORM, or receipt semantics;
- use optimistic UI for merge, split, decision, or promotion;
- weaken a canonical assertion because production omitted a required state.

If the existing API does not provide authoritative comparison data, R3B records
a no-go blocker for CAN-002-R3. Placeholder data is forbidden.

Current blockers are the pending program-recovery coordinator checkpoint and
the incomplete R3A retirement prerequisite. This addendum does not claim
either checkpoint complete.

## Writable/Delete Boundary

R3B writable paths are exactly those listed for `ODP-P10-CAN-001-R3B` in
`PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md`. The four delete
paths above are the only intake visual deletions allowed by this addendum.
R3B must not edit `tests/e2e/**`, `apps/api/**`, auth middleware, source
archives, task ledgers, or another wave's ACK.

## Acceptance Gates

```text
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run build --workspace=@oday-plus/web
npx playwright test tests/e2e/operator-assisted-listing-intake-a11y.spec.ts --project=chromium
git diff --check
```

The unchanged accessibility spec is the axe gate. Static/import/orphan/route
gates must prove:

- only one production intake detail component is reachable;
- the four alternate/orphan files and all imports to them are absent;
- production imports `ListingCompareTable` and `MatchEvidencePanel`;
- production detail does not import `IntakeDialogShell`;
- detail renders continuous sections without Timeline/Evidence/Receipts/Error
  tabs;
- `390px + POSSIBLE_MATCH` renders inline `DESKTOP_REQUIRED`;
- 1024px and 1440px retain complete comparison and correction;
- `/intake/{id}` direct open/reload and browser return restore the same record
  and inbox context;
- no page-level overflow at 390, 1024, or 1440;
- no active Package 6/7 visual-baseline wording remains in intake product code;
- API clients, auth, source policy, permissions, WORM, and receipts remain
  fail-closed.

R3B passes only after its allowed changes and ACK are committed and pushed.
Until then, the program remains `no_go_pending_CAN001_R3`.
