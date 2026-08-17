# Package 10 Live Completion Gap Analysis

- Document ID: `ODP-P10-LIVE-GAP-20260809`
- Status: `NO-GO`
- Prepared at: `2026-08-09T10:49:01Z`
- Evidence refreshed at: `2026-08-09T12:43Z` by `ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-20260809`
- Scope: Package 10 Operator runtime, API/data binding, legacy retirement, and live release closure
- Runtime baseline: `origin/dev@4d89bea64ce98753895a16194e320c9a8ea45852`
- Latest completed deployment evidence: Deploy Dev run `31312735093` at `4d89bea64ce98753895a16194e320c9a8ea45852` (`failure_rolled_back`)
- Exact live-gate artifact: generated `2026-08-09T12:32:33Z`, correlation ID `corr-live-e2e-4d89bea64ce9-1786278753`
- Public rollback release: `8ec12c02`, read back from the deployed `oday-api` at `2026-08-09T12:26:28Z` with `/platform/health` `503` at `12:26:29Z`

### Evidence refresh, dev merge burst of 2026-08-09

This document was first prepared against `9e5434cd`. Between `11:47Z` and
`12:33Z` the `dev` merge burst started three further Deploy Dev runs. Only the
last one completed a deployment attempt:

| Run | Head SHA | Merge | Conclusion | Window (UTC) | Standing |
|---|---|---|---|---|---|
| `31311664947` | `817d53052e23cf867085342fcafa340743e4a7cb` | PR #744 | `cancelled` | `11:47:34Z` → `12:05:44Z` | superseded by the next `dev` push; no live-gate artifact |
| `31312411417` | `188bec5411846fcb7439fb63991daadad7fee60f` | PR #745 | `cancelled` | `12:05:24Z` → `12:13:41Z` | superseded by the next `dev` push; no live-gate artifact |
| `31312735093` | `4d89bea64ce98753895a16194e320c9a8ea45852` | PR #747 | `failure` | `12:13:20Z` → `12:33:49Z` | latest completed run; sole current gate authority |

Both cancellations are supersession by a newer `dev` push, not gate failures.
Neither produced a `cloud-run-dev-validation` artifact, so neither can be cited
as evidence for or against any gap. `4d89bea6` is `origin/dev` at refresh time,
so run `31312735093` is both the latest completed run and the current tip.

Run `31312735093` replaces the earlier `31308339896` at `9e5434cd` as closure
evidence. The blocker set did not change across that move; only its exact-SHA
anchor did.

## 1. Decision

Package 10 is not live-complete. The canonical implementation and API contract
work are materially ahead of the public runtime, but no recent `dev` release
has passed the fail-closed live gate and remained promoted. Public traffic still
serves rollback release `8ec12c02`, and `/platform/health` returns `503`.

The remaining work is not one undifferentiated Human/Ops blocker. It divides
into three classes:

1. work that Supervisor and auto workers can execute now;
2. work that auto workers can execute after a named human input or approval;
3. decisions or source authority that auto workers must never invent.

The machine-dispatchable plan is defined in:

- `docs/evidence/fleet_dispatch/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.md`
- `docs/evidence/fleet_dispatch/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.json`

## 2. Evidence Authority

Use evidence in this order:

1. exact-SHA Deploy Dev artifacts and public runtime readback;
2. the live canonical Supervisor status root;
3. committed Package 10 source archive, page-by-page diff, and task pack;
4. historical implementation ACKs only for the scope and SHA they name.

The following are not current closure evidence:

- chat summaries;
- an uncommitted local audit;
- a static check from an older Package 10 branch;
- a PR `product-e2e-gate` pass without the Deploy Dev live gate;
- a candidate revision that was rolled back;
- worker `succeeded` status without durable API and PG16 readback.

### 2.1 Exact live-gate result at `4d89bea6`

Source: artifact `cloud-run-dev-validation` (`live-e2e-gate.json`) from run
`31312735093`. `ok=false`, `schema_version=1`, 50 checks, 43 passed, 7 failed,
`blocking_dependencies = [external-data, mlflow]`.

The seven blockers, verbatim:

