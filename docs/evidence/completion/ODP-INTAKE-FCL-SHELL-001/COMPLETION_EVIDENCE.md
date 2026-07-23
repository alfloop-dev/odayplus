---
task_id: ODP-INTAKE-FCL-SHELL-001
baseline_commit: c900e906f96cb3750274c24e1a8f2922999f9048
branch: task/ODP-INTAKE-FCL-SHELL-001
production_route: /w/expansion/listings/intake/:intakeId
status: ready-for-independent-review
updated_at: 2026-07-23
---

# ODP-INTAKE-FCL-SHELL-001 Completion Evidence

## Closed Contract

This task closes the shell-owned portion of `FCF-001` and all seven outcomes
listed under `ODP-INTAKE-FCL-SHELL-001`:

1. `/w/expansion/listings/intake/:intakeId` is a real App Router page with
   route loading and error boundaries.
2. The page restores its intake from the detail API and keeps section,
   compare target, compare mode, task, role, and subject in URL state.
3. Listing Inbox row selection opens a preview-only drawer.
4. Correction, comparison, identity decisions, assignment, recovery, durable
   receipts, and Candidate Site promotion are mounted on the durable page.
5. The production route reaches every required detail component.
6. A busy high-risk modal cannot close by Escape, overlay, close, or cancel.
7. Loading, missing-record, permission-denied, action error/conflict, and
   recovery surfaces remain on the durable route.

The compatibility route `/intake/:intakeId` redirects only to the required
durable route. It does not redirect to
`/w/expansion/listings?selected=...&dialog=detail`.

## Production Import Graph

```text
apps/web/src/app/w/expansion/listings/intake/[intakeId]/page.tsx
└── AssistedIntakeDetailPage
    └── AssistedIntakeSection (detailIntakeId)
        ├── intakeApi.get(intakeId)
        ├── IntakeProcessingDetail (presentation="page")
        │   ├── IntakeDialogShell (page boundary)
        │   ├── IntakeStageTimeline
        │   ├── EvidencePanel (parsed-field lineage rows and corrections)
        │   ├── IdentityDecisionPanel
        │   │   ├── ListingCompareTable
        │   │   └── MatchEvidencePanel
        │   ├── AssignmentSlaSummary
        │   ├── DurableReceiptPanel
        │   ├── PromotionReviewPanel
        │   │   └── SiteScoreJobStatus
        │   └── IntakeErrorRecovery
        ├── IntakeFieldFixDialog
        ├── IntakeDecisionDialog
        ├── TransferIntakeDialog
        └── PauseSlaDialog

/w/expansion/listings
└── AssistedIntakeSection (Inbox mode)
    ├── ListingInboxIntakeView
    └── IntakeDetailDialog (previewOnly)
        └── "開啟完整收件頁面"
            └── /w/expansion/listings/intake/:intakeId
```

Candidate promotion is withheld until the durable promotion lookup completes.
The request form therefore cannot appear before the page knows whether a
promotion decision already exists.

## Browser and API Evidence

Focused Playwright:

```text
OPSBOARD_PORT=3118 ODP_API_PORT=8118 \
ODP_API_BASE_URL=http://127.0.0.1:8118 \
npx playwright test \
  tests/e2e/operator-assisted-listing-intake-durable-route.spec.ts \
  --project=chromium --workers=1

3 passed
```

The test uses the real FastAPI and Next.js servers without route interception.
It records a real URL intake through
`POST /api/v1/operator/network-listings/intake/submit`, then direct-opens and
reloads `/w/expansion/listings/intake/IN-3001`. Browser traffic read the
persisted record through
`GET /api/v1/operator/network-listings/intake/IN-3001` and restored promotion
state through `GET /api/v1/intakes/IN-3001/promotion-decision`.

The scenarios prove:

- direct open and reload on the exact required route;
- external source opens in a new page without replacing intake state;
- timeline -> evidence -> identity history works with back and forward;
- `compareTarget=LISTING-SHELL-TARGET` and `task=TASK-SHELL-001` survive;
- timeline, evidence/field rows, identity/compare, assignment, receipts, and
  promotion are visible through the production route;
- Inbox row selection exposes only the preview drawer, whose CTA reaches the
  durable route;
- missing and denied states stay at the requested intake URL.

## Automated Verification

```text
npm run typecheck --workspace=@oday-plus/web
PASS

npm run test --workspace=@oday-plus/web -- \
  --run features/operator/network/intake/__tests__
PASS: 9 files, 90 tests

npm run build --workspace=@oday-plus/web
PASS
Route manifest:
ƒ /w/expansion/listings/intake/[intakeId]  275 B  159 kB

git diff --check
PASS
```

The production build reports pre-existing autoprefixer warnings in
`designAligned.module.css`, `governance.module.css`, and
`networkFindAreas.module.css`. Those files are outside this task and the build
completed successfully.

## Remaining Cross-Task Work

No shell-owned requirement remains open. End-to-end functional closure still
depends on the separately dispatched tasks:

- `FCL-RUNTIME-001`: canonical persisted revisions and identity effects;
- `FCL-ROLES-001`: all six role modes and backend-aligned grants;
- `FCL-INBOX-001`: complete Inbox/map/filter contract;
- `FCL-REVIEW-001`: complete field lineage and durable assisted-entry drafts;
- `FCL-IDENTITY-001`: authoritative reversible identity commands;
- `FCL-EVIDENCE-001`: authoritative evidence and receipt payloads;
- `FCL-LIFECYCLE-001`: polling/subscription and complete lifecycle controls;
- `FCL-INTEGRATION-001`: merge all Wave 1 slices and run the full product suite;
- `FCL-ACCEPTANCE-001`: independent requirement-by-requirement closure review.

These dependencies are not replaced or simulated by this shell task.
