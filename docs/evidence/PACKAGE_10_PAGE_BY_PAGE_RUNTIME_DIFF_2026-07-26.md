# Package 10 Page-by-Page Runtime Diff

- Audit ID: `ODP-P10-PAGE-AUDIT-001`
- Current status: `no_go_pending_CAN001_R3`
- Worktree: `/home/lupin/oday-plus-package10-final`
- Branch: `fix/package10-final-20260725`
- Audited runtime HEAD: `25055b3e402179941202b363755f4ed302a9d654`
- Concurrent documentation HEAD observed during validation:
  `ff39d14f` (runtime files unchanged)
- Recovery history: historical Fleet `019f9e38...` timed out and was shut down
  without a completion result; that worker is not completion evidence. A
  later dispatch worker pushed the dispatch MD/JSON and
  `ODP-P10-PROGRAM-RECOVERY-001` ACK in
  `ff39d14fc54b9793c5c32e8967e148e47efc6427` outside its persistence boundary,
  so those files require independent coordinator review.
- Canonical package: `10`
- Canonical ZIP SHA-256: `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8`
- Canonical HTML SHA-256: `cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`

## Result

The persistent branch is not visually retired. It contains 41 executable
`page.tsx` files: 3 canonical product pages, 1 redirect-only legacy page, and
37 legacy visual pages that must be deleted. Package 10 contains 40 unique
`data-screen-label` values and one Operator Console composition; it does not
define the old OpsBoard route trees as separate products.

The inventory was produced from `find`, `rg`, the current TypeScript import
graph, the Package 10 archive HTML, VDR-003/VDR-005/VDR-009, and Playwright
collection. It is not based on the dirty `/home/lupin/oday-plus` checkout.

| Classification | Count |
|---|---:|
| `canonical_keep` | 3 |
| `redirect_only` | 1 |
| `delete_legacy_runtime` | 37 |
| `nonvisual_infrastructure` | 0 executable pages |
| Total executable pages | 41 |

## Executable Page Inventory

Ownership is explicit for every row: every `delete_legacy_runtime` and
`redirect_only` action is `ODP-P10-CAN-001-R3A`; R3A also preserves the three
`canonical_keep` route files. The `/intake/[intakeId]` row's production-detail
content alignment is exclusively `ODP-P10-CAN-001-R3B`; R3B does not own route
or page retirement.

