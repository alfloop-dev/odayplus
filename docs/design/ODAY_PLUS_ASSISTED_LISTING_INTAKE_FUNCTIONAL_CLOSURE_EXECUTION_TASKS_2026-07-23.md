---
doc_id: ODP-INTAKE-FUNCTIONAL-CLOSURE-EXEC-001
title: ODay Plus Assisted Listing Intake Functional Closure Execution Tasks
version: 1.0.0
status: approved-for-dispatch
owner: Product Platform Engineering
audit: ODP-INTAKE-FUNCTIONAL-AUDIT-001
audited_commit: eb94d53b0d1af9beb1cf6b290ce5e6b4d98b6585
target_branch: task/ODP-INTAKE-FUNCTIONAL-CLOSURE-001
updated_at: 2026-07-23
---

# ODay Plus Assisted Listing Intake Functional Closure Execution Tasks

## 1. Objective

Close every functional gap in
`ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_COMPLETENESS_AUDIT_2026-07-23.md`
and prove the complete `ODP-UXD-003-ADD-002` version 1.0.1 workflow.

These tasks supersede the functional completion claims of the previous intake
UI tasks. Previous code may be reused, but previous completion evidence cannot
close a task unless it proves the current acceptance contract at the exact
implementation commit.

Machine-readable authority:

`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_CLOSURE_EXECUTION_TASKS_2026-07-23.json`

## 2. Completion Rules

A Fleet must not mark a task complete based on:

- component or helper existence;
- unit tests without a production caller;
- fixture or mock-provider execution;
- client-generated receipt, verification, hash, or entity ID;
- screenshots without persisted API readback;
- an Inbox dialog standing in for the durable detail page;
- direct mutation of Listing fields standing in for ListingRevision;
- a selected role subset standing in for the six required role modes;
- tests that skip the route, role, conflict, or persistence requirement.

Every implementation task must provide:

1. exact commit SHA;
2. production route used;
3. request and response evidence;
4. persisted readback evidence;
5. focused tests;
6. an E2E scenario when the task changes user-visible behavior;
7. a list of requirement rows closed;
8. a list of remaining rows, if any;
9. independent review by a different Fleet.

## 3. Task Waves

| Wave | Task | Owner | Depends on | Functional scope |
|---|---|---|---|---|
| 1 | `ODP-INTAKE-FCL-RUNTIME-001` | Claude2 | Approved contracts | Canonical intake runtime, revision, identity, lifecycle persistence |
| 1 | `ODP-INTAKE-FCL-SHELL-001` | Codex2 | None | Durable routes and production detail composition |
| 1 | `ODP-INTAKE-FCL-ROLES-001` | Codex | None | Six role and permission modes |
| 1 | `ODP-INTAKE-FCL-INBOX-001` | Antigravity3 | None | Complete Inbox, map, filters, Add URL |
| 1 | `ODP-INTAKE-FCL-REVIEW-001` | Antigravity4 | None | Field lineage, assisted entry, durable drafts |
| 1 | `ODP-INTAKE-FCL-IDENTITY-001` | Antigravity5 | Approved identity contract | Compare and reversible identity decisions |
| 1 | `ODP-INTAKE-FCL-EVIDENCE-001` | Antigravity6 | None | Authoritative receipts, audit, evidence, errors |
| 1 | `ODP-INTAKE-FCL-LIFECYCLE-001` | Antigravity7 | None | Stage/job updates, assignment/SLA, cancel/retry/DLQ |
| 2 | `ODP-INTAKE-FCL-INTEGRATION-001` | Claude | All Wave 1 tasks | Mount, integrate, and prove complete application flows |
| 3 | `ODP-INTAKE-FCL-ACCEPTANCE-001` | Codex2 | Integration | Independent requirement-by-requirement acceptance |

## 4. Task Contracts

### ODP-INTAKE-FCL-RUNTIME-001 - Canonical runtime and persisted effects

Owned paths:

- `modules/opsboard/application/network_listings.py`
- `modules/external_data/application/assisted_intake.py`
- `apps/api/app/routes/operator_modules/network_listings.py`
- `apps/api/app/routes/listings.py`
- `packages/openapi-client/src/index.ts`
- focused API/domain tests
- `docs/evidence/completion/ODP-INTAKE-FCL-RUNTIME-001/`