| Check | Dependency | Detail |
|---|---|---|
| `runtime:model_bindings` | `mlflow` | `mode=mlflow-production-unverified ready=False autoSeeded=False error=forecastops: PRODUCTION_MODEL_REGISTRY_UNAVAILABLE: forecast_revenue_interval: configured MLflow registry has no production alias` |
| `runtime:model_capability:forecastops` | `mlflow` | `available=False reasonCode=PRODUCTION_MODEL_REGISTRY_UNAVAILABLE` |
| `models:registry` | `mlflow` | `versions=0` |
| `models:forecastops:production_alias` | `mlflow` | `model=forecast_revenue_interval versionsWithProductionAlias=0 (exactly one required)` |
| `data:ingestion_runs` | `external-data` | `runs=0` |
| `data:admin_boundary.official_dataset:run_exists` | `external-data` | `no persisted ingestion run for a required live provider` |
| `data:poi.commercial_api:run_exists` | `external-data` | `no persisted ingestion run for a required live provider` |

The 43 passes, by dependency group:

| Dependency | Result | Passing checks |
|---|---|---|
| `config` | 11/11 | `api_url`, `web_url`, `expected_sha`, `operator_credential`, `operator_role`, `required_providers`, `provider_registry_known`, `snapshot_providers`, `worker_probe_provider`, `expected_deployment`, `worker_polling` |
| `release` | 1/1 | `release:platform_version` (`expected=actual=4d89bea6…`) |
| `data-binding` | 6/6 | `no_surrogate_markers` on release, runtime, models, audit, and data, plus `auth:operator_bootstrap:provenance` (`data_mode=live data_source=operator-shell-production surrogatePaths=none`) |
| `api-runtime` | 2/2 | `runtime:readiness` (`status=ok requireLiveData=True deploymentMode=dev`), `runtime:no_blocking_reasons` |
| `postgresql` | 2/2 | `runtime:persistence` (`postgresql durable=True reachable=True`), `runtime:data_origin` (`mode=live origin=authoritative operatorReady=True`) |
| `provider` | 4/4 | `runtime:provider` (`mode=live`), plus authenticated schema-valid probes for `admin_boundary.official_dataset`, `geocode.primary_api`, `poi.commercial_api` |
| `auth` | 3/3 | `anonymous_denied` (`401`), `operator_bootstrap` (`200`), `web_operator_requires_login` (`307 → /login?returnTo=%2Foperator`) |
| `worker` | 5/5 | `enqueue` (`202`), `idempotent_replay` (`sameJob=True created=False`), `drain_trigger`, `terminal_success` (`attempts=1`), `ingestion_probe:poi.commercial_api` |
| `audit` | 3/3 | `durable_receipt`, `idempotent_replay_receipt`, `receipt_integrity` (hash-chained) |
| `mlflow` | 6/10 | `model_capability` for `avm`, `heatzone`, `sitescore` (all `governedDisabled=True reasonCode=DATA_CONTRACT_NOT_MATURE`), and `no_fabricated_alias` for the same three |
| `external-data` | 0/3 | none |

Every other report in the same artifact bundle is `ok=true`, so none of them is
a gate blocker: `cloud-run-preflight.json` (WIF and configuration, `12:17:53Z`),
`cloud-run-smoke.json` (candidate `12:32:07Z`),
`cloud-run-migration-compatibility.json` (`12:26:30Z`), and the three
`cloud-run-jobs/*.json` validations for `migration`, `scheduler`, and `worker`,
each confirming the exact release SHA in image/env/labels and all four required
secret env vars bound with no unselected-provider leakage.

Sequence inside the deploy step: candidate deployed at 0% traffic, promoted to
100% at `12:32:16Z`, live gate run against the promoted release at `12:32:33Z`,
gate failed at `12:33:24Z`, traffic restored to `oday-api-00005-gin=100` and
`oday-web-00008-ws4=100`, Cloud Scheduler triggers restored, step exit code `1`.
The release therefore reached public traffic briefly and was withdrawn by the
fail-closed gate; it was never a retained promotion.

`cloud-run-migration-compatibility.json` (`12:26:30Z`) supplies an
artifact-backed public readback: the pre-deploy `oday-api` reported
`release_sha=8ec12c02` with `/platform/version` `200` and `/platform/health`
`503`. This closes the one claim the prior verification recorded as not
reproducible from a committed artifact.

