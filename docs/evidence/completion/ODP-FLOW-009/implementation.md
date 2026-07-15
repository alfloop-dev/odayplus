# ODP-FLOW-009 · Complete Learning Hub validation release and rollback flow — Implementation

- Task: ODP-FLOW-009 (Product Flow Implementation phase)
- Owner: Claude · Reviewer: Claude2
- Source design: `docs/design/ODAY_PLUS_LEARNING_HUB_UI_SPEC.md`;
  release/rollback state machine encoded in `modules/learninghub`

## Scope

The Learning Hub validation → release → rollback loop already shipped and is
durable: dataset snapshot registration, feature/label allow-listing, model
validation gates, model cards, artifact registration, and the
SHADOW/CANARY/FULL/ROLLBACK release state machine live in
`modules/learninghub` with a durable repository + artifact store
(`shared/infrastructure/persistence`) and the `learninghub_router`
(`apps/api/app/routes/learninghub.py`). ODP-FLOW-009 closes the two remaining
product-flow gaps named in the acceptance:

1. **API-backed Learning UI** (acceptance #4) — the release log UI was
   fixture-only; it now binds to the live backend.
2. **Release monitor** (acceptance #3) — the release/rollback state machine was
   audited, but there was no executable monitor that evaluates a live release's
   monitoring window against its guardrails and records the result.

## What changed

### Backend — release monitor (`modules/learninghub/application/monitor.py`)
- New pure guardrail evaluator: `evaluate_guardrails(observed_metrics,
  guardrails)` reuses `MetricThreshold.evaluate` and returns a `GuardrailBreach`
  per hard-limit failure (WARNING bands are not breaches; absent metrics are
  skipped). Result types: `MonitorStatus`, `RecommendedAction`,
  `ReleaseMonitorAssessment`.
- `LearningHubService.monitor_release(...)` (`application/release.py`) looks up
  the release decision, evaluates the guardrails, records a
  `learninghub.release_monitor.v1` audit event (`outcome=breached|healthy`,
  metadata carries observed metrics, breaches, recommended action, rollback
  target), and returns the assessment. Consistent with the platform's
  never-optimistic stance, a breach **recommends** a rollback (surfaced to the
  Rollback Console for human approval) — it never mutates an alias or stage.
- Worker entry point `run_learninghub_release_monitor` and a governed endpoint
  `POST /learninghub/releases/{release_id}/monitor` (perm `model:PUBLISH`).

### openapi-client (`packages/openapi-client/src/index.ts`)
- Added the `ModelReleaseSummary` type (mirrors
  `ModelReleaseDecision.to_dict()`: `release_id`, `model_name`,
  `from_version`/`to_version`, `release_type`, `monitoring_window`,
  `rollback_target`, `approved_by`, `created_at`, `audit_event_id`; detail left
  open via an index signature).
- Added `listLearningReleases({ modelName })` → `GET /learninghub/releases`,
  returning the standard `ListResponse<ModelReleaseSummary>` envelope.

### Web (`apps/web/features/learninghub/LearningHubWorkspace.tsx`, `.../app/learning/page.tsx`, `.../app/w/ai/releases/page.tsx`)
- `/learning` and `/w/ai/releases` are now `force-dynamic` and build an
  `ApiBinding<ModelReleaseSummary>` from `GET /learninghub/releases` via
  `getServerApiClient()` + `loadApiBinding` (never throws; degrades to fixture).
- New `LiveReleases` region renders the live release/rollback log
  (`release_id` / model / type / version / monitoring / audit_event_id) with a
  `DataSourceBadge` (`data-testid="learning-data-source"`, region
  `data-testid="learning-live-releases"`). Type tone: ROLLBACK → red, FULL →
  green, CANARY → orange, SHADOW → blue.
- The existing fixture `ReleasesTable` remains a **documented non-product
  fallback** for cold-store / unconfigured / error states (distinct copy per
  state), so the product renders without a backend. Browser default roles lack
  `model:VIEW`, so the UI keeps its documented fallback while the API-level
  assertions prove the live loop.

### Tests
- `tests/integration/test_learninghub_release.py` — three new tests: healthy
  monitor records an audit event and recommends no action; a breached monitor
  recommends ROLLBACK, writes a `breached` audit event, and leaves the
  PRODUCTION alias unchanged (never optimistic); an unknown release raises.
- `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts` — after the
  CANARY → FULL loop the spec now POSTs the monitor (asserts `BREACHED` /
  `recommended_action = ROLLBACK` / breach metric), asserts the
  `GET /learninghub/releases` list the UI binds to contains the FULL release and
  a ROLLBACK, expects `learninghub.release_monitor.v1` in the audit stream, and
  navigates to `/w/ai/releases` to assert the `learning-live-releases` region
  and `learning-data-source` badge render.

## Acceptance mapping

| Acceptance criterion | Where satisfied |
| --- | --- |
| dataset model and artifact registrations persist | Durable repository + artifact store (`shared/infrastructure/persistence`) wired via `bundle` in `apps/api/oday_api/main.py`; POST dataset-snapshots / versions persist (pre-existing, re-verified). |
| validation gates and model cards are executable | `validate_candidate`, `register_model_version`, `_assert_release_gate` (`application/release.py`), exposed at the router; covered by integration + product E2E. |
| release monitor and rollback state machine is audited | Rollback state machine audits `learninghub.model_release.v1`; new `monitor_release` audits `learninghub.release_monitor.v1` and recommends rollback on breach without optimistic mutation. |
| API backed Learning UI E2E passes | `/w/ai/releases` binds to `GET /learninghub/releases` with a `DataSourceBadge`; product E2E asserts the live region + the release-log endpoint + the monitor audit event. |