Required outcomes:

1. production UI commands use the canonical intake contract, not a legacy
   fixture facade;
2. approved retrieval is queued and persists every legal stage transition,
   attempt, checkpoint, timeout, failure, cancellation, and retry;
3. exact source identity returns an existing Listing target and a navigation
   receipt;
4. `REVISION` appends ListingRevision and never overwrites historical state;
5. duplicate and possible-match decisions create authoritative identity edges;
6. merge, split, unmerge, reversal, quarantine release, and reject return
   authoritative receipts and persisted readback;
7. all six role modes receive contract-correct allowed actions and masking;
8. audit events include before/after, reason, snapshot/parser, related IDs,
   correlation, version, and evidence state.

Required proof:

- API integration tests for all six core flows;
- database/repository readback for ListingRevision and identity edges;
- queue worker test proving non-synthetic stage history;
- generated client compatibility;
- no `fixture_replay` in the production command path.

### ODP-INTAKE-FCL-SHELL-001 - Durable routes and detail composition

Owned paths:

- `apps/web/src/app/w/expansion/listings/intake/[intakeId]/`
- `apps/web/src/app/intake/[intakeId]/page.tsx`
- `apps/web/features/operator/network/intake/AssistedIntakeSection.tsx`
- `apps/web/features/operator/network/intake/IntakeProcessingDetail.tsx`
- `apps/web/features/operator/network/intake/IntakeDetailDialog.tsx`
- `apps/web/features/operator/network/intake/IntakeDialogShell.tsx`
- `apps/web/features/operator/network/intake/urlState.ts`
- focused route/composition tests
- `docs/evidence/completion/ODP-INTAKE-FCL-SHELL-001/`

Required outcomes:

1. `/w/expansion/listings/intake/:intakeId` is a real page;
2. reload, back, forward, external source open, active section, compare target,
   and task deep link preserve state;
3. wide drawer is preview-only;
4. full compare, correction, identity decision, quarantine release, and
   promotion review run on the durable page;
5. the production page mounts the complete detail, stage, evidence, receipt,
   field lineage, identity, assignment, and promotion composition;
6. high-risk dialogs cannot close from Escape, overlay click, or close button
   while submitting;
7. route-level loading, missing record, denial, conflict, and recovery states
   are implemented.

Required proof:

- direct-open and reload E2E on the exact required route;
- a production import graph showing every required component is reachable;
- no redirect from the durable route back to an Inbox dialog.

### ODP-INTAKE-FCL-ROLES-001 - Complete role-aware experience

Owned paths:

- `apps/web/features/operator/navigation.tsx`
- `apps/web/features/operator/network/intake/intakePermissions.ts`
- operator identity/header mapping used by the intake UI
- focused role tests
- `docs/evidence/completion/ODP-INTAKE-FCL-ROLES-001/`

Required outcomes:

1. Expansion staff, Expansion manager, Data steward, Governance reviewer,
   Privacy officer, and permission-limited modes are selectable and reachable;
2. own/assigned, source/data, governance, purpose-bound, masked, and read-only
   variants are distinct;
3. denied actions expose backend reason codes;
4. ordinary identity and promotion decisions enforce separate proposer and
   reviewer subjects;
5. role switching reloads authoritative permissions without losing the intake
   deep link.

Required proof:

- role/action E2E matrix on the durable detail route;
- self-review denial for identity and promotion;
- masking and purpose-bound evidence tests.

### ODP-INTAKE-FCL-INBOX-001 - Complete Inbox and URL intake

Owned paths:

- `apps/web/features/operator/network/intake/ListingInboxIntakeView.tsx`
- `apps/web/features/operator/network/intake/AddListingFromUrlDialog.tsx`
- `apps/web/features/operator/network/intake/useIntakeInboxQuery.ts`
- Inbox query API adapter not owned by Runtime
- focused tests
- `docs/evidence/completion/ODP-INTAKE-FCL-INBOX-001/`

Required outcomes:

1. server pagination, cursor, stable sort, saved views, selection, and URL
   restoration;