## 3. Confirmed Complete Foundations

These items do not need to be reimplemented:

| Foundation | Current evidence | Closure boundary |
|---|---|---|
| Canonical Package 10 source archive | ZIP manifest and extracted HTML under `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/` | Workers must continue receiving immutable source hashes |
| Page-by-page design diff | `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md` | It is the visual contract, not a live pass |
| Source-doc materialization | `ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001` was merged and archived | Missing or hash-mismatched sources still fail closed |
| GCP/WIF deployment authentication | Run `31312735093` passed WIF preflight (`cloud-run-preflight.json`, `ok=true`) and Google Cloud authentication | Authentication success does not imply release success |
| Cloud Run job release binding | `migration`, `scheduler`, and `worker` job validations in run `31312735093` are all `ok=true` with the exact SHA in image/env/labels and all four required secrets bound | Correct job binding does not imply the job persisted anything |
| Operator smoke RBAC | `operations_manager,model_owner,data_owner`; anonymous request denied; authenticated bootstrap `200` | The public promoted release is still old |
| Candidate Operator provenance | Candidate bootstrap reported `data_mode=live`, `data_source=operator-shell-production`, no surrogate path | Must be repeated on the final promoted SHA |
| Provider connectivity | Admin boundary, geocode, and POI connectivity/auth/schema probes passed | Connectivity does not prove persisted ingestion |
| PG16 reachability | Persistence reported PostgreSQL, durable, reachable | Write/read tenant and schema equality remains unproven for ingestion runs |

## 4. Detailed Gap Register

### GAP-01 - Exact `dev` release is not promoted

- Severity: `P0`
- State: `OPEN`
- Evidence:
  - latest completed run `31312735093` at `4d89bea6…` failed in `Build, push, deploy, and verify Cloud Run`;
  - the candidate was promoted to 100% traffic, failed the live gate, and was rolled back to `oday-api-00005-gin` / `oday-web-00008-ws4`;
  - the two preceding `dev` runs, `31311664947` and `31312411417`, were cancelled as superseded and produced no gate artifact;
  - public `/platform/version` reports `8ec12c02` (artifact readback `12:26:28Z`);
  - public `/platform/health` returns `503` (artifact readback `12:26:29Z`).
- Impact: no Package 10 code or API claim can be called live-complete.
- Automation class: `AUTO_AFTER_DEPENDENCY`.
- Auto worker can:
  - deploy the exact merged SHA;
  - prove Cloud Run API/Web revision labels and traffic;
  - run authenticated API, worker, audit, and rollback gates;
  - preserve a complete redacted receipt.
- Human-only boundary:
  - approve a release-risk waiver, if the organization chooses to waive a gate;
  - such a waiver is not authorized by this plan.
- Execution task: existing `ODP-P10-DEV-REDEPLOY-VERIFY-001`, to be corrected and resumed only after GAP-02 and GAP-04 close.

### GAP-02 - Required provider jobs succeed but no ingestion run is readable

- Severity: `P0`
- State: `OPEN_DIAGNOSIS_REQUIRED`
- Evidence from run `31312735093`:
  - `worker:enqueue` passed (`202`, job `9a03dcdc-2402-4731-b434-7b22ac2c224c`, type `external-fetch`);
  - `worker:idempotent_replay` passed (`sameJob=True created=False`);
  - worker drain and terminal success passed (`terminal_status=succeeded`, `attempts=1`);
  - `worker:ingestion_probe:poi.commercial_api` passed;
  - all three `audit:*` receipt checks passed, so the enqueue is durably recorded;
  - `GET /api/v1/external-data/ingestion-runs` returned `runs=0`;
  - no persisted run existed for `admin_boundary.official_dataset` or `poi.commercial_api`.
- Refresh note: the worker Cloud Run job validation in the same run reports the
  exact release SHA and all four required secret env vars bound, including
  `ODAY_DATABASE_URL`, so the contradiction is not an unbound-credential or
  stale-image case. The diagnosis in `ODP-P10-LIVE-EXTDATA-DIAG-001` still owns
  the root cause.
