# Package 10 Canonical Runtime Execution Tasks

- Program ID: `ODP-P10-CANONICAL-R3`
- Current status: `no_go_pending_CAN001_R3`
- Active wave: `ODP-P10-CAN-001-R3B`
- Next: `ODP-P10-CAN-001-R3B`
- Worktree: `/home/lupin/oday-plus-package10-final`
- Branch: `fix/package10-final-20260725`
- Canonical package: `10`
- Canonical ZIP SHA-256: `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8`
- Canonical HTML SHA-256: `cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`
- Execution mode: sequential ownership transfer
- Persistence state: `coordinator_checkpoint_complete`

## Binding Source Order

Every Fleet must read the following committed artifacts before pickup:

1. `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/manifest.json`
2. `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/Oday Plus Operator Console.dc.html`
3. `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE.md`
4. `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md`
5. `docs/design/PACKAGE_10_INTAKE_DETAIL_CANONICALIZATION_EXECUTION_ADDENDUM_2026-07-26.md`
6. For R3A:
   `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-CAN-001-R3A-ORPHAN-SHELL-ADDENDUM.md`
7. For R3B:
   `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-CAN-001-R3B-LISTING-RADAR-ADDENDUM.md`
8. This ledger and the immediately preceding committed/pushed ACK.

Chat claims, orphan-component tests, and evidence from `/home/lupin/oday-plus`
are not closure evidence.

## Required Order

| Order | Wave | Initial status | Start condition |
|---|---|---|---|
| 1 | `ODP-P10-CAN-001-R3A` | `pass_committed_pushed` | Recovery documents and program recovery ACK coordinator-reviewed, committed, and pushed |
| 2 | `ODP-P10-CAN-001-R3B` | `ready_for_pickup` | R3A pass ACK committed and pushed |
| 3 | `ODP-P10-CAN-002-R3` | `blocked_on_CAN001_R3B` | R3B pass ACK committed and pushed |
| 4 | `ODP-P10-CAN-003-R3A` | `blocked_on_CAN002_R3` | CAN-002 pass ACK committed and pushed |
| 5 | `ODP-P10-CAN-003-R3B` | `blocked_on_CAN003_R3A` | CAN-003-R3A pass ACK committed and pushed |
| 6 | `ODP-P10-CAN-004-R3` | `blocked_on_CAN003_R3B` | CAN-003-R3B 107/107 ACK committed and pushed |

No wave may start from a chat report or skip its predecessor.

## Mandatory Protocol For Every Wave

Before work, read the committed recovery documents, committed dispatch pair,
this ledger, and the immediately preceding committed ACK; inspect current
assignments and outputs for other-LLM conflicts. Before ownership transfer,
run wave gates and `git diff --check`, obtain coordinator review, commit only
authorized paths, push the exact commit to
`origin/fix/package10-final-20260725`, and record the full SHA and pushed ref
in the wave ACK. Transfer is forbidden until the coordinator confirms that
checkpoint. Chat, `/tmp`, dirty `/home/lupin/oday-plus`, and uncommitted work
are not evidence.

## ODP-P10-CAN-001-R3 - Parent Task

- Status: `split_into_sequential_R3A_R3B`
- Child 1: `ODP-P10-CAN-001-R3A` retires old routes, features, shell visuals,
  map ownership, and legacy visual E2E.
- Child 2: `ODP-P10-CAN-001-R3B` creates the single Package 10 intake runtime.

The parent passes only when both child ACKs are committed and pushed in order.

## ODP-P10-CAN-001-R3A - Old Runtime Retirement

Objective: remove the old OpsBoard visual runtime while preserving Package 10
domain/API behavior. This wave does not redesign intake detail.

### Canonical pages to keep

- `apps/web/src/app/operator/page.tsx`
- `apps/web/src/app/intake/[intakeId]/page.tsx`
- `apps/web/src/app/franchisee/page.tsx`

### Page files to delete

Delete these 38 executable page files after redirects exist in
`apps/web/next.config.mjs`:

```text
apps/web/src/app/page.tsx
apps/web/src/app/adlift/page.tsx
apps/web/src/app/admin/page.tsx
apps/web/src/app/admin/audit/page.tsx
apps/web/src/app/audit/page.tsx
apps/web/src/app/avm/page.tsx
apps/web/src/app/expansion/page.tsx
apps/web/src/app/interventions/page.tsx
apps/web/src/app/learning/page.tsx
apps/web/src/app/map/page.tsx
apps/web/src/app/netplan/page.tsx
apps/web/src/app/notifications/page.tsx
apps/web/src/app/operations/page.tsx
apps/web/src/app/pricing/page.tsx
apps/web/src/app/search/page.tsx
apps/web/src/app/settings/page.tsx
apps/web/src/app/tasks/page.tsx
apps/web/src/app/w/ai/models/page.tsx
apps/web/src/app/w/ai/models/[modelName]/page.tsx
apps/web/src/app/w/ai/models/[modelName]/[version]/page.tsx
apps/web/src/app/w/ai/releases/page.tsx
apps/web/src/app/w/ai/releases/[releaseId]/page.tsx
apps/web/src/app/w/audit/decisions/page.tsx
apps/web/src/app/w/audit/decisions/[decisionId]/page.tsx
apps/web/src/app/w/audit/evidence/page.tsx
apps/web/src/app/w/dealroom/cases/page.tsx
apps/web/src/app/w/dealroom/cases/[caseId]/page.tsx
apps/web/src/app/w/expansion/page.tsx
apps/web/src/app/w/expansion/candidates/page.tsx
apps/web/src/app/w/expansion/heatzone/page.tsx
apps/web/src/app/w/expansion/listings/page.tsx
apps/web/src/app/w/expansion/sitescore/page.tsx
apps/web/src/app/w/expansion/sitescore/[reportId]/page.tsx
apps/web/src/app/w/network/scenarios/page.tsx
apps/web/src/app/w/network/scenarios/[scenarioId]/page.tsx
apps/web/src/app/w/operations/alerts/page.tsx
apps/web/src/app/w/operations/forecast/page.tsx
apps/web/src/app/w/operations/forecast/[storeId]/page.tsx
```

Also delete route-local remnants:

```text
apps/web/src/app/w/expansion/listings/error.tsx
apps/web/src/app/w/expansion/listings/loading.tsx
```

Redirect `/` to `/operator`. Redirect retired expansion/map URLs to
`/operator?ws=network`, retired pricing/adlift URLs to
`/operator?ws=growth`, retired audit/admin URLs to
`/operator?ws=govern`, and every other retired visual URL to `/operator`.
Redirects preserve old bookmarks but must not preserve old components.

### Exact migrations

| Source | Target | Required change |
|---|---|---|
| `apps/web/features/map/HeatZoneMap.tsx` | `apps/web/features/operator/network/HeatZoneMap.tsx` | Move the live map; replace `resolveProductionMode` with `isOperatorProductionMode` plus explicit-true override |
| `apps/web/features/map/map.module.css` | `apps/web/features/operator/network/heatZoneMap.module.css` | Move styles and update the import |
| `HeatZone`, `Listing`, `CandidateSite` declarations in `apps/web/features/expansion/data.ts` | `apps/web/features/operator/network/mapTypes.ts` | Move only map-facing type contracts; do not retain expansion fixture imports |
| `apps/web/features/operator/networkFindAreasLoader.ts` | `apps/web/features/operator/network/networkFindAreasLoader.ts` | Move the API binding/adapters unchanged and update `OperatorConsole.tsx` |
| `formatStamp` in `apps/web/features/shell/HomeWorkspace.tsx` | `apps/web/features/shell/formatStamp.ts` | Preserve the helper used by `FranchiseeWorkspace.tsx` before deleting `HomeWorkspace.tsx` |

### Exact retirement roots and files

Delete these roots only after the map/type migration:

```text
apps/web/features/adlift
apps/web/features/audit
apps/web/features/avm
apps/web/features/expansion
apps/web/features/intervention
apps/web/features/learninghub
apps/web/features/map
apps/web/features/netplan
apps/web/features/operations
apps/web/features/priceops
```

Delete these old shell visual files while retaining the franchisee and
nonvisual shell resources used by the canonical franchisee page:

```text
apps/web/src/app/OpsBoardFrame.tsx
apps/web/features/shell/AcknowledgeButton.tsx
apps/web/features/shell/AdminWorkspace.tsx
apps/web/features/shell/AssignTaskForm.tsx
apps/web/features/shell/HomeWorkspace.tsx
apps/web/features/shell/NotificationsWorkspace.tsx
apps/web/features/shell/OfflineBanner.tsx
apps/web/features/shell/PreferencesForm.tsx
apps/web/features/shell/RoleWorkspacesForm.tsx
apps/web/features/shell/SearchKeyboardNav.tsx
apps/web/features/shell/SearchWorkspace.tsx
apps/web/features/shell/SettingsForm.tsx
apps/web/features/shell/SettingsWorkspace.tsx
apps/web/features/shell/TaskCenterWorkspace.tsx
```

Delete these 18 legacy visual E2E specs:

```text
tests/e2e/e2e-api-bound-ui.spec.ts
tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts
tests/e2e/e2e-avm-netplan.spec.ts
tests/e2e/e2e-exp.spec.ts
tests/e2e/e2e-expansion-product.spec.ts
tests/e2e/e2e-intervention-price-ad.spec.ts
tests/e2e/e2e-learning-audit.spec.ts
tests/e2e/e2e-map-a11y.spec.ts
tests/e2e/e2e-map-live-boundary.spec.ts
tests/e2e/e2e-map-resilience.spec.ts
tests/e2e/e2e-map-tooltip-evidence.spec.ts
tests/e2e/e2e-map.spec.ts
tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts
tests/e2e/e2e-ops.spec.ts
tests/e2e/e2e-pgap-ux-001.spec.ts
tests/e2e/opsboard-shell.spec.ts
tests/e2e/shell-product-mobile.spec.ts
tests/e2e/shell-product.spec.ts
```

### Writable paths

R3A may write only the exact delete/migrate paths above and:

```text
apps/web/next.config.mjs
apps/web/src/app/layout.tsx
apps/web/src/app/error.tsx
apps/web/src/app/global-error.tsx
apps/web/src/app/loading.tsx
apps/web/src/app/not-found.tsx
apps/web/src/app/__tests__/productionRoutes.test.ts
apps/web/features/operator/OperatorConsole.tsx
apps/web/features/operator/NetworkFindAreasWorkspace.tsx
apps/web/features/operator/operatorDataMode.ts
apps/web/features/operator/network/HeatZoneMap.tsx
apps/web/features/operator/network/heatZoneMap.module.css
apps/web/features/operator/network/mapTypes.ts
apps/web/features/operator/network/networkFindAreasLoader.ts
apps/web/features/shell/FranchiseeWorkspace.tsx
apps/web/features/shell/formatStamp.ts
packages/ui/src/nav/routes.ts
docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3A.json
```

Do not edit intake visuals, API code, or any of the 16 canonical E2E specs.

### R3A gates

```text
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run build --workspace=@oday-plus/web
npx playwright test --list --project=chromium <the 16 canonical specs>
git diff --check
```

Static gates:

- executable page inventory equals exactly the three keep paths;
- the ten retired feature roots, `OpsBoardFrame.tsx`, old loader path, and 13
  old shell files are absent;
- imports contain no retired feature root or `OpsBoardFrame`;
- shared navigation contains no retired href;
- all 18 legacy specs are absent and all 16 canonical specs remain;
- `/operator`, `/intake/[intakeId]`, and `/franchisee` build successfully.

## ODP-P10-CAN-001-R3B - Single Intake Runtime

Objective: replace the production tabbed intake detail with the Package 10
continuous full-page composition. Follow the intake addendum for exact
migrate/delete paths and section order.

Writable paths:

```text
apps/web/src/app/intake/[intakeId]/page.tsx
apps/web/src/app/intake/layout.tsx
apps/web/src/app/operator/operator-layout.css
apps/web/features/operator/OperatorConsole.tsx
apps/web/features/operator/network/ListingRadarPanel.tsx
apps/web/features/operator/network/intake/AssistedIntakeSection.tsx
apps/web/features/operator/network/intake/ListingInboxIntakeView.tsx
apps/web/features/operator/network/intake/IntakeProcessingDetail.tsx
apps/web/features/operator/network/intake/AssistedEntryForm.tsx
apps/web/features/operator/network/intake/intakeFreshness.ts
apps/web/features/operator/network/intake/ListingCompareTable.tsx
apps/web/features/operator/network/intake/MatchEvidencePanel.tsx
apps/web/features/operator/network/intake/AssignmentSlaSummary.tsx
apps/web/features/operator/network/intake/DurableReceiptPanel.tsx
apps/web/features/operator/network/intake/EvidencePanel.tsx
apps/web/features/operator/network/intake/IntakeErrorRecovery.tsx
apps/web/features/operator/network/intake/IntakeStageTimeline.tsx
apps/web/features/operator/network/intake/PromotionReviewPanel.tsx
apps/web/features/operator/network/intake/SiteScoreJobStatus.tsx
apps/web/features/operator/network/intake/StateMatrix.tsx
apps/web/features/operator/network/intake/intake.module.css
apps/web/features/operator/network/intake/intakeClient.ts
apps/web/features/operator/network/intake/intakeTypes.ts
apps/web/features/operator/network/intake/types.ts
apps/web/features/operator/network/intake/urlState.ts
apps/web/features/operator/network/intake/index.ts
apps/web/features/operator/network/intake/__tests__/AssignmentSlaSummary.test.tsx
apps/web/features/operator/network/intake/__tests__/IdentityDecisionPanel.test.tsx
apps/web/features/operator/network/intake/__tests__/IntakeProcessingDetail.test.tsx
apps/web/features/operator/network/intake/__tests__/Package10VisualP1.test.tsx
docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3B.json
```