2. filters for method, stage, outcome, source, submitter, owner/assignment,
   review, SLA, observed/updated range, HeatZone/area, restricted data,
   quarantine, failure, and retryability;
3. semantic table columns for every required field;
4. a real geographic map using authoritative coordinates, including a clear
   unlocated state;
5. direct open, claim, review, retry, and correction actions;
6. Add URL shows original/canonical URL, source, HeatZone, submitter,
   tenant/scope/owner, policy expectation, request lock, and durable receipt;
7. exact duplicate opens the existing Listing.

Required proof:

- server request assertions for every filter and cursor;
- list/map URL restoration E2E;
- semantic table accessibility assertions;
- exact-duplicate existing-Listing navigation E2E.

### ODP-INTAKE-FCL-REVIEW-001 - Field lineage and durable assisted entry

Owned paths:

- `apps/web/features/operator/network/intake/AssistedEntryForm.tsx`
- `apps/web/features/operator/network/intake/ParsedDataReview.tsx`
- `apps/web/features/operator/network/intake/FieldLineageRow.tsx`
- `apps/web/features/operator/network/intake/useCorrectionDraft.ts`
- `apps/web/features/operator/network/intake/IntakeFieldFixDialog.tsx`
- focused tests
- `docs/evidence/completion/ODP-INTAKE-FCL-REVIEW-001/`

Required outcomes:

1. Identity, Location, Commercial, Property, and Provenance groups;
2. parsed, normalized, corrected, and effective values in every row;
3. missing, low-confidence, masked, correction actor, reason, time, snapshot,
   parser run, and supersession lineage;
4. reason, risk acknowledgement, and independent review for material changes;
5. assisted-entry drafts survive close, reload, network failure, conflict, and
   retry without becoming authoritative until submitted;
6. `ASSISTED_ENTRY_ONLY` never triggers retrieval or requests credentials.

Required proof:

- reload/conflict draft-preservation E2E;
- server readback of corrected/effective values and lineage;
- screen-reader change summary test.

### ODP-INTAKE-FCL-IDENTITY-001 - Full compare and reversible identity decisions

Owned paths:

- `apps/web/features/operator/network/intake/ListingCompareTable.tsx`
- `apps/web/features/operator/network/intake/MatchEvidencePanel.tsx`
- `apps/web/features/operator/network/intake/IdentityDecisionPanel.tsx`
- new identity graph plan/receipt components
- focused tests
- `docs/evidence/completion/ODP-INTAKE-FCL-IDENTITY-001/`

Required outcomes:

1. NEW, EXACT_DUPLICATE, REVISION, POSSIBLE_MATCH, and QUARANTINED are distinct;
2. current and submitted source ID, canonical URL, address, area, floor, type,
   rent/price, status, confidence, and contradictions are shown side by side;
3. create, append revision, duplicate, steward, reject, and quarantine actions
   are explicit;
4. merge, split, unmerge, and reversal show graph plan, lineage impact,
   before/after, proposer, reviewer, reason, risk, conflict, and receipt;
5. POSSIBLE_MATCH never auto-merges and identity self-review is denied;
6. mobile presents a durable desktop-required state without losing drafts.

Required proof:

- persisted identity-edge readback E2E;
- ListingRevision readback E2E;
- independent-review conflict and reversal E2E;
- no production caller falls back to the old signal-only `MatchReview`.

### ODP-INTAKE-FCL-EVIDENCE-001 - Authoritative receipt, audit, and recovery

Owned paths:

- `apps/web/features/operator/network/intake/DurableReceiptPanel.tsx`
- `apps/web/features/operator/network/intake/EvidencePanel.tsx`
- `apps/web/features/operator/network/intake/IntakeErrorRecovery.tsx`
- new structured audit timeline component
- focused tests
- `docs/evidence/completion/ODP-INTAKE-FCL-EVIDENCE-001/`

Required outcomes:

1. remove every fallback or fabricated ID, hash, verification, WORM, Listing,
   Candidate, submission, assignment, correction, decision, and audit value;
2. hide unavailable receipts instead of inventing them;
3. render server-issued submission, assignment, SLA, correction, decision,
   identity, promotion, job, evidence, and export receipts;
4. render structured actor, role, time, action, reason, before/after,
   snapshot/parser, related IDs, correlation, version, and evidence state;