- Impact: the release cannot prove authentic external data, snapshots, DQ, freshness, or lineage.
- Automation class: `AUTO_NOW`, followed by `CONDITIONAL_AUTO_REMEDIATION`.
- Auto worker can:
  - inspect exact worker execution logs and job payloads;
  - compare API and worker release SHA, Secret Manager PG16 binding, tenant, schema, and store factory;
  - query redacted PG16 counts and lineage using read-only supported tools;
  - invoke the supported scheduled or authenticated ingestion path;
  - implement a scoped code/config fix only in a separate remediation task after diagnosis.
- Auto worker must not:
  - insert rows directly into PG16;
  - mark an empty or failed provider run successful;
  - create fixture, synthetic, placeholder, or invented lineage;
  - assume this is only missing data before write/read equality is proven.
- Execution tasks:
  - `ODP-P10-LIVE-EXTDATA-DIAG-001`;
  - conditional `ODP-P10-LIVE-EXTDATA-REMEDIATE-001`.

### GAP-03 - ForecastOps authoritative daily history is insufficient or not handed back

- Severity: `P0`
- State: `BLOCKED_ON_SOURCE_AUTHORITY`
- Existing task: `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001`.
- Known prior evidence: 1,303 eligible rows cover only four calendar days, which cannot form canonical 7/14/28-day per-store windows.
- Impact: no governed ForecastOps production candidate can be trained and evaluated.
- Automation class: `HYBRID_HUMAN_GATE`.
- Auto worker can:
  - inventory every accessible authoritative transaction source;
  - report tenant/store/date/lineage coverage and exact missing intervals;
  - ingest an already authorized immutable source through the governed pipeline;
  - verify point-in-time safety, horizon completeness, row counts, and hashes;
  - train and evaluate after sufficient history exists.
- Human/Ops must:
  - identify or supply the authoritative source when it is not already accessible;
  - attest source ownership, permitted use, completeness, and cutoff;
  - resolve discrepancies that require business authority.
- Auto worker must not create synthetic dates, repeat four days across a longer range, or relabel immature rows as eligible.

### GAP-04 - ForecastOps has no governed MLflow production alias

- Severity: `P0`
- State: `BLOCKED_ON_GAP-03_AND_HUMAN_APPROVAL`
- Existing task: `ODP-PRODUCTION-MODEL-REGISTRY-001`.
- Exact candidate gate evidence from run `31312735093`:
  - registry `versions=0`;
  - `forecast_revenue_interval` has zero versions with the `production` alias, where exactly one is required;
  - ForecastOps reports `PRODUCTION_MODEL_REGISTRY_UNAVAILABLE`;
  - `runtime:model_bindings` reports `mode=mlflow-production-unverified ready=False autoSeeded=False`, so nothing was auto-seeded to mask the gap;
  - `avm`, `heatzone`, and `sitescore` remain `governedDisabled=True` with `no_fabricated_alias` passing, so ForecastOps is the only model blocker.
- Impact: model binding and platform live readiness fail closed.
- Automation class: `HYBRID_HUMAN_GATE`.
- Auto worker can:
  - train from authentic eligible history;
  - generate evaluation, temporal validation, model card, lineage, rollback candidate, and shadow evidence;
  - register a candidate version;
  - move the alias only after a durable approval record authorizes the exact version.
- Human model/data owner must:
  - approve the exact candidate version and risk/quality evidence;
  - reject it when business or model risk is unacceptable.
- Auto worker must not self-approve, invent a model version, set an empty alias, or waive thresholds.

### GAP-05 - Candidate API binding is proven, public API binding is not

- Severity: `P0`
- State: `PARTIAL`
- Candidate evidence from run `31312735093`:
  - authenticated Operator bootstrap `200`;
  - anonymous request denied (`401`);
  - Web `/operator` requires login (`307 → /login?returnTo=%2Foperator`);
  - live provenance (`data_mode=live`, `data_source=operator-shell-production`) and no surrogate path on any of the six `data-binding` checks.
- Missing evidence:
  - the same checks on the final promoted public SHA;
  - durable data readback after provider ingestion and model binding close;
  - browser-to-API correlation for every Package 10 workspace.
- Automation class: `AUTO_AFTER_DEPENDENCY`.
- Execution task: `ODP-P10-DEV-REDEPLOY-VERIFY-001` plus `ODP-P10-LIVE-VISUAL-PARITY-001`.

