# Verification Report: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001

## Review Scope

- Owner: `Antigravity`
- Reviewer: `Codex`
- Branch: `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Reopen baseline: `origin/dev` at `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`
- Reopened branch head: `82f4c57e3d47b4738c818a375c5a3ec4bd0c91dc`
- Health-contract anchor: `010ceef74e8fd661ad7964c741f19dc75cfaae9f`
- Failed deployment evidence: Deploy Dev run `30735077825`
- Failed equivalent-tree CI evidence: run `30735077838`

The reopened head had the same tree as the baseline. Its real
`/platform/health` and `/readiness` responses reported `status=ok` and carried
the live mode only under `modes.data.mode` or `details.data.mode`. The unchanged
deployment validator accepted a direct declared mode and consequently reported
`data_mode=<missing>` during candidate smoke.

## Reopen Remediation

- `/platform/health` now exposes top-level `data_mode` from the exact same
  `modes["data"]["mode"]` value used by the existing nested contract.
- `/readiness` exposes the same top-level field for both healthy and unhealthy
  responses while preserving `details.data.mode`.
- The deployment validator and deployment workflow remain unchanged.
- A real `create_app()` regression feeds health and readiness payloads into the
  unchanged validator and proves three states:
  - serviceable PostgreSQL/live composition: HTTP 200, `status=ok`,
    `data_mode=live`, accepted;
  - live-required memory composition: HTTP 503, `status=unhealthy`,
    `data_mode=unavailable`, rejected;
  - non-live memory composition: HTTP 200, `status=ok`, `data_mode=fixture`,
    rejected by the live-mode condition.

The response field is provenance only; it does not reclassify ForecastOps,
alter production model bindings, weaken the release gate, fabricate an alias,
or change the model/history row policy. ForecastOps remains required and
unresolved until authentic 7/14/28-day history and an independently approved
MLflow production alias exist.

## Current Modified File Inventory

Relative to the reopen baseline, this task owns only:

1. `apps/api/oday_api/main.py`
2. `tests/integration/test_operator_live_provenance_health.py`
3. `tests/ops/test_cloud_run_live_deployment.py`
4. `docs/evidence/runtime/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001/verification_report.md`

There are no task diffs in deployment/model gates, Package 10 visuals or
routes, design/archive documents, retired-path inventory, orchestrator files,
or GitHub workflows. The concurrent health-contract PR #574 overlaps the API
response shape, but this task branch deliberately does not import its validator
changes; review must use this task's exact pushed head.

## Local Verification

- Full focused replay (15 files): `uv run --frozen pytest -q` over health,
  fail-closed, platform contract, external ingestion, Operator repository and
  composition, tenant routing, HeatZone, SiteScore, and deployment-validator
  suites; exit `0`, with one existing conditional skip and deprecation warnings
  only.
- Reopen regression after adding the unavailable-state assertion:
  `uv run --frozen pytest -q tests/ops/test_cloud_run_live_deployment.py::test_real_app_health_data_mode_matches_unchanged_deploy_validator`; exit `0`.
- Narrow pre-anchor replay: the Operator health integration case plus the real
  app-to-validator regression; exit `0`.
- Ruff on the three touched Python files: clean.
- `git diff --check`: clean.

## Pending External Evidence

The old `82f4c57e` head had no GitHub check-runs. Exact-head CI, independent
Codex review, PR merge, and a post-merge Deploy Dev replay are still required;
this report does not claim them complete. Even after the direct health mode
contract deploys, `ODP-P10-DEV-REDEPLOY-VERIFY-001` remains blocked on the real
ForecastOps history/model release if the unchanged deployment gate still
requires the authentic production alias.