5. render purpose binding, classification, expiry, masking, legal-hold/export
   result, and verification from authoritative responses;
6. every error shows summary, exact code, correlation, occurred time,
   retryability, state/version, operation, server value, preserved input, and
   next action.

Required proof:

- test that no receipt element renders without a server field;
- checksum/signature verification fixture from an API response, not UI
  generation;
- E2E for every named error family in requirement section 10.

### ODP-INTAKE-FCL-LIFECYCLE-001 - Live lifecycle, assignment, and job controls

Owned paths:

- `apps/web/features/operator/network/intake/IntakeStageTimeline.tsx`
- `apps/web/features/operator/network/intake/AssignmentSlaSummary.tsx`
- `apps/web/features/operator/network/intake/TransferIntakeDialog.tsx`
- `apps/web/features/operator/network/intake/PauseSlaDialog.tsx`
- `apps/web/features/operator/network/intake/PromotionReviewPanel.tsx`
- `apps/web/features/operator/network/intake/SiteScoreJobStatus.tsx`
- new lifecycle polling/subscription hook
- focused tests
- `docs/evidence/completion/ODP-INTAKE-FCL-LIFECYCLE-001/`

Required outcomes:

1. persisted intake, assignment, SLA, decision, promotion, and job histories;
2. automatic polling or subscription with backoff and visibility handling;
3. attempt, timeout, checkpoint, next retry, cancellation, DLQ, and replay;
4. controlled reopen for failed/quarantined and terminal cancelled behavior;
5. owner, queue, assigned/claimed time, due time, pause/transfer/escalation
   history and direct actions;
6. Candidate remains visible after `SCORE_FAILED`;
7. no fabricated progress or optimistic high-risk transition.

Required proof:

- browser observation of server-driven state transitions without reload;
- cancel, retry, DLQ, escalation, pause, transfer, and replay E2E;
- authoritative history readback.

### ODP-INTAKE-FCL-INTEGRATION-001 - Production composition and complete E2E

Owned paths after Wave 1 is terminal:

- integration changes across intake composition files;
- `tests/e2e/operator-assisted-listing-intake.spec.ts`
- new full-spec Playwright specifications;
- `docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/`

Required outcomes:

1. merge and mount every Wave 1 slice on the durable route;
2. remove dead alternate detail, compare, evidence, and recovery paths;
3. pass all six canonical flows against the real API and persisted state;
4. pass six role modes and every required named error family;
5. pass desktop, tablet, and mobile capabilities;
6. pass semantic table, keyboard, focus, live region, contrast, reduced motion,
   wrapping, and no-overflow checks;
7. produce a requirement-to-test-to-evidence matrix for every section 4 audit
   row.

Required proof:

- clean build, typecheck, unit/integration suites, full Playwright, axe;
- screenshots and API/database readback at 390, 1024, and 1440 px;
- zero fixture-only production paths;
- zero fabricated receipts;
- zero required unmounted components.

### ODP-INTAKE-FCL-ACCEPTANCE-001 - Independent closure audit

Owned paths:

- tests and read-only inspection;
- `docs/evidence/completion/ODP-INTAKE-FCL-ACCEPTANCE-001/`
- final update to the functional audit disposition.

Required outcomes:

1. independently re-run every command and E2E scenario;
2. inspect production import graph and routes;
3. inspect persisted ListingRevision, identity edge, stage history, receipts,
   and audit readbacks;
4. verify every requirement trace row is `PASS`;
5. reject closure when evidence is indirect, missing, fixture-only, or limited
   to component tests.

The umbrella closes only when the independent reviewer records
`FUNCTIONALLY_COMPLETE` against the exact integrated commit.

## 5. Dispatch and Merge Policy

- Every Fleet starts from the pushed functional-closure baseline.
- Wave 1 tasks use isolated worktrees and disjoint owned paths.
- A worker must commit and push its task branch before requesting review.
- Wave 2 starts only after all Wave 1 commits are integrated.
- Wave 3 must be performed by a Fleet that did not own the integration task.
- No previous `done` status is inherited.
- No task is terminal until its code is integrated into the functional-closure
  branch and its acceptance evidence still passes there.