R3B may delete only the four alternate/orphan intake files named in the
addendum, plus
`apps/web/features/operator/network/intake/__tests__/IdentityDecisionPanel.test.tsx`
after its assertions are migrated. It must not edit `tests/e2e/**`,
`apps/api/**`, auth middleware, source-policy decisions, or permission rules.

R3B gates:

```text
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run build --workspace=@oday-plus/web
npx playwright test tests/e2e/operator-assisted-listing-intake-a11y.spec.ts --project=chromium
git diff --check
```

The a11y spec is the axe gate and is run unchanged. Static gates must prove
zero orphan intake files/imports, one production detail composition, continuous
sections without detail tabs, a durable direct route, a 390px inline
`DESKTOP_REQUIRED` result for `POSSIBLE_MATCH`, full 1024px comparison, and no
active Package 6/7 visual-baseline wording.

## ODP-P10-CAN-002-R3 - API/Security Re-verification

- Status: `blocked_on_CAN001_R3B`
- Writable by default: only
  `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-002-R3.json`
- Conditional fixes: `apps/api/**` or `packages/openapi-client/**` only after
  an unchanged named test proves a real contract defect.
- Forbidden: web visual files and `tests/e2e/**`.

Run the current exact 69-test collection:

```text
uv run pytest -q \
  tests/contract/test_operator_assisted_listing_api.py \
  tests/contract/test_operator_network_listings_api.py \
  tests/contract/test_operator_network_rebalance_api.py \
  tests/contract/test_operator_network_review_api.py \
  tests/contract/test_operator_network_scoring_api.py \
  tests/integration/test_operator_canonical_wiring.py \
  tests/security/test_assisted_listing_intake_authorization_matrix.py \
  tests/security/test_operator_security_platform.py
```

Tenant headers, source policy, self-review denial, second actor, idempotency,
version conflicts, audit/WORM evidence, and promotion must remain fail-closed.
Coordinator collect-only evidence is `69 tests collected` across these exact
eight files; any `71 tests` claim is stale/conflicting.

## ODP-P10-CAN-003-R3A - Canonical E2E Alignment

- Status: `blocked_on_CAN002_R3`
- Writable: the following 16 specs and its own ACK only.

```text
tests/e2e/e2e-network-find-areas-api-binding.spec.ts
tests/e2e/e2e-operator-console.spec.ts
tests/e2e/operator-assisted-listing-intake-a11y.spec.ts
tests/e2e/operator-assisted-listing-intake-mobile.spec.ts
tests/e2e/operator-assisted-listing-intake.spec.ts
tests/e2e/operator-governance.spec.ts
tests/e2e/operator-growth.spec.ts
tests/e2e/operator-network-assisted-intake.spec.ts
tests/e2e/operator-network-listings.spec.ts
tests/e2e/operator-network-rebalance.spec.ts
tests/e2e/operator-network-review.spec.ts
tests/e2e/operator-network-scoring.spec.ts
tests/e2e/operator-shell-today.spec.ts
tests/e2e/operator-store-ops.spec.ts
tests/e2e/product-e2e-env.spec.ts
tests/e2e/shell-resource-binding.spec.ts
docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-003-R3A.json
```

Align assertions to the implemented Package 10 runtime without removing or
weakening required behavior. Cover the continuous detail, real source/field/
signal/compare/decision data, 390px `DESKTOP_REQUIRED`, 1024px full flow,
1440px full flow, durable reload/return, axe, API reads/writes, and fail-closed
states. A missing required UI returns the program to CAN-001-R3B.

## ODP-P10-CAN-003-R3B - Read-only Chromium Gate

- Status: `blocked_on_CAN003_R3A`
- Writable: only
  `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-003-R3B.json`
- Expected inventory: 16 files, 107 Chromium tests.

Run the 16 specs unchanged. Any product, test, config, snapshot, or helper edit
is a no-go.

## ODP-P10-CAN-004-R3 - Release Closure

- Status: `blocked_on_CAN003_R3B`
- Writable: release evidence and
  `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-004-R3.json`
  only.

Reconcile all committed ACKs; rerun unit, typecheck, build, axe, route/import/
orphan gates, 69 API/security tests, and 107 Chromium tests. Verify archive
hashes, rebase against current `origin/dev`, rerun the gates, push the exact
commit, and record deployment readiness. Do not claim deployment without a
real deployment receipt.

## Persistence

Every wave must pass `git diff --check`, commit only allowed paths, push the
exact commit to `origin/fix/package10-final-20260725`, and record the pushed SHA
in its ACK after coordinator review. Ownership transfers only after the
coordinator confirms the committed ACK and pushed SHA. Uncommitted or
temporary-worktree output is not delivery evidence.
