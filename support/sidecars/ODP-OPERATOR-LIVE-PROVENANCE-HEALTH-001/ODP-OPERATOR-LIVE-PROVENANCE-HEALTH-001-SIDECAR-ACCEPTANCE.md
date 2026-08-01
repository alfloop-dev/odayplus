# ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001 acceptance packet

- Support task: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Packet owner: `Codex7`
- Packet reviewer: `Antigravity4`
- Prepared: `2026-08-01`
- Authority: support-only; this packet does not change canonical contracts, runtime behavior, task scope, or review disposition.

## Purpose and decision rule

Use this packet to assemble the parent's exact-head evidence and to prevent a narrow positive test from hiding cross-tenant, fail-open, scope, or model-gate regressions. The parent is ready for independent review only when every `P0` item below has evidence at one exact pushed head. An external dependency may remain blocked only where the parent acceptance explicitly permits fail-closed unresolved behavior.

The reviewer should record each item as `PASS`, `FAIL`, or `BLOCKED`, with a command, artifact path, or immutable receipt. An assertion in a verification report is not evidence unless it matches the exact pushed head and replay output.

## Preparation snapshot

This is a volatile preparation snapshot, not an approval:

- `origin/dev`: `eed83c0937f491211247ee3fdb0bdf8d932564fb`
- parent remote head: `a0333308b1ffbfcac38c2fc3e4e319908b6e4ae3`
- merge base: `eed83c0937f491211247ee3fdb0bdf8d932564fb`
- parent branch distance: 20 commits ahead, 0 behind
- GitHub PR for the parent branch: none found at preparation time
- latest parent commit addresses tenant-scoped ingestion scheduler state and rehydration after the `c9b910a4` cross-tenant provenance rejection

Recompute all values before handoff. The current parent `verification_report.md` still names baseline `97e3ae2e`, omits the newest external-ingestion test scope, and does not attest `a0333308`; it must be refreshed by the parent owner before review.

## Sidecar CI reconciliation

PR `#560` reached exact pushed head `a7e8b7b87c629ef4eb11a2f05b18cc4d8cbb3f68` with a one-file diff containing only this packet. Its CI run `30723040164` passed `orchestrator` and `performance-gate`, but job `91429808881` failed `product-e2e-gate` before merge. The authoritative GitHub log reports:

```text
Product release gate failed:
- intervening commits touch non-evidence paths: support/sidecars/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001-SIDECAR-ACCEPTANCE.md
make: *** [Makefile:79: product-e2e-gate] Error 1
```

This is a shared CI/sidecar compatibility blocker, not a failed packet assertion: the current receipt validator treats the required `support/sidecars/**` artifact as a product-runtime mutation requiring a fresh Product E2E receipt. Refreshing product evidence or changing `.github`, `Makefile`, or `scripts/e2e/**` is outside this support-only task. Resolution is owned by `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001`; after that task merges, rebase or merge the repaired `origin/dev`, rerun PR checks, and request independent re-review at the new exact pushed head.

Do not work around this blocker by moving the packet into product evidence, manufacturing a receipt, weakening the fail-closed release gate, or editing canonical CI from this sidecar lane.

## P0 acceptance checklist