### GAP-06 - Forty Package 10 screen contracts lack final live visual proof

- Severity: `P0`
- State: `OPEN`
- Historical source: `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md`.
- Missing evidence:
  - authenticated screenshots on the exact promoted release;
  - all three viewports required by `delivery_toolchain/e2e/check_product_grade_ci_gates.py`, `390`, `1024`, and `1440`;
  - loading, empty, success, partial, blocked, retryable, permission-limited, and durable-return states;
  - browser console/network errors and API provenance per page;
  - comparison against the archived Package 10 HTML.
- Automation class: `AUTO_AFTER_DEPENDENCY`.
- Auto worker can execute Playwright/agent-browser checks, screenshots, DOM assertions, accessibility checks, and API correlation.
- Human design/product owner must decide only genuine visual ambiguity or approve an intentional deviation. A worker may not silently redefine the design.
- Execution task: `ODP-P10-LIVE-VISUAL-PARITY-001`.

### GAP-07 - Legacy visual retirement is not verified on the final release

- Severity: `P0`
- State: `HISTORICAL_PASS_CURRENTLY_STALE`
- Historical evidence:
  - 117 retired paths were absent at `435c79e3` on 2026-07-26;
  - the evidence itself states `Release status: NO-GO`.
- Missing evidence:
  - static inventory on current `origin/dev`;
  - HTTP/redirect behavior on the final promoted SHA;
  - no retired selector/component/import can execute through current route or chunk graphs;
  - no alternate intake detail or old OpsBoard shell resurfaces.
- Automation class: `AUTO_AFTER_DEPENDENCY` for final runtime proof; the current-dev static half can run now.
- Execution task: `ODP-P10-LIVE-LEGACY-RETIREMENT-001`.

### GAP-08 - Fleet task truth is incomplete and stale

- Severity: `P0_CONTROL_PLANE`
- State: `OPEN`
- Evidence, re-checked against the live canonical status root at refresh time:
  - `ODP-P10-DEV-REDEPLOY-VERIFY-001` is still `blocked` with `last_update` `2026-08-05T11:43:03Z` and a `next` field containing only auto-reassignment text, not current runtime blockers;
  - the previously created provider-ingestion task is absent from current canonical active and archive state;
  - the Supervisor process is healthy, but the audit snapshot showed zero active and zero queued workers while actionable work remained;
  - historical Package 10 implementation ledgers still say `NO-GO` for old R3 checkpoints and must not be redispatched as if implementation restarted.
- Impact: valid work may not be picked up, and stale instructions may cause duplicate or conflicting implementation.
- Automation class: `AUTO_NOW`.
- Execution task: `ODP-P10-LIVE-FLEET-STATE-REPAIR-001`.
- Human-only boundary: none unless the status store cannot be repaired without destructive recovery; destructive recovery requires explicit operator approval.

### GAP-09 - Live staging, UAT, and final release acceptance are not closed

- Severity: `P0_RELEASE`
- State: `OPEN_AFTER_RUNTIME`
- Existing tasks:
  - `ODP-PLAN-LIVE-STAGING-PROOF-001`;
  - `ODP-PLAN-UAT-SIGNOFF-001`;
  - `ODP-PLAN-FINAL-GATE-AUDIT-001`.
- Automation class: `AUTO_EVIDENCE_PLUS_HUMAN_SIGNOFF`.
- Auto worker can prepare the complete evidence packet, run deterministic acceptance checks, and identify every exception.
- Human UAT/product/release owner must approve business acceptance and release risk. Workers cannot sign on behalf of accountable humans.

## 5. Automation Classification

### 5.1 Supervisor/auto worker can execute now

1. Repair the canonical Package 10 Fleet task graph and stale task metadata.
2. Diagnose the external-data worker-success/API-zero contradiction.
3. Run current-dev static checks for the 117 retired paths and old selectors.
4. Inventory existing authoritative ForecastOps sources and produce an exact missing-data handback.
5. Collect latest exact-SHA Deploy Dev artifacts and redacted Cloud Logging.
6. Verify source-doc manifests and prevent old Package 10 ledgers from being redispatched as new implementation work.

### 5.2 Auto worker can execute after dependencies or approval

