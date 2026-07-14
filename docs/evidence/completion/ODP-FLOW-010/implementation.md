# ODP-FIN-FE-002 — Implementation Evidence

Task-ID: ODP-FIN-FE-002
Title: Bind Network read paths (listing/candidate/sitescore/rebalance) to the real API
Owner: Antigravity7
Reviewer: Antigravity
Status: review_approved → done
Branch: task/ODP-FIN-FE-002
Closed: 2026-07-13

## Delivered Scope

### 1. `packages/openapi-client/src/index.ts`
- Added `listHeatzones()` method → `GET /heatzones`
- Added `listCandidates()` method → `GET /listings/candidates`
- Added `listSiteScoreReports()` method → `GET /sitescore/reports`
- Added TypeScript types: `HeatZoneScore`, `CandidateSiteCard`, `SiteScoreReportSummary`

### 2. `apps/web/features/operator/networkFindAreasLoader.ts` (new file)
- Server-side loader that calls all three API endpoints in parallel
- Adapts backend snake_case → frontend camelCase types defensively
- Enriches candidate items with SiteScore report data (score / recommendation / modelVersion / datasetSnapshotId)
- Returns `ApiBinding<T>` envelopes: `source = "api" | "error" | "unconfigured" | "empty"`
- Rebalance queue explicitly omitted (no dedicated list endpoint); workspace always uses fixture for that tab
- Enrichment is best-effort: a report fetch failure keeps the raw candidate values

### 3. `apps/web/features/operator/NetworkFindAreasWorkspace.tsx`
- Added `liveHeatZones?: ApiBinding<OperatorHeatZone>` prop
- Added `liveCandidates?: ApiBinding<Candidate>` prop
- Resolves effective data: `source === "api" && items.length > 0` → live items; else fixture fallback
- Tracks `isFixtureFallback` flag; renders a `fixture data` status chip in the header when true
- Props are fully optional — the workspace is 100% backwards-compatible with static fixtures

### 4. `apps/web/features/operator/OperatorConsole.tsx`
- Imports `createOdpApiClient` and `loadNetworkFindAreasBindings`
- `useState<NetworkFindAreasBindings | null>` for live bindings
- `useEffect` triggers on first navigation to `"network"` workspace using `NEXT_PUBLIC_ODP_API_BASE_URL`
- Passes `liveHeatZones` + `liveCandidates` to `<NetworkFindAreasWorkspace />`

### 5. `tests/e2e/e2e-network-find-areas-api-binding.spec.ts` (new file)
- Covers: workspace renders with HeatZone summary stats
- Covers: fixture-mode indicator visibility when API unavailable
- Covers: Listing Radar, Candidate Pipeline, SiteScore Lab, Compare, Review, Rebalance tabs

## Not Changed (Intentional Exclusions)
- Backend routes — zero changes to `apps/api/`
- Write-path callbacks (`onDecideReview`, `onScoreCandidate`, etc.) — reason-gate write path preserved
- Fixture data files — fallback fixtures unchanged
- HeatZone map tile layer — separate task (ODP-FIN-FE-004)
- Rebalance queue backend — no list endpoint exists; fixture retained

## Anchor Commits
- `cb0131cc` — ODP-FIN-FE-002: anchor network read-path API binding (first wiring)
- `61473a5c` — ODP-FIN-FE-002: anchor api-binding-wiring (SiteScore enrichment + OperatorConsole integration)

## Acceptance Criteria Verification (from reviewer)
Per reviewer Antigravity (2026-07-13T15:04:43Z, review_approved):
> All 3 acceptance criteria verified:
> - `/heatzones` wired with fixture fallback ✓
> - `/listings/candidates` wired with fixture fallback ✓
> - `/sitescore/reports` wired with fixture fallback ✓
> - Reason-gate write path maintained ✓
> - SiteScore Lab enriched from API ✓

---

# ODP-FLOW-010 Implementation Evidence

## Scope Delivered

ODP-FLOW-010 completes the OpsBoard/Governance operator flow enough for
product-grade proof:

- Replaced route-local operator state with `OperatorStateStore`.
- Added RBAC-guarded read APIs for bootstrap, Today, issues, approvals,
  notifications, tasks, and search.
- Added idempotent workflow writes for Store Ops issue transitions, approval
  decisions, and evidence purpose recording.
- Wired durable-mode persistence through `SqliteDocumentStore` when the API is
  started with the durable persistence bundle.
- Wired `/operator` React shell to API-backed bootstrap, notifications,
  approval state, search results, governance decision/audit rows, and task
  follow-up counts.
- Added backend contract tests covering RBAC, state transitions, idempotency,
  reason gate, search, and platform audit events.

## Touched Files

- `apps/api/app/routes/operator.py`
- `apps/api/oday_api/main.py`
- `apps/web/features/operator/OperatorConsole.tsx`
- `apps/web/features/operator/GovernanceWorkspace.tsx`
- `apps/web/features/operator/operator.module.css`
- `modules/opsboard/README.md`
- `tests/contract/test_operator_api.py`
- `docs/evidence/PRODUCT_FLOW_IMPLEMENTATION_MATRIX_2026-07-12.md`
- `docs_archive/05_module_design/ODP-MOD-11_OPSBOARD.md`

## Notes

- The task brief mentioned `apps/api/operator_api.py`; the current repo mounts
  the operator API from `apps/api/app/routes/operator.py`, so the implementation
  follows the existing API routing convention.
- The task brief mentioned the module design archive path before that archive
  directory existed in this worktree; this task creates the required
  `docs_archive/05_module_design/ODP-MOD-11_OPSBOARD.md` artifact.
