# Package 10 Program Ledger Recovery Task

- `task_id`: `ODP-P10-PROGRAM-RECOVERY-001`
- `status`: `ready_for_pickup`
- `owner`: `program-audit`
- `worktree`: `/home/lupin/oday-plus-package10-final`
- `branch`: `fix/package10-final-20260725`
- `head_before_dispatch`: `4bf4755b`
- `execution_mode`: documentation and audit only

## Objective

Rebuild the Package 10 canonical execution source of truth after the
2026-07-26 environment restart. Produce a current design-to-runtime audit and
sequential Fleet task ledger before any further product implementation.

The output must prevent another LLM from:

- treating Package 6/7 or OpsBoard pages as the current visual source;
- editing an orphan intake component instead of the production composition;
- weakening a canonical E2E assertion to hide a product gap;
- reporting route retirement as complete visual retirement;
- reading or writing the dirty main worktree;
- leaving completed Fleet work only in a temporary worktree.

## Read Before Work

1. `docs/evidence/fleet_dispatch/package10_20260726/FLEET_HEALTH_SMOKE_TASK.md`
2. `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-FLEET-SMOKE-001.json`
3. `docs/design/ODAY_PLUS_CLAUDE_DESIGN_MASTER_BRIEF.md`
4. `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_DESIGN_REQUIREMENTS.md`
5. `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/manifest.json`
6. `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/Oday Plus Operator Console.dc.html`
7. `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html`
8. `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE.md`
9. Current runtime, route, navigation, Playwright, and intake files named below.

## Canonical Source

- Package: `10`
- ZIP SHA-256:
  `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8`
- HTML SHA-256:
  `cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`
- Canonical product routes after retirement:
  `/operator`, `/intake/[intakeId]`, `/franchisee`
- Correct worktree:
  `/home/lupin/oday-plus-package10-final`
- Dirty main worktree:
  `/home/lupin/oday-plus`; read-only for this program and never closure evidence

## Required Audit

### A. Page and route inventory

Compare every executable `apps/web/src/app/**` page with the Package 10 HTML.
Classify each as:

- `canonical_keep`
- `redirect_only`
- `delete_legacy_runtime`
- `nonvisual_infrastructure`

The old OpsBoard page trees under the following paths are expected retirement
targets, not Package 10 product pages:

`adlift`, `admin`, `audit`, `avm`, `expansion`, `interventions`, `learning`,
`map`, `netplan`, `notifications`, `operations`, `pricing`, `search`,
`settings`, `tasks`, and `w/**`.

### B. Feature and shell inventory

Audit old feature roots:

`adlift`, `audit`, `avm`, `expansion`, `intervention`, `learninghub`, `map`,
`netplan`, `operations`, and `priceops`.

Audit `OpsBoardFrame.tsx`, `networkFindAreasLoader.ts`, old shell workspaces,
shared navigation hrefs, `next.config.mjs`, middleware, not-found/error links,
and imports from current Package 10 code. Map code may move into
`features/operator/network/**`; it must not remain as an alternate old page.

### C. Intake internal visual inventory

Trace the production import graph from:

`OperatorConsole -> NetworkFindAreasWorkspace -> AssistedIntakeSection`.

Compare these files with the Package 10 intake detail in the canonical HTML:

- `IntakeProcessingDetail.tsx`
- `EvidencePanel.tsx`
- `IntakeDetailDialog.tsx`
- `AssistedIntakeQueuePanel.tsx`
- `IdentityDecisionPanel.tsx`
- `ListingCompareTable.tsx`
- `MatchEvidencePanel.tsx`
- `IntakeAssignmentSlaDialog.tsx`
- `ListingInboxIntakeView.tsx`
- `AssistedIntakeSection.tsx`
- `intake.module.css`
- `intakeTypes.ts`
- their unit tests

The audit must verify these already observed facts:

1. Package 10 VDR-003 and P0 acceptance item 6 require a 390px
   `POSSIBLE_MATCH` inline `DESKTOP_REQUIRED` state; tablet retains full
   functionality.
2. Package 10 VDR-005 requires a durable detail route/state.
3. Package 10 VDR-009 says the package must not contain two UIs.
4. The canonical HTML detail is a full-page layer below the top navigation,
   with a 1160px content maximum and continuous sections. It is not the current
   Timeline/Evidence/Receipts/Error tabbed modal.
5. The production `IntakeProcessingDetail` has no active side-by-side match
   review or mobile `DESKTOP_REQUIRED` output.
6. Compare output currently exists only in the non-production
   `IdentityDecisionPanel -> ListingCompareTable -> MatchEvidencePanel` tree.
7. `IntakeDetailDialog` is not the production detail, but the production file
   imports `AssistedEntryForm` from it and unit tests still mount its old main
   component.
8. `AssistedIntakeQueuePanel` is orphaned and explicitly describes a Package 7
   layout.
