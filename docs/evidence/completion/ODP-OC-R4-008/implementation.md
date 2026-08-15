# ODP-OC-R4-008 Implementation

## Scope

- Added `modules/opsboard/application/network_rebalance.py` as the Operator R4 low-efficiency rebalance service.
- Added `/api/v1/operator/network-rebalance/*` routes for AVM request/completion, NetPlan solve, scenario selection, submit review, and reset.
- Replaced the fixture-only rebalance tab with `apps/web/features/operator/network/RebalancePanel.tsx`.
- Connected `NetworkFindAreasWorkspace` to the rebalance API and preserved fallback fixtures only when the API is unavailable.
- Added `OperatorStateService.upsert_network_rebalance_approval()` so submit review creates a Govern approval row.

## Package 6 Source

- Canonical ZIP: `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip`
- SHA-256: `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`
- Interactive HTML: `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/extracted/Oday Plus Operator Console.dc.html`
- Relevant labels: `Network 低效重配`, `Govern 治理稽核`

## Behavior

- Initial rebalance candidate starts at `watching` with no AVM valuation block.
- AVM completion returns service-owned P10/P50/P90, model version, snapshot id, and evidence id.
- NetPlan solve returns three service-owned scenarios: `keep`, `move`, `exit`, each with model/snapshot metadata.
- Scenario selection persists owner and evidence across reloads.
- Submit review creates `APR-NET-RB-801` in Govern approvals and leaves `relocationExecuted=false`.
- Runtime/model unavailability returns HTTP 503 with `state=retryable_unavailable` and does not advance valuation or execution state.