1. Ingest real provider snapshots after write/read identity is proven.
2. Implement a scoped ingestion persistence/config fix after diagnosis and independent review.
3. Backfill ForecastOps history after an authoritative source and use rights are established.
4. Train, evaluate, register, and shadow a ForecastOps candidate.
5. Move the MLflow production alias after exact-version human approval.
6. Deploy exact `dev`, validate, and retain promotion only after all live gates pass.
7. Run authenticated 40-screen visual/API/a11y verification and 117-path runtime retirement checks.
8. Build the UAT and final release packets.

### 5.3 Auto worker cannot do

1. Invent, synthesize, duplicate, or relabel missing authoritative business history.
2. Attest data ownership, licensing, provider terms, or source permission without an accountable human authority.
3. Self-approve a production model, business KPI threshold, design deviation, UAT, or final release risk.
4. Waive a fail-closed deployment, security, tenant, source-policy, audit, or lineage gate.
5. Directly insert or rewrite production PG16 records to make a gate green.
6. Perform destructive recovery, IAM escalation, billing/quota acceptance, or secret-policy changes without explicit authorization.
7. Use a successful candidate or static old-head audit as proof that the public release is complete.

## 6. Other-LLM Conflict Register

| Observed claim or action | Conflict | Required resolution |
|---|---|---|
| External-data is only a missing-data problem | Worker and provider probes succeeded while the API still read zero runs | Diagnose store/tenant/schema/write-read identity before choosing runtime-only or code/config remediation |
| Public `8ec12c02` health payload defines current exact-dev model requirements | It is an old rollback release and may contain older readiness logic | Use exact candidate Deploy Dev artifact for current blockers; use public payload only for shared environment facts |
| Four model aliases are definitely the current deploy blocker | Latest exact candidate gate names ForecastOps; the old public release exposes broader legacy requirements | Keep AVM/HeatZone/SiteScore governed-disabled unless current exact-dev acceptance explicitly requires them |
| Historical R3 implementation pack should be restarted | Current code and later merges already contain those implementation waves | Treat the 7/26 pack as historical source/retirement authority; dispatch only live-closure tasks in the 8/9 pack |
| A reassignment message is an adequate blocked-task next step | It omits actionable dependency and evidence | Replace it with exact blocker, dependency, run, SHA, and resume condition |
| Worker terminal success proves ingestion | API and durable store returned zero records | Require durable PG16 plus authenticated API readback and lineage |
| A cancelled Deploy Dev run is a gate failure or a new blocker | `31311664947` and `31312411417` were superseded by the next `dev` push and emitted no `cloud-run-dev-validation` artifact | Cite only runs that reached the live gate; a cancelled run is neither evidence of failure nor of pass |
| The dev merge burst changed the Package 10 blocker set | The blocker set at `4d89bea6` is identical to the one at `9e5434cd`: same two dependencies, same seven checks | Re-anchor evidence to the newest exact SHA without reopening or re-scoping any gap |

Any new LLM output that overlaps a writable path, changes a dependency, merges
`dev`, starts a Deploy Dev run, changes a model alias, or mutates provider data
must be compared to this graph before dispatch. On conflict, the worker stops,
records the exact task/PR/SHA/path overlap, and returns control to the
coordinator.

## 7. Release Exit Criteria

Package 10 may be called complete only when all of the following are true on one
exact release SHA:

1. `origin/dev`, deployed API, deployed Web, image labels, and public version agree.
2. Deploy Dev completes successfully and does not roll back.
3. Required provider ingestion runs are authentic, non-empty, lineage-complete, and readable through the Operator API.
4. ForecastOps resolves exactly one approved MLflow `production` alias and inference smoke passes.
5. Operator bootstrap is authenticated, live, non-placeholder, tenant-correct, and fail-closed for invalid access.
6. All 40 Package 10 screen contracts pass at all three required viewports, `390`, `1024`, and `1440`, with screenshots and API correlation.
7. All 117 retired paths remain absent or redirect to the canonical shell, and no retired implementation is executable.
8. Accessibility, console, network, audit, rollback, and durable deep-link checks pass.
9. Independent worker review passes on the exact evidence head.
10. Human UAT/model/release approvals required by policy identify the exact SHA/version and are durably recorded.