| Gate | Required proof | Fail conditions |
| --- | --- | --- |
| P0-1 Baseline reproduction | Preserve evidence for Deploy Dev run `30680943677` at deployed SHA `97e3ae2e`, including `/platform/health` and `/readiness` 503 behavior, degraded Operator bootstrap provenance, and unverified model binding details. | The remediation report drops the original failure, changes its SHA/run identity, or claims a new deploy without an exact-SHA receipt. |
| P0-2 Tenant-authoritative reads | Every Operator section is backed by a verified request tenant and the same authoritative repository used by its canonical writer. Prove tenant A visibility, tenant B isolation, and restart visibility through real API composition. | Global/unscoped fallback; missing tenant ownership; direct test-only writes that bypass the canonical API writer; tenant A data appears in tenant B; a write disappears after restart. |
| P0-3 Truthful section state | An authoritative empty result may be `available` only after a successful tenant-scoped read. Missing resolution, failed queries, ownership-less rows, or unavailable projections must be explicit `unavailable`/`degraded` with reason codes. | Zero records is treated as proof of availability; `meta.dataMode=live`, `complete=true`, or authoritative origin is emitted while any required section was not safely read. |
| P0-4 Ingestion lifecycle isolation | On one service instance, tenant A and B using the same provider/schedule/window and colliding API keys receive tenant-local scheduler, idempotency, watermark, circuit, capture, run, correlation, and source provenance state. Rehydration must read each tenant's authoritative store. | Cache keys omit tenant; tenant B replays tenant A's run/correlation/source; provider capture crosses tenants; `_rehydrate` enumerates only a global store; factory failure falls back or is swallowed. |
| P0-5 Core health layering | With database/provider/Operator repositories serviceable, `/platform/health` and `/readiness` can return 200 without erasing `productionModelBindingsReady=false` and its explicit capability blockers. | Missing model alias crashes or globally marks repository service unavailable; health reports model bindings ready; blocker detail is suppressed. |
| P0-6 ForecastOps fail-closed | `ForecastOps` remains required-active. Until authentic per-store 7/14/28-day history and an approved MLflow production alias exist, capability availability and execution remain unresolved/unavailable and fail closed. | `governed-disabled` reclassification, daily row count presented as horizon completeness, fabricated alias/receipt, fixture/synthetic/auto-seed data, empty-registry bypass, or weakened execution/release gate. |
| P0-7 Protected-surface integrity | Diff proves Package 10 visuals/routes/design archives, deploy workflows, shared model contracts, model scripts, and live E2E gates remain identical to the accepted baseline. | Any forbidden path changes, deleted regression, minimum-row drift, or release-gate bypass. |
| P0-8 Scope authorization | Every changed file is within the parent's current `writable_paths`, or task metadata contains explicit authorization before review. | A behavior may be correct but the branch mutates an unowned layer. Sidecar or reviewer inference is not authorization. |
| P0-9 Exact-head evidence | Focused replay, lint, `git diff --check`, diff inventory, pushed-head equality, PR, independent review, and all required GitHub checks refer to one SHA. | Stale report, local-only head, mismatched baseline/inventory, no PR, pending/red checks, or test claims without captured output. |
| P0-10 Downstream truth | After parent merge, `ODP-P10-DEV-REDEPLOY-VERIFY-001` stays blocked if the unchanged deploy gate still requires a real ForecastOps production alias. | Core-health remediation is represented as completion of model release or successful runtime redeploy. |

## Tenant/provenance negative matrix

The parent test packet should exercise these cases through production composition, not test-only repository shortcuts:

| Surface | Positive proof | Required negative proof |
| --- | --- | --- |
| Listings | tenant A canonical API write is visible through a fresh app's Operator API after durable restart | tenant B cannot see it; ownership-less unscoped rows do not become a truthful empty/live section |
| HeatZone | real score-job writer persists explicit tenant ownership into the repository Operator reads | resolver failure cannot fall back to `bundle.heatzone_store`; tenant B sees no tenant A scores |
| SiteScore | real decision writer and Operator read use the same tenant partition | resolver failure is explicit; prediction/calibration behavior owned by `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001` is unchanged |
| External ingestion | same service + same window produces distinct tenant A/B runs, correlations, captures, and provenance; same-tenant replay dedupes locally | colliding API key/window cannot replay another tenant; store-factory failure from actual `create_app` composition is propagated; no duplicate-router false positive |
| Ingestion restart | fresh app/client rehydrates tenant-local idempotency/watermark and reads the prior run through `/api/v1/operator` | rehydration does not enumerate a process-global store or import another tenant's run state |
| Risk projection | risk rows derive from a governed tenant-authoritative source | failed signal source cannot emit fixed placeholder scores or a successful/normal risk state |
| Empty tenant | a successful authoritative query returning zero rows remains distinguishable from query unavailability | an unconfigured, failed, or ownership-rejected repository cannot be labeled `available`, `live`, and `complete` merely because its list is empty |

## Dependency map

The parent currently declares no formal `depends_on` entries. The following are operational acceptance gates and collision boundaries, not a sidecar rewrite of task metadata:

| Task / authority | Snapshot status | Relationship to parent |
| --- | --- | --- |
| `ODP-MODEL-CAPABILITY-READINESS-001` | referenced by downstream tasks but no active task row or task-scoped brief was present in this worker snapshot | Source authority must be resolved from canonical status/archive before using its receipts. Do not invent its disposition. |
| `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` | blocked | Must supply real governed history with complete per-store 7/14/28-day windows; no fixture, synthetic, or auto-seed path. |
| `ODP-PRODUCTION-MODEL-REGISTRY-001` | blocked on real history and model-readiness dependencies | Owns ForecastOps activation, approved MLflow alias, immutable lineage, and release evidence. The parent preserves its required-active/fail-closed contract but does not manufacture activation. |
| `ODP-P10-DEV-REDEPLOY-VERIFY-001` | blocked after failed run `30680943677` | Consumer of the merged parent remediation and real model release. Must rerun from an exact merged `origin/dev` SHA; parent source tests are not redeploy evidence. |
| `ODP-LIVE-RUNTIME-DEV-COMPOSE-001` | blocked | Downstream live composition gate. Parent should not claim this deployment/composition outcome. |
| `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001` | todo | Adjacent ownership/collision boundary for SiteScore prediction, registry lineage, and calibration semantics; parent may tenant-scope persistence plumbing but must not change this task's prediction truth. |