9. `IntakeAssignmentSlaDialog` is exported but has no production caller.
10. `Package10VisualP1.test.tsx` directly tests the orphan compare component,
    so its filename is not proof of production Package 10 coverage.

If any observation is wrong in the current branch, record exact contrary
evidence. Do not silently adopt the observation.

### D. E2E inventory

Inventory every `tests/e2e/*.spec.ts`. Separate:

- the 16 expected Package 10 canonical specs;
- the 18 known legacy visual specs to delete;
- any additional or missing specs.

Do not call a missing compare or `DESKTOP_REQUIRED` assertion stale merely
because the current product omitted the required UI.

### E. Other LLM conflict audit

Record every assignment or output that conflicts with the current objective.
At minimum:

- the smoke ACK proves Fleet health, but its `next` field is stale because it
  says to resume CAN-003 before the newly discovered CAN-001-R3 product gap;
- any claim based on `/home/lupin/oday-plus` is wrong-worktree evidence;
- route-level deletion does not prove internal intake visual retirement;
- a test that directly mounts an orphan component is not runtime evidence.

## Required Outputs

Create:

1. `docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md`
2. `docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.json`
3. `docs/design/PACKAGE_10_INTAKE_DETAIL_CANONICALIZATION_EXECUTION_ADDENDUM_2026-07-26.md`
4. `docs/design/PACKAGE_10_INTAKE_DETAIL_CANONICALIZATION_EXECUTION_ADDENDUM_2026-07-26.json`
5. `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md`
6. `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.json`
7. `docs/evidence/fleet_dispatch/PACKAGE_10_CANONICAL_RUNTIME_FLEET_DISPATCH_2026-07-26.md`
8. `docs/evidence/fleet_dispatch/PACKAGE_10_CANONICAL_RUNTIME_FLEET_DISPATCH_2026-07-26.json`
9. `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-CONFLICT-VISUAL-AUDIT.md`
10. `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-CONFLICT-VISUAL-AUDIT.json`
11. `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-PROGRAM-RECOVERY-001.json`

## Required Execution Sequence

The ledger must define sequential ownership transfer:

1. `ODP-P10-CAN-001-R3`
   Canonical runtime cleanup, intake detail integration, and old visual
   retirement.
2. `ODP-P10-CAN-002-R3`
   API/security contract re-verification. Product permissions remain
   fail-closed.
3. `ODP-P10-CAN-003-R3A`
   Canonical E2E assertion and responsive coverage alignment after product
   implementation.
4. `ODP-P10-CAN-003-R3B`
   Read-only complete Chromium gate, expected count declared from the current
   canonical spec inventory.
5. `ODP-P10-CAN-004-R3`
   Release gate, evidence reconciliation, integration, and deployment
   readiness.

No later task may start from a chat claim. It must read the committed task
documents and prior ACK.

## CAN-001-R3 Minimum Contract

The task must:

- retain only the Package 10 executable routes;
- convert old URLs to redirects without keeping old pages/components;
- retire old feature roots, shell workspaces, frame, loader, old navigation,
  and old visual E2E specs;
- produce one production intake detail composition;
- implement the Package 10 continuous full-page detail;
- include source policy, parsed/normalized/corrected values, match confidence,
  agreeing and contradictory signals, desktop side-by-side comparison,
  mobile inline `DESKTOP_REQUIRED`, assignment/SLA, WORM/receipts, promotion,
  human decisions, timeline, and durable return/deep link;
- migrate reusable form/helper logic out of an old component before deleting
  that component;
- delete or migrate tests that only mount retired visual components;
- remove active Package 6/7 visual-baseline wording;
- preserve API clients, fail-closed auth, and shared domain APIs;
- forbid E2E edits during product implementation;
- run unit, typecheck, build, accessibility, import/orphan, and route gates;
- update only its own ACK.

## Persistence Rule

Every completed wave must:

1. pass `git diff --check`;
2. commit its allowed changes on `fix/package10-final-20260725`;
3. push the exact commit to `origin/fix/package10-final-20260725`;
4. record the commit SHA in its ACK.

Uncommitted work in `/tmp` is never accepted as delivery evidence.

## Writable Paths

- the eleven required output files listed above

## Forbidden Paths

- `apps/**`
- `packages/**`
- `tests/**`
- `scripts/**`
- `docs_archive/**`
- all pre-existing task or evidence documents not named in Required Outputs
- the smoke task and smoke ACK

## Exit Criteria

- all eleven outputs exist;
- MD and JSON agree on current status, task order, paths, gates, and blockers;
- JSON parses successfully;
- the page-by-page audit includes every executable page;
- delete/migrate paths are exact;
- conflicts name their source and resolution;
- the current state is `no_go_pending_CAN001_R3`;
- no product or test file changed;
- `git diff --check` passes.