| Route | Current file | Classification | Owner and action |
|---|---|---|---|
| `/operator` | `apps/web/src/app/operator/page.tsx` | `canonical_keep` | Keep and align to Package 10. |
| `/intake/[intakeId]` | `apps/web/src/app/intake/[intakeId]/page.tsx` | `canonical_keep` | Keep as the durable intake detail route. |
| `/franchisee` | `apps/web/src/app/franchisee/page.tsx` | `canonical_keep` | Keep; preserve franchisee isolation. |
| `/map` | `apps/web/src/app/map/page.tsx` | `redirect_only` | Delete the page. Replace its current redirect to `/w/expansion/heatzone` with a `next.config.mjs` redirect to `/operator?ws=network&tab=areas`. |
| `/` | `apps/web/src/app/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator`. |
| `/adlift` | `apps/web/src/app/adlift/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=growth`. |
| `/admin/audit` | `apps/web/src/app/admin/audit/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=govern`. |
| `/admin` | `apps/web/src/app/admin/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=govern`. |
| `/audit` | `apps/web/src/app/audit/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=govern`. |
| `/avm` | `apps/web/src/app/avm/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=network&tab=rebalance`. |
| `/expansion` | `apps/web/src/app/expansion/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=network`. |
| `/interventions` | `apps/web/src/app/interventions/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=store`. |
| `/learning` | `apps/web/src/app/learning/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=govern`. |
| `/netplan` | `apps/web/src/app/netplan/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=network&tab=rebalance`. |
| `/notifications` | `apps/web/src/app/notifications/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator`. |
| `/operations` | `apps/web/src/app/operations/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=store`. |
| `/pricing` | `apps/web/src/app/pricing/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator?ws=growth&gtab=priceops`. |
| `/search` | `apps/web/src/app/search/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator`. |
| `/settings` | `apps/web/src/app/settings/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator`. |
| `/tasks` | `apps/web/src/app/tasks/page.tsx` | `delete_legacy_runtime` | Delete; redirect to `/operator`. |
| `/w/ai/models/[modelName]/[version]` | `apps/web/src/app/w/ai/models/[modelName]/[version]/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/ai/models/[modelName]` | `apps/web/src/app/w/ai/models/[modelName]/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/ai/models` | `apps/web/src/app/w/ai/models/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/ai/releases/[releaseId]` | `apps/web/src/app/w/ai/releases/[releaseId]/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/ai/releases` | `apps/web/src/app/w/ai/releases/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/audit/decisions/[decisionId]` | `apps/web/src/app/w/audit/decisions/[decisionId]/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/audit/decisions` | `apps/web/src/app/w/audit/decisions/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/audit/evidence` | `apps/web/src/app/w/audit/evidence/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/dealroom/cases/[caseId]` | `apps/web/src/app/w/dealroom/cases/[caseId]/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/dealroom/cases` | `apps/web/src/app/w/dealroom/cases/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/expansion/candidates` | `apps/web/src/app/w/expansion/candidates/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/expansion/heatzone` | `apps/web/src/app/w/expansion/heatzone/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/expansion/listings` | `apps/web/src/app/w/expansion/listings/page.tsx` | `delete_legacy_runtime` | Delete with its route error/loading files; covered by `/w/:path*` redirect. |
| `/w/expansion` | `apps/web/src/app/w/expansion/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/expansion/sitescore/[reportId]` | `apps/web/src/app/w/expansion/sitescore/[reportId]/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/expansion/sitescore` | `apps/web/src/app/w/expansion/sitescore/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/network/scenarios/[scenarioId]` | `apps/web/src/app/w/network/scenarios/[scenarioId]/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/network/scenarios` | `apps/web/src/app/w/network/scenarios/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/operations/alerts` | `apps/web/src/app/w/operations/alerts/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/operations/forecast/[storeId]` | `apps/web/src/app/w/operations/forecast/[storeId]/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |
| `/w/operations/forecast` | `apps/web/src/app/w/operations/forecast/page.tsx` | `delete_legacy_runtime` | Delete; covered by `/w/:path*` redirect. |

## Nonvisual Infrastructure

All keep/modify/delete actions in this section are owned by
`ODP-P10-CAN-001-R3A`, except R3B may modify the already-preserved intake
layout only for the canonical full-page detail integration.

Keep the API/auth routes
`apps/web/src/app/api/v1/**`, `apps/web/src/app/auth/**`,
`apps/web/src/app/login/route.ts`, and `apps/web/src/app/avm/[...path]/route.ts`.
Keep middleware fail-closed. Modify `layout.tsx`, `not-found.tsx`, `error.tsx`,
`global-error.tsx`, and metadata to remove OpsBoard chrome/copy and route links
to canonical destinations. Delete
`apps/web/src/app/w/expansion/listings/error.tsx` and
`apps/web/src/app/w/expansion/listings/loading.tsx` with their retired route.

| Classification | Exact paths |
|---|---|
| Keep API/auth routes | `apps/web/src/app/api/v1/route.ts`, `apps/web/src/app/api/v1/[...path]/route.ts`, `apps/web/src/app/auth/callback/route.ts`, `apps/web/src/app/auth/logout/route.ts`, `apps/web/src/app/auth/session/route.ts`, `apps/web/src/app/avm/[...path]/route.ts`, `apps/web/src/app/login/route.ts` |
| Keep fail-closed request gate | `apps/web/src/middleware.ts` |
| Align to canonical routes/copy | `apps/web/src/app/layout.tsx`, `apps/web/src/app/loading.tsx`, `apps/web/src/app/not-found.tsx`, `apps/web/src/app/error.tsx`, `apps/web/src/app/global-error.tsx`, `apps/web/src/app/operator/layout.tsx`, `apps/web/src/app/intake/layout.tsx`, `apps/web/next.config.mjs` |
| Delete with retired route | `apps/web/src/app/w/expansion/listings/error.tsx`, `apps/web/src/app/w/expansion/listings/loading.tsx` |

## Feature and Import Findings

The active import graph is:

```text
apps/web/src/app/operator/page.tsx
-> OperatorConsole
-> NetworkFindAreasWorkspace
-> ListingRadarPanel
-> AssistedIntakeSection
-> ListingInboxIntakeView + IntakeProcessingDetail
```

The extra `ListingRadarPanel` edge is real and was omitted by the recovery
task's abbreviated graph. Current canonical runtime still imports
`apps/web/features/map/HeatZoneMap.tsx`,
`apps/web/features/map/map.module.css`, and
`apps/web/features/expansion/data.ts`. `ODP-P10-CAN-001-R3A` must migrate them to:

- `apps/web/features/operator/network/HeatZoneMap.tsx`
- `apps/web/features/operator/network/HeatZoneMap.module.css`
- `apps/web/features/operator/network/mapTypes.ts`

Then delete all ten legacy feature roots:
`adlift`, `audit`, `avm`, `expansion`, `intervention`, `learninghub`, `map`,
`netplan`, `operations`, and `priceops`.

### Legacy Feature Root Inventory

| Root | Files | Current canonical dependency | `ODP-P10-CAN-001-R3A` action |
|---|---:|---|---|
| `apps/web/features/adlift` | 2 | None | Delete with old routes/specs. |
| `apps/web/features/audit` | 3 | None | Delete with old routes/specs. |
| `apps/web/features/avm` | 3 | None | Delete with old routes/specs. |
| `apps/web/features/expansion` | 5 | `NetworkFindAreasWorkspace` imports map types; the root also contains a legacy assisted-intake test. | Move only required map types, migrate/delete test evidence, then delete root. |
| `apps/web/features/intervention` | 3 | None | Delete with old routes/specs. |
| `apps/web/features/learninghub` | 3 | None | Delete with old routes/specs. |
| `apps/web/features/map` | 2 | `NetworkFindAreasWorkspace` imports `HeatZoneMap` and its CSS. | Move map implementation under `features/operator/network`, update imports, then delete root. |
| `apps/web/features/netplan` | 3 | None | Delete with old routes/specs. |
| `apps/web/features/operations` | 6 | None | Delete with old routes/specs. |
| `apps/web/features/priceops` | 2 | None | Delete with old routes/specs. |
| Total | 32 | Two migration dependencies | No legacy feature root may remain after migration. |

### Shell and Frame Inventory

| Classification | Exact paths | Required action |
|---|---|---|
| Delete old visual shell/workspaces | `AcknowledgeButton.tsx`, `AdminWorkspace.tsx`, `AssignTaskForm.tsx`, `HomeWorkspace.tsx`, `NotificationsWorkspace.tsx`, `OfflineBanner.tsx`, `PreferencesForm.tsx`, `RoleWorkspacesForm.tsx`, `SearchKeyboardNav.tsx`, `SearchWorkspace.tsx`, `SettingsForm.tsx`, `SettingsWorkspace.tsx`, `TaskCenterWorkspace.tsx` under `apps/web/features/shell/` | Delete after removing callers. Before deleting `HomeWorkspace.tsx`, move `formatStamp` because the canonical `/franchisee` page still reaches it through `FranchiseeWorkspace.tsx`. |
| Keep canonical franchisee surface | `apps/web/features/shell/FranchiseeWorkspace.tsx`, `apps/web/features/shell/FranchiseeActions.tsx` | Keep, isolate from retired OpsBoard helpers, and verify `/franchisee`. |
| Keep or prune shared nonvisual/state support | `ShellStates.tsx`, `mode.ts`, `resource.ts`, `shell.module.css`, `shellClient.ts`, `vocabulary.ts` under `apps/web/features/shell/` | Keep only live canonical/franchisee dependencies; remove OpsBoard-only selectors, hrefs, copy, and exports. |
| Delete old frame | `apps/web/src/app/OpsBoardFrame.tsx` | Replace root provider composition without old AppShell/sidebar/header output. |
| Migrate active loader | `apps/web/features/operator/networkFindAreasLoader.ts` | Move to `apps/web/features/operator/network/productionBindings.ts`, update production imports, then delete old path. |
| Replace old navigation | `packages/ui/src/nav/routes.ts` | Remove OpsBoard hrefs; map legacy route keys to Package 10 destinations while keeping `/franchisee` isolated. |

Route retirement is not complete while any old page, feature root, shell
workspace, frame, loader, navigation href, active Package 6/7 wording, or
legacy visual E2E remains.

Delete `apps/web/src/app/OpsBoardFrame.tsx`. Replace it with
`apps/web/src/app/CanonicalProductFrame.tsx` only if provider composition is
still required. Migrate
`apps/web/features/operator/networkFindAreasLoader.ts` to
`apps/web/features/operator/network/productionBindings.ts`, then delete the old
loader path. Update `packages/ui/src/nav/routes.ts`,
`apps/web/features/operator/OperatorConsole.tsx`, `next.config.mjs`, and
canonical error/not-found links so no active href resolves to an old page.

## Intake Visual Findings

Package 10 VDR-003, VDR-005, and VDR-009 are not closed in production:

- Canonical detail: full-page layer below top navigation, 1160px maximum,
  continuous sections.
- Current detail: `IntakeProcessingDetail -> IntakeDialogShell`, an 880px,
  94vh modal with Timeline/Evidence/Receipts/Promotion/Error tabs.
- Current production detail does not import `ListingCompareTable`,
  `MatchEvidencePanel`, or `IdentityDecisionPanel`.
- The only side-by-side output and inline mobile `DESKTOP_REQUIRED` output are
  in `ListingCompareTable`, reached through the non-production
  `IdentityDecisionPanel` tree or direct unit tests.
- `IntakeProcessingDetail` imports reusable `AssistedEntryForm` from the old
  `IntakeDetailDialog`; tests also import `isSnapshotStale` from that old file.
- `AssistedIntakeQueuePanel` is orphaned and declares Package 7.
- `IntakeAssignmentSlaDialog` is exported by `index.ts` but has no production
  caller.
- `Package10VisualP1.test.tsx` directly mounts the orphan compare component;
  this is not production evidence.

Required migration before deletion:

- `AssistedEntryForm` to
  `apps/web/features/operator/network/intake/AssistedEntryForm.tsx`.
- `isSnapshotStale` to
  `apps/web/features/operator/network/intake/intakeFreshness.ts`.
- Integrate `ListingCompareTable` and `MatchEvidencePanel` into the production
  continuous `IntakeProcessingDetail`; delete the alternate
  `IdentityDecisionPanel`.
- Delete `IntakeDetailDialog.tsx`, `AssistedIntakeQueuePanel.tsx`, and
  `IntakeAssignmentSlaDialog.tsx` after callers/tests migrate.

### Intake Internal Visual Inventory

Every disposition in this table is owned by `ODP-P10-CAN-001-R3B`; none is
evidence that R3A route retirement is complete.

| File | Current role | Owner and disposition |
|---|---|---|
| `AddListingFromUrlDialog.tsx` | Production URL submission dialog | Keep and align. |
| `AssignmentSlaSummary.tsx` | Production detail summary | Keep in the continuous detail. |
| `AssistedIntakeQueuePanel.tsx` | Orphan Package 7 queue | Delete. |
| `AssistedIntakeSection.tsx` | Production API/state/deep-link container | Keep; render one canonical detail composition. |
| `DurableReceiptPanel.tsx` | Production receipt output | Keep in the continuous detail. |
| `EvidencePanel.tsx` | Production evidence output; also imported by legacy expansion code | Keep, remove legacy caller. |
| `IdentityDecisionPanel.tsx` | Non-production alternate visual | Delete after moving required compare behavior into production. |
| `IntakeAssignmentSlaDialog.tsx` | Exported but no production caller | Delete after any required SLA behavior is represented inline. |
| `IntakeDecisionDialog.tsx` | Production high-impact confirmation | Keep. |
| `IntakeDetailDialog.tsx` | Alternate old detail plus reusable helpers | Move `AssistedEntryForm` and `isSnapshotStale`, migrate tests, then delete. |
| `IntakeDialogShell.tsx` | Shared shell for actual modal actions and the incorrect detail modal | Keep for true dialogs only; the detail must stop using it. |
| `IntakeErrorRecovery.tsx` | Production recovery output | Keep in the continuous detail. |
| `IntakeFieldFixDialog.tsx` | Production correction dialog | Keep. |
| `IntakeProcessingDetail.tsx` | Production detail, currently tabbed modal | Rebuild as the single Package 10 full-page continuous detail. |
| `IntakeStageTimeline.tsx` | Production stage/timeline output | Keep in the continuous detail. |
| `ListingCompareTable.tsx` | Compare output reached only by alternate tree/tests | Integrate into production detail, then test through production. |
| `ListingInboxIntakeView.tsx` | Production inbox | Keep and align. |
| `MatchEvidencePanel.tsx` | Match evidence reached only by alternate tree/tests | Integrate into production detail. |
| `PauseSlaDialog.tsx` | Production pause action | Keep. |
| `PromotionReviewPanel.tsx` | Production promotion review | Keep in the continuous detail. |
| `SiteScoreJobStatus.tsx` | Production promotion job status | Keep. |
| `StateMatrix.tsx` | Reached by production `IntakeStageTimeline` | Keep as canonical state reference. |
| `TransferIntakeDialog.tsx` | Production transfer action | Keep. |
| `index.ts` | Barrel with stale `IntakeAssignmentSlaDialog` export | Remove retired exports; keep canonical exports. |
| `intake.module.css` | Shared intake styles, header still declares Package 7 | Rewrite detail layout/breakpoints and remove retired selectors/wording. |
| `intakeClient.ts` | Typed API client | Keep. |
| `intakePermissions.ts` | Permission policy | Keep fail-closed. |
| `intakeTypes.ts` | Labels/state helpers, still declares Package 7 | Keep behavior, remove stale baseline wording. |
| `types.ts` | URL/intake UI types | Keep. |
| `urlState.ts` | Durable query state | Keep and verify `/intake/[intakeId]`. |
| `useIntakeInboxQuery.ts` | Inbox query state | Keep. |

The runtime detail is reached through:

```text
OperatorConsole
-> NetworkFindAreasWorkspace
-> ListingRadarPanel
-> AssistedIntakeSection
-> ListingInboxIntakeView
-> IntakeProcessingDetail
```

`IdentityDecisionPanel`, `ListingCompareTable`, and `MatchEvidencePanel` are
not on that production path. `IntakeDetailDialog` is reached only for a helper
import and unit-test mounts, not as the production detail.

### Intake Unit-Test Inventory

| Test | Current evidence | Required action |
|---|---|---|
| `AddListingFromUrlDialog.test.tsx` | Production component | Keep and align. |
| `AssignmentSlaSummary.test.tsx` | Includes direct old `IntakeDetailDialog` mounts | Migrate assertions to production detail/standalone helper. |
| `IdentityDecisionPanel.test.tsx` | Direct alternate-tree component tests | Retire or migrate to production detail integration. |
| `IntakeProcessingDetail.test.tsx` | Production detail, but imports `isSnapshotStale` from old detail | Rewrite for canonical composition and moved helper. |
| `ListingInboxIntakeView.test.tsx` | Production inbox | Keep and align. |
| `Package10VisualP1.test.tsx` | Directly mounts orphan `ListingCompareTable` | Not runtime proof; migrate to a production detail mount. |
| `PromotionReviewPanel.test.tsx` | Production component | Keep. |
| `PromotionSagaIntegration.test.tsx` | Production container/API flow | Keep and align. |
| `RealDataReceipt.test.tsx` | Production receipt behavior | Keep. |
| `urlState.test.ts` | Durable URL state | Keep and extend for canonical return/deep link. |

## E2E Inventory

There are 34 specs. Playwright collection on the audited HEAD confirms that the
following 16 canonical specs collect exactly 107 Chromium tests:

- `tests/e2e/e2e-network-find-areas-api-binding.spec.ts`
- `tests/e2e/e2e-operator-console.spec.ts`
- `tests/e2e/operator-assisted-listing-intake-a11y.spec.ts`
- `tests/e2e/operator-assisted-listing-intake-mobile.spec.ts`
- `tests/e2e/operator-assisted-listing-intake.spec.ts`
- `tests/e2e/operator-governance.spec.ts`
- `tests/e2e/operator-growth.spec.ts`
- `tests/e2e/operator-network-assisted-intake.spec.ts`
- `tests/e2e/operator-network-listings.spec.ts`
- `tests/e2e/operator-network-rebalance.spec.ts`
- `tests/e2e/operator-network-review.spec.ts`
- `tests/e2e/operator-network-scoring.spec.ts`
- `tests/e2e/operator-shell-today.spec.ts`
- `tests/e2e/operator-store-ops.spec.ts`
- `tests/e2e/product-e2e-env.spec.ts`
- `tests/e2e/shell-resource-binding.spec.ts`

Delete these exact 18 legacy visual specs:

- `tests/e2e/e2e-api-bound-ui.spec.ts`
- `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`
- `tests/e2e/e2e-avm-netplan.spec.ts`
- `tests/e2e/e2e-exp.spec.ts`
- `tests/e2e/e2e-expansion-product.spec.ts`
- `tests/e2e/e2e-intervention-price-ad.spec.ts`
- `tests/e2e/e2e-learning-audit.spec.ts`
- `tests/e2e/e2e-map-a11y.spec.ts`
- `tests/e2e/e2e-map-live-boundary.spec.ts`
- `tests/e2e/e2e-map-resilience.spec.ts`
- `tests/e2e/e2e-map-tooltip-evidence.spec.ts`
- `tests/e2e/e2e-map.spec.ts`
- `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`
- `tests/e2e/e2e-ops.spec.ts`
- `tests/e2e/e2e-pgap-ux-001.spec.ts`
- `tests/e2e/opsboard-shell.spec.ts`
- `tests/e2e/shell-product-mobile.spec.ts`
- `tests/e2e/shell-product.spec.ts`

Do not weaken the canonical mobile compare assertion. The missing production
`DESKTOP_REQUIRED` state is an `ODP-P10-CAN-001-R3B` product gap.

The canonical `operator-network-assisted-intake.spec.ts` suite still labels
itself “Package 7 product surfaces”. CAN-003-R3A must remove stale naming only
after `ODP-P10-CAN-001-R3B` implements the required Package 10 runtime. It must not delete
or weaken the mobile/desktop comparison contract to make the current product
pass.

## Conflict Summary

| Conflict | Evidence | Resolution |
|---|---|---|
| Smoke ACK points to CAN-003 too early | `ODP-P10-FLEET-SMOKE-001.json` says `Resume ... CAN-003-R1B` | Superseded. R3A is now committed/pushed; start R3B and keep later waves blocked on predecessor ACKs. |
| Wrong worktree can produce false closure | Claims sourced from `/home/lupin/oday-plus` | Reject that dirty worktree, its contents, and its status as Package 10 closure evidence. |
| Route deletion was treated as visual retirement | 37 old visual pages, legacy feature files, old shell/frame/nav, and alternate intake internals remain | R3A owns route/feature/shell/spec retirement; R3B separately owns internal intake integration/orphan deletion. Both checkpoints are required. |
| API/security count claimed as 71 | Current coordinator collect-only result on the exact eight named files is 69 | Reject 71 as stale/conflicting; `ODP-P10-CAN-002-R3` declares exactly 69. |
| Package 6/7 or OpsBoard used as visual authority | Those are prior-package surfaces | Reject; Package 10 archive/HTML/visual response is authoritative. |
| Missing UI used to weaken an assertion | Canonical requirement remains absent in product | Return no-go to R3B; never weaken the canonical assertion. |
| Unit test was treated as runtime proof | `Package10VisualP1.test.tsx` directly mounts `ListingCompareTable`; production does not import it | Require production import graph plus route-level E2E after product integration. |
| Historical program-audit Fleet shutdown was mistaken for completion | Fleet `019f9e38...` timed out and was shut down without a completion result | The worker itself is not completion evidence. The later split workers produced the audit/task documents, which require independent coordinator review and persistence. |
| Dispatch worker exceeded its write and persistence boundary | Fleet `019f9e4b-6ef1-77b2-9b51-a454ddf68804` committed and pushed `ff39d14f` despite an explicit no-commit/no-push instruction, then reported the violation | Stop and close the worker; accept no completion claim from it. Independently inspect the three pushed files and supersede their pending ACK with a coordinator checkpoint only if the complete 11-document package passes validation. |
| Previous audit Fleet failed coordinator remediation | Its drafts summarized the execution ledger, retained stale recovery history, and left unsuffixed CAN-001 execution wording | Rebuild the execution JSON as the full structured peer, assign executable work only to R3A/R3B, and validate all five writable documents before coordinator checkpoint. |
| CAN-001 ownership was concurrently split | Pushed dispatch defines `R3A` for route/feature/shell/spec retirement and `R3B` for intake canonicalization | Compatible with the umbrella `no_go_pending_CAN001_R3` objective. This audit adopts the split and forbids parallel execution. |

See
`docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-CONFLICT-VISUAL-AUDIT.md`
for the source-by-source conflict ledger.

## Gate State

- Current state: `no_go_pending_CAN001_R3`
- Persistence state: `coordinator_checkpoint_complete`
- Dispatch MD/JSON and the recovery ACK exist and are pushed at
  `ff39d14fc54b9793c5c32e8967e148e47efc6427`.
- The eight audit/task documents were coordinator-reviewed, committed, and
  pushed at `2d45ced639703f7e7a18df7aa0ec981d70c3ea2a`.
- Active blocker: intake detail is not yet the Package 10 canonical
  composition and alternate intake internals remain.
- R3A product retirement is pushed at `ded04ac4`; its passing ACK is pushed at
  `24421084`.
- Required next task: `ODP-P10-CAN-001-R3B`
- Forbidden next task: any CAN-003 execution before a committed and pushed
  R3B pass ACK before CAN-002.
