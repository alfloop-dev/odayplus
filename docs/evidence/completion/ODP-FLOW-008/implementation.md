# ODP-FLOW-008 · Complete NetPlan scenario solver and publish flow — Implementation

- Task: ODP-FLOW-008 (Product Flow Implementation phase)
- Owner: Claude · Reviewer: Antigravity3
- Source design: `docs_archive/05_module_design/ODP-MOD-09_NETPLAN.md`,
  `docs/design/ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md` (Part B)

## Scope

The full NetPlan decision loop — scenario builder → constraints/versions →
constrained solve → alternatives / explained infeasibility → submit → approval →
execution → outcome tracking, all audited — already shipped in
`modules/netplan`, `solver/netplan`, and `apps/api/app/routes/netplan.py`
(ODP-R5-002). ODP-FLOW-008 closes the remaining product-flow gap named in the
task acceptance: the **API-backed comparison UI** and the **deterministic
end-to-end proof** that a backend scenario reaches that UI.

The NetPlan overview (`/netplan`) previously rendered only bundled fixtures. It
now binds to the live scenario list, matching the established
AVM / Audit / Intervention API-binding pattern (ODP-FLOW-004).

## What changed

### openapi-client (`packages/openapi-client/src/index.ts`)
- Added the `NetPlanScenarioSummary` type (mirrors `NetPlanScenario.to_dict()`:
  `scenario_id`, `scenario_name`, `planning_horizon`, `status`,
  `solver_version`, `model_version`, `correlation_id`; full solve/execution/
  outcome detail left open via an index signature).
- Added `listNetplanScenarios()` → `GET /netplan/scenarios`, returning the
  standard `ListResponse<NetPlanScenarioSummary>` envelope. No runtime deps; the
  client is server-only (Next.js server components / Playwright), like the rest
  of the client.

### Web (`apps/web/features/netplan/NetPlanWorkspace.tsx`, `apps/web/src/app/netplan/page.tsx`)
- The `/netplan` route is now `force-dynamic` and builds an
  `ApiBinding<NetPlanScenarioSummary>` from `GET /netplan/scenarios` via
  `getServerApiClient()` + `loadApiBinding` (never throws; degrades to fixture).
- A new `LiveNetPlanScenarios` region renders the live scenario comparison
  (`scenario_id` / name / horizon / status / solver) with a `DataSourceBadge`
  (`data-testid="netplan-data-source"`, region `data-testid="netplan-live-scenarios"`).
  Status tone follows the lifecycle vocabulary (approved/executed/outcome_observed
  → green, infeasible/rejected → red, solved/pending_approval → blue).
- The existing fixture scenarios remain as a **documented non-product fallback**
  for cold-store / unconfigured / error binding states (distinct fallback copy
  per state), so the product still renders without a backend.

### Tests (`tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`)
- After the backend loop drives create → solve → submit → decide → execute →
  outcome → detail, the spec now asserts:
  - `solve.result.alternative_plan_available === true` and a non-empty
    `alternatives` set — the side-by-side comparison basis (acceptance #2);
  - `GET /netplan/scenarios` (the list endpoint the UI binds to) returns the
    scenario with `status === "outcome_observed"` and a truthy `solver_version`;
  - navigating the browser to `/netplan` renders the `netplan-live-scenarios`
    region and its `netplan-data-source` badge (the API-backed comparison UI).

## Acceptance mapping

| Acceptance criterion | Where satisfied |
| --- | --- |
| scenario constraints and versions persist | `NetPlanScenario` freezes `constraints` + `model_version`/`feature_version`/`solver_version`/`policy_version`; persisted via `create_scenario` and served by `GET /netplan/scenarios` (list) + `/{id}` (detail) |
| solver returns alternatives or explained infeasibility | `solve_network_plan` → feasible plan with ranked `alternatives`, or `INFEASIBLE` + structured `diagnostics`; E2E asserts `alternative_plan_available` + non-empty alternatives on the feasible path (the infeasible path stays terminal, no auto-relax) |
| approval publish and tracking are audited | `submit`/`decide`/`execute`/`record_outcome`/`close` each emit `netplan.*.v1` audit events; the product E2E asserts `netplan.solved.v1`, `netplan.executed.v1`, `netplan.outcome_observed.v1` under the correlation id |
| API backed comparison UI deterministic E2E passes | `/netplan` bound to `GET /netplan/scenarios` via typed `ApiBinding` + `DataSourceBadge`; the deterministic product E2E drives the full loop and asserts the list endpoint + the live comparison region — **run green** (see verification.md) |

## Compose / boundary

- Owned layer: NetPlan web comparison binding + openapi-client method +
  product-E2E assertions + this evidence.
- Not changing: the NetPlan domain / solver / API router (already durable in dev
  via ODP-R5-002); other FLOW rows in the shared matrix are left for their owners.
- Composes with: the dev NetPlan feature shell, `solver/netplan` (ortools SCIP),
  and the `netplan_router` audit/RBAC surface.