Operational flow:

```text
authoritative Forecast history
  -> governed model registry + approved ForecastOps alias
  -> unchanged live release gate can pass
  -> exact-SHA dev redeploy verification

parent tenant/provenance remediation
  -> truthful Operator bootstrap + layered core health
  -> merge to dev after exact-head review
  -> redeploy verification still waits for model release when the gate requires it
```

## Scope audit at preparation head

The parent task brief permits writes under `apps/api/**`, the Operator live repository, three named integration suites, and its runtime evidence directory. At `a0333308`, these 11 changed files are outside that current writable allowlist:

```text
modules/external_data/application/ingestion_service.py
modules/external_data/application/ingestion_store.py
modules/heatzone/workers/scoring_worker.py
shared/domain/models.py
shared/infrastructure/persistence/external_data.py
shared/infrastructure/persistence/factory.py
shared/infrastructure/persistence/operator_domains.py
shared/infrastructure/persistence/repositories.py
shared/workflow/sitescore.py
tests/integration/test_external_ingestion_persistence.py
tests/ops/test_cloud_run_live_deployment.py
```

This packet does not decide whether those edits are acceptable. Before parent review, the owner must either restore them or obtain an explicit task-scope update from the governing task authority. Do not silently treat the broader `artifacts` list as equivalent to `writable_paths`.

## Exact-head evidence recipe

Run from a clean checkout after fetching both refs. Replace `PARENT_HEAD` only when the owner has formally handed off a newer pushed SHA.

```bash
PARENT_HEAD=origin/task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001
git fetch origin dev task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001
git rev-parse origin/dev "$PARENT_HEAD"
git merge-base origin/dev "$PARENT_HEAD"
git rev-list --left-right --count origin/dev..."$PARENT_HEAD"
git diff --name-status origin/dev..."$PARENT_HEAD"
git diff --check origin/dev..."$PARENT_HEAD"
```

Replay at the exact parent head in an isolated checkout/worktree:

```bash
uv run pytest -q \
  tests/integration/test_external_ingestion_persistence.py \
  tests/integration/test_operator_live_repository.py \
  tests/integration/test_operator_live_provenance_health.py \
  tests/integration/test_production_api_composition.py \
  tests/e2e/test_live_e2e_gate.py \
  tests/ops/test_cloud_run_live_deployment.py
uv run ruff check apps/api modules shared \
  tests/integration/test_external_ingestion_persistence.py \
  tests/integration/test_operator_live_repository.py \
  tests/integration/test_operator_live_provenance_health.py \
  tests/integration/test_production_api_composition.py \
  tests/ops/test_cloud_run_live_deployment.py
```

Protected-path comparison must be empty:

```bash
git diff --name-only origin/dev..."$PARENT_HEAD" -- \
  apps/web docs_archive docs/design docs/evidence/PACKAGE_10_\* \
  scripts/data_plane scripts/models scripts/e2e/check_live_e2e_gate.py \
  models/shared_ml tests/e2e/test_live_e2e_gate.py .orchestrator .github
```

The final evidence report should record:

- exact `origin/dev`, merge-base, parent head, and remote-head equality;
- exact changed-file inventory and explicit scope authorization result;
- test commands, counts, skips, exit codes, and relevant negative-test names;
- original Deploy Dev failure receipt and separation from any later redeploy;
- ForecastOps history/alias dependency receipts without relabeling the capability;
- PR URL, independent reviewer identity, exact reviewed SHA, and green required checks.

## Reviewer handoff template

```text
Packet review at <exact sidecar SHA> against parent <exact pushed parent SHA>:
- P0 behavior gates: <PASS/FAIL/BLOCKED with evidence>
- tenant/provenance negative matrix: <PASS/FAIL with test names>
- ForecastOps authority preserved: <PASS/FAIL; dependency receipt refs>
- scope authorization: <PASS/FAIL; out-of-allowlist disposition>
- protected diff: <PASS/FAIL>
- exact-head tests/checks/PR: <PASS/FAIL>
- downstream redeploy disposition: <still blocked / eligible to rerun, with reason>
Decision: <accept packet / request packet revision>
```

Acceptance of this sidecar packet means only that the checklist and dependency map are usable. The parent owner and assigned independent reviewer remain responsible for runtime acceptance and for deciding whether the parent branch can enter review.
