# ODP-P10-DEV-REDEPLOY-VERIFY-001 runtime evidence

- Task: `ODP-P10-DEV-REDEPLOY-VERIFY-001`
- Owner: Antigravity3 (current); Claude2 (run 30362772798)
- Independent reviewer: Codex6
- Result: **BLOCKED — deploy runs have failed. Package 10 Operator runtime
  parity is not proven and must not be claimed.**

**Run history:**

| Run | SHA | Trigger | Result | Failure |
|---|---|---|---|---|
| [30362772798](https://github.com/alfloop-dev/odayplus/actions/runs/30362772798) | `450c7fadd` (PR #474) | push | failure | `gcloud run jobs executions describe-latest` rejected by runner SDK |
| [30376737123](https://github.com/alfloop-dev/odayplus/actions/runs/30376737123) | `dda726155a` (PR #479) | push | failure | `jobs-smoke:migration:secret_bindings` fail-closed |
| [30402570022](https://github.com/alfloop-dev/odayplus/actions/runs/30402570022) | `7d13f8e162` (PR #484) | push | failure | `migration-compatibility-smoke` probe timeout (`/platform/version` & `/platform/health`) |
| [30412416116](https://github.com/alfloop-dev/odayplus/actions/runs/30412416116) | `79cf9b67e6` (PR #488) | push | failure | `worker Cloud Run Job` execution failure (`oday-worker-r-79cf9b67e62c-6fhw5`) |
| [30680943677](https://github.com/alfloop-dev/odayplus/actions/runs/30680943677) | `97e3ae2e26` (dev) | push | failure | Candidate revision smoke fail-closed (`/platform/health` & `/readiness` 503, operator bootstrap degraded data_mode/provenance, forecastops unverified model bindings) |


This directory is evidence only. Per the task conflict gate, no product code,
deploy script, workflow, Package 10 archive, or retired path was modified.

## 1. Deploy Dev on the exact merged SHA

`Deploy Dev` run [`30362772798`](https://github.com/alfloop-dev/odayplus/actions/runs/30362772798)
was triggered by the `push` of the merge commit itself at `2026-07-28T13:17:11Z`,
so it ran on exactly the required SHA — no re-dispatch or re-tag was needed.

| Field | Value |
|---|---|
| Head branch / SHA | `dev` / `450c7faddda32155fadce6c36cfdeed623a385a3` |
| Event | `push` |
| `e2e-operational-evidence` job | success |
| `deploy` job | **failure** at step 13 |
| Completed | `2026-07-28T13:30:27Z` |

Full step-by-step receipt: `deploy-run-30362772798.json`.

### The dependency fix did hold

Everything `ODP-DEPLOY-SCRIPT-LOCKED-PYTHON-001` was created to fix now passes
in the real workflow. `scripts/deploy_cloud_run_waji.sh`'s own internal preflight
ran under the locked environment and passed:

```text
Running fail-closed live deployment preflight...
Cloud Run live deployment preflight passed.
report=.odp_data/deployment/cloud-run-preflight.json
```

The uploaded report (`cloud-run-preflight.json`, taken verbatim from the run's
`cloud-run-dev-validation` artifact) records `ok: true`, `release_sha:
450c7faddda32155fadce6c36cfdeed623a385a3`, **72 checks, 0 failures**, including
the two that failed in run `30331484524`:

- `repository:provider_registry_import` — now passes (was `ModuleNotFoundError: httpx`)
- `repository:operator_bootstrap_data_source` — now passes (was `ModuleNotFoundError: pydantic`)

The three images the script builds before this point — `oday-api`,
`oday-worker`, `oday-scheduler`, all tagged `dev-450c7faddda32155fadce6c36cfdeed623a385a3`
— were built, pushed, cosign-signed, and cosign-verified (3× `Verification
PASSED`). The `oday-web` image is built later in the script and was never
reached.

### Where it failed

The deploy died at the first Cloud Run Job proof capture, immediately after the
migration job had already executed successfully:

```text
Execution [oday-migration-r-450c7faddda3-8bwjd] has successfully completed.
ERROR: (gcloud.run.jobs.executions) Invalid choice: 'describe-latest'.
Deployment failed; restoring the recorded API/Web traffic split.
##[error]Process completed with exit code 2.
```

Verbatim excerpt: `deploy-failure-excerpt.log`.

The offending call sites are `scripts/deploy_cloud_run_waji.sh:284`
(`capture_job_proof`) and `:308` (`execute_job` failure path). This is an
environment-portability defect, not a product defect: the worker host's Cloud
SDK 577.0.0 *does* expose `gcloud run jobs executions describe-latest`, while
the SDK that `google-github-actions/setup-gcloud@v2` (`version: latest`)
provided on the runner does not, so the subcommand is rejected outright. The
locked-Python task's shell harness stubs `gcloud`, so no existing test exercises
this argv against a real SDK.

`scripts/**` is a forbidden path for this task, so it was not patched. See
§5 for the recommended remediation task.

## 2. Cloud Run state after the failure

The armed rollback worked correctly. Because the failure happened before the
API/Web candidate revisions are created, **no `oday-api` or `oday-web` revision
carries `oday-release-sha=450c7faddda32155fadce6c36cfdeed623a385a3`**, and
traffic is still on the pre-run release:

| Service | Serving revision (100%) | Release SHA label |
|---|---|---|
| `oday-api` | `oday-api-00005-gin` (created 2026-07-25) | none |
| `oday-web` | `oday-web-00008-ws4` (created 2026-07-25) | none |

Full snapshot: `cloud-run-post-rollback-state.json`.

The one runtime surface that *did* reach the target release is the migration
Cloud Run Job `oday-migration-r-450c7faddda3`, labelled
`oday-release-sha=450c7faddda32155fadce6c36cfdeed623a385a3`, whose execution
`oday-migration-r-450c7faddda3-8bwjd` emitted a structured success receipt
(`migration-job-receipt.json`):

```json
{"release_sha": "450c7fadd...", "status": "succeeded", "returncode": 0,
 "runtime_schema_verified": true, "checksum_status": "verified",
 "target_revision": "head", "environment": "dev"}
```

**Risk to hand to the remediation owner:** the dev database was migrated to the
new release head while API/Web traffic was rolled back to the older revisions.
The receipt reports `runtime_schema_verified: true` and the migration is
checksum-verified and idempotent, but the serving API image is now behind the
schema it talks to. Re-running a successful deploy closes this; leaving dev in
this state indefinitely should not be assumed safe.

## 3. Acceptance status

| # | Acceptance criterion | Status | Basis |
|---|---|---|---|
| 1 | Deploy Dev runs from the exact merged `origin/dev` SHA and completes successfully | **FAIL** | Ran on the exact SHA (`450c7fadd...`), but concluded `failure`; `deploy-run-30362772798.json` |
| 2 | Cloud Run API and web revisions report the deployed release SHA | **FAIL** | No revision carries the label; `cloud-run-post-rollback-state.json` |
| 3 | Operator API returns live non-placeholder data and fails closed on invalid access | **NOT REACHED** | The release-aware smoke gate and live E2E gate never ran; the release never served traffic |
| 4 | `/operator` leaves loading state and renders the Package 10 canonical shell at desktop and mobile | **NOT REACHED** | Same; probing the old serving revision would prove nothing about this SHA and is not offered as evidence |
| 5 | All 40 Package 10 screen contracts and 117 retired visual paths remain verified | **PASS (source scope)** | `package10-contract-verification.txt`: 40/40 labels, 117 retired paths, 0 survivors, 3 executable pages, both canonical hashes match |
| 6 | Independent Codex6 evidence review and CI pass before closeout | **PENDING** | Requires this evidence PR |

Criterion 5 is the only one this task can honestly close right now, and only at
source scope — it says the tree at `450c7fadd...` still satisfies the Package 10
contract, not that a runtime serves it.

## 4. Preserved worker dispatch receipts

Per the `2026-07-28T13:26:24Z` reassignment note, both failed Antigravity3
dispatches are preserved verbatim in `worker-dispatch-receipts.json`, sourced
from the Supervisor status root `/tmp/oday-plus-supervisor-live-20260726`:

| Worker run id | Started | Finished | Exit | Reported cause |
|---|---|---|---|---|
| `antigravity3-20260728T132329Z-eae36591` | `13:23:29Z` | `13:24:01Z` | 1 | Individual quota reached, resets in 4h14m58s (Gemini default) |
| `antigravity3-20260728T132453Z-b59ed3ae` | `13:24:53Z` | `13:25:03Z` | 1 | Individual quota reached, resets in 2h38m16s (`--model "Claude Sonnet 4.6 (Thinking)"` fallback) |

Both failed on provider quota before any task work started, followed by
`provider_dispatch_paused` for `antigravity3` until `13:40:59Z`. Ownership then
moved to Claude2. The file also carries the 9 matching Supervisor
`ai-activity-log.jsonl` events; no synthetic run id or derived timestamp is used.

## 5. Recommended remediation task (not performed here)

A separate task is required because `scripts/**` is forbidden in this scope:

- **Scope:** make the Cloud Run Job execution proof portable across Cloud SDK
  versions. Replace both `gcloud run jobs executions describe-latest` call sites
  (`scripts/deploy_cloud_run_waji.sh:284`, `:308`) with a form present in every
  supported SDK — e.g. `gcloud run jobs executions list --job=... --sort-by=~metadata.creationTimestamp --limit=1 --format=json`
  reduced to the single execution object that
  `validate_cloud_run_live_deployment.py jobs-smoke` expects — or pin
  `setup-gcloud` to a version known to expose `describe-latest`.
- **Regression guard:** the current harness in
  `tests/ops/test_cloud_run_live_deployment.py` stubs `gcloud`, so it accepts any
  argv. The remediation should assert the emitted argv against a real
  `gcloud ... --help` surface, otherwise the same class of defect recurs.
- **Also fix:** step 13's `--wait` failure path writes job proofs into a
  `JOB_REPORT_DIR` subdirectory, but the workflow uploads only
  `.odp_data/deployment/*.json` (non-recursive), so the job smoke reports were
  not retrievable from this run's artifact.
- **Then:** re-dispatch this task to re-run Deploy Dev on the then-current
  `origin/dev` and complete acceptance items 1–4.

## 6. Verification commands

```text
git fetch origin dev --prune
git rev-parse origin/dev                              # 450c7faddda32155fadce6c36cfdeed623a385a3
gh run view 30362772798 -R alfloop-dev/odayplus --json status,conclusion,jobs
gh api repos/alfloop-dev/odayplus/actions/jobs/90287054224/logs
gh run download 30362772798 -R alfloop-dev/odayplus -n cloud-run-dev-validation
gcloud run services describe oday-api --region asia-east1 --project alfaloop-data-project --format='json(status.traffic)'
gcloud run services describe oday-web --region asia-east1 --project alfaloop-data-project --format='json(status.traffic)'
gcloud run revisions list --service oday-api --region asia-east1 --project alfaloop-data-project --format='table(metadata.name,metadata.labels.oday-release-sha)'
gcloud run revisions list --service oday-web --region asia-east1 --project alfaloop-data-project --format='table(metadata.name,metadata.labels.oday-release-sha)'
gcloud run jobs describe oday-migration-r-450c7faddda3 --region asia-east1 --project alfaloop-data-project --format=json
gcloud run jobs executions list --job oday-migration-r-450c7faddda3 --region asia-east1 --project alfaloop-data-project
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="oday-migration-r-450c7faddda3"' --project alfaloop-data-project
python3 scripts/e2e/check_product_grade_ci_gates.py --report
git ls-tree -r --name-only origin/dev                 # retired-path survivor diff
```

## 7. Files

| File | Contents |
|---|---|
| `README.md` | this report |
| `deploy-run-30362772798.json` | full job/step receipt for the failed Deploy Dev run (SHA 450c7fadd) |
| `deploy-failure-excerpt.log` | verbatim ANSI-stripped log excerpts: preflight, migration job, failure, rollback (run 30362772798) |
| `cloud-run-preflight.json` | unmodified `cloud-run-dev-validation` artifact from run 30362772798 |
| `cloud-run-post-rollback-state.json` | `oday-api` / `oday-web` traffic, revisions, and release-SHA labels after rollback (run 30362772798) |
| `migration-job-receipt.json` | structured migration receipt at SHA 450c7fadd |
| `worker-dispatch-receipts.json` | the two preserved Antigravity3 quota failures plus Supervisor events |
| `package10-contract-verification.txt` | 40-screen contract and 117-retired-path re-verification at `origin/dev` |
| `deploy-run-30376737123.json` | full job/step receipt for Deploy Dev run 30376737123 (SHA dda726155a) |
| `deploy-failure-excerpt-run-30376737123.log` | verbatim log excerpts: preflight, images, migration execution, smoke fail, rollback |
| `cloud-run-preflight-run-30376737123.json` | unmodified `cloud-run-dev-validation` artifact from run 30376737123 |
| `cloud-run-post-rollback-state-run-30376737123.json` | Cloud Run traffic state after rollback, reconstructed from logs |

---

## 8. Deploy Dev run 30376737123 (SHA dda726155a, PR #479)

`Deploy Dev` run [`30376737123`](https://github.com/alfloop-dev/odayplus/actions/runs/30376737123)
was triggered by the `push` of the merge commit of PR #479
(`ODP-DEPLOY-CLOUD-RUN-JOB-EXECUTION-COMPAT-001`) at `2026-07-28T16:07:28Z`,
running on exactly the required SHA — no re-dispatch needed.

| Field | Value |
|---|---|
| Head branch / SHA | `dev` / `dda726155a399487474ae148b4dc1c3294ea9463` |
| Event | `push` |
| `e2e-operational-evidence` job | **success** |
| `deploy` job | **failure** at step 13 |
| Started | `2026-07-28T16:07:28Z` |
| Completed | `2026-07-28T16:21:05Z` |

Full step-by-step receipt: `deploy-run-30376737123.json`.

### What changed from the previous run

`ODP-DEPLOY-CLOUD-RUN-JOB-EXECUTION-COMPAT-001` replaced the
`gcloud run jobs executions describe-latest` call sites in
`scripts/deploy_cloud_run_waji.sh`. As a result the script progressed
significantly further than run `30362772798`:

- Preflight: 72 checks, 0 failures (`ok: true`, `release_sha: dda726155a...`)
- Images built, pushed, cosign-signed, cosign-verified: `oday-api`, `oday-worker`,
  `oday-scheduler` (3× Verification PASSED)
- Traffic snapshot recorded
- Migration Cloud Run Job `oday-migration-r-dda726155a39` deployed and executed:
  `Execution [oday-migration-r-dda726155a39-ndb4l] has successfully completed.`

### Where run 30376737123 failed

The deploy failed at the migration job smoke validator, immediately after execution
confirmed success:

```text
Cloud Run migration Job smoke failed (fail-closed):
- jobs-smoke:migration:secret_bindings: database and provider secret environment bindings are configured
report=.odp_data/deployment/cloud-run-jobs/migration-validation.json
Deployment failed; restoring the recorded API/Web traffic split.
##[error]Process completed with exit code 1.
```

Verbatim excerpt: `deploy-failure-excerpt-run-30376737123.log`.

The failing check is `jobs-smoke:migration:secret_bindings`. This is a
fail-closed smoke gate that validates the migration job's secret environment
bindings after execution. The migration job itself ran to completion, but
the post-execution smoke report (`cloud-run-jobs/migration-validation.json`)
did not pass the binding verification.

**Note on artifact availability:** Step 14 (`Upload deployment validation reports`)
successfully ran and uploaded `.odp_data/deployment/*.json`. However the job smoke
report path is `.odp_data/deployment/cloud-run-jobs/migration-validation.json` —
a subdirectory not matched by the non-recursive `*.json` glob. This means the
failing report was NOT uploaded to the artifact. Only `cloud-run-preflight.json`
is available in the artifact (`cloud-run-dev-validation`). This is the same
artifact-upload gap noted in §5 for the previous run.

`scripts/**` is a forbidden path for this task. See §9 for the recommended
remediation task.

## 9. Cloud Run state after run 30376737123 rollback

The rollback worked correctly. Because the failure happened before the API/Web
candidate revisions are created, **no `oday-api` or `oday-web` revision carries
`oday-release-sha=dda726155a399487474ae148b4dc1c3294ea9463`**, and traffic
remains on the pre-run release:

| Service | Serving revision (100%) | Release SHA label |
|---|---|---|
| `oday-api` | `oday-api-00005-gin` | none (pre-run) |
| `oday-web` | `oday-web-00008-ws4` | none (pre-run) |

Reconstructed from log (gcloud not scoped on this host): `cloud-run-post-rollback-state-run-30376737123.json`.

The migration Cloud Run Job `oday-migration-r-dda726155a39` executed
successfully at the target release SHA. As with the previous run, the dev
database was migrated to `dda726155a` while API/Web traffic serves the
pre-run schema revision.

## 10. Acceptance status (run 30376737123)

| # | Acceptance criterion | Status | Basis |
|---|---|---|---|
| 1 | Deploy Dev runs from exact merged `origin/dev` SHA and completes successfully | **FAIL** | Ran on `dda726155a`; concluded `failure`; `deploy-run-30376737123.json` |
| 2 | Cloud Run API and web revisions report the deployed release SHA | **FAIL** | No revision carries the label; `cloud-run-post-rollback-state-run-30376737123.json` |
| 3 | Operator API returns live non-placeholder data and fails closed on invalid access | **NOT REACHED** | Release never served traffic |
| 4 | `/operator` leaves loading state and renders Package 10 canonical shell at desktop and mobile | **NOT REACHED** | Same |
| 5 | All 40 Package 10 screen contracts and 117 retired visual paths remain verified | **PASS (source scope)** | `package10-contract-verification.txt` (unchanged, applies to both SHA) |
| 6 | Independent Codex6 evidence review and CI pass before closeout | **PENDING** | Requires this evidence PR |

## 11. Recommended remediation tasks (not performed here)

### 11a. Migration job smoke binding failure (NEW)

A separate task is required:

- **Scope:** investigate why `jobs-smoke:migration:secret_bindings` fails
  fail-closed. The migration job executed successfully
  (`oday-migration-r-dda726155a39-ndb4l`), so the job definition was deployed,
  but the smoke validator rejected the binding configuration in its post-execution
  report. The smoke report (`cloud-run-jobs/migration-validation.json`) was not
  retrievable due to the artifact glob being non-recursive.
- **Also fix:** the artifact upload path (`if-no-files-found: ignore`,
  `.odp_data/deployment/*.json`) must be made recursive or explicitly include
  `cloud-run-jobs/*.json` so smoke reports are recoverable.
- **Then:** once the smoke check passes, re-dispatch this task.

### 11b. gcloud SDK compat (resolved by ODP-DEPLOY-CLOUD-RUN-JOB-EXECUTION-COMPAT-001)

Confirmed resolved: run 30376737123 passed the `describe-latest` call sites
and progressed to post-execution smoke. No further action on this defect.

## 12. Verification commands (run 30376737123)

```text
git fetch origin dev --prune
git rev-parse origin/dev                              # dda726155a399487474ae148b4dc1c3294ea9463
gh run view 30376737123 -R alfloop-dev/odayplus --json status,conclusion,jobs
gh api repos/alfloop-dev/odayplus/actions/jobs/90335080135/logs
gh run download 30376737123 -R alfloop-dev/odayplus -n cloud-run-dev-validation
gcloud run services describe oday-api --region asia-east1 --project alfaloop-data-project --format='json(status.traffic)'
gcloud run services describe oday-web --region asia-east1 --project alfaloop-data-project --format='json(status.traffic)'
gcloud run jobs describe oday-migration-r-dda726155a39 --region asia-east1 --project alfaloop-data-project --format=json
gcloud run jobs executions list --job oday-migration-r-dda726155a39 --region asia-east1 --project alfaloop-data-project
```

---

## 13. Deploy Dev run 30402570022 (SHA 7d13f8e162, PR #484)

`Deploy Dev` run [`30402570022`](https://github.com/alfloop-dev/odayplus/actions/runs/30402570022)
was triggered by the `push` of the merge commit of PR #484
(`ODP-DEPLOY-JOB-SECRET-BINDING-SELECTION-001`) at `2026-07-28T21:55:00Z`,
running on exactly the required SHA `7d13f8e162d035ad7318d1f659dfa0f2bd85ca65`.

| Field | Value |
|---|---|
| Head branch / SHA | `dev` / `7d13f8e162d035ad7318d1f659dfa0f2bd85ca65` |
| Event | `push` |
| `e2e-operational-evidence` job | **success** |
| `deploy` job | **failure** at step 13 |
| Started | `2026-07-28T21:55:00Z` |
| Completed | `2026-07-28T22:09:21Z` |

Full step-by-step receipt: `deploy-run-30402570022.json`.

### What changed from the previous run

`ODP-DEPLOY-JOB-SECRET-BINDING-SELECTION-001` (PR #484) fixed the migration job secret bindings validation and updated the workflow artifact upload rules to capture validation reports recursively. As a result:

- Preflight: 72 checks, 0 failures (`ok: true`, `release_sha: 7d13f8e162...`)
- Images built, pushed, cosign-signed, cosign-verified: `oday-api`, `oday-worker`, `oday-scheduler` (3× Verification PASSED)
- Migration Cloud Run Job `oday-migration-r-7d13f8e162d0` deployed and executed:
  `Execution [oday-migration-r-7d13f8e162d0-zg2jr] has successfully completed.`
- **Migration Job smoke check: PASSED** (`Cloud Run migration Job smoke passed.`)
- **Artifact upload gap: RESOLVED** (`cloud-run-migration-compatibility.json` captured and uploaded in `cloud-run-dev-validation` artifact).

### Where run 30402570022 failed

The deploy failed during post-migration compatibility smoke testing:

```text
Cloud Run migration compatibility smoke failed (fail-closed):
- compatibility:/platform/version:http: The read operation timed out
- compatibility:/platform/health:database: The read operation timed out
report=.odp_data/deployment/cloud-run-migration-compatibility.json
```

Verbatim excerpt: `deploy-failure-excerpt-run-30402570022.log`.

The failing gate is `validate_cloud_run_live_deployment.py migration-compatibility-smoke`.
It sends HTTP requests to `https://oday-api-7sxbjoeozq-de.a.run.app/platform/version` and `/platform/health` with a 15-second timeout to verify that the currently serving API revision remains compatible with the newly migrated database before candidate revisions receive traffic. Both requests timed out (`The read operation timed out`).

## 14. Cloud Run state after run 30402570022 rollback

The automated rollback restored traffic split correctly:

| Service | Serving revision (100%) | Release SHA label |
|---|---|---|
| `oday-api` | `oday-api-00005-gin` | none (pre-run) |
| `oday-web` | `oday-web-00008-ws4` | none (pre-run) |

Snapshot receipt: `cloud-run-post-rollback-state-run-30402570022.json`.

The migration Cloud Run Job `oday-migration-r-7d13f8e162d0` executed successfully at the target release SHA `7d13f8e162`.

## 15. Acceptance status (run 30402570022)

| # | Acceptance criterion | Status | Basis |
|---|---|---|---|
| 1 | Deploy Dev runs from exact merged `origin/dev` SHA and completes successfully | **FAIL** | Ran on `7d13f8e162`; concluded `failure`; `deploy-run-30402570022.json` |
| 2 | Cloud Run API and web revisions report the deployed release SHA | **FAIL** | No revision carries the label; `cloud-run-post-rollback-state-run-30402570022.json` |
| 3 | Operator API returns live non-placeholder data and fails closed on invalid access | **NOT REACHED** | Release candidate never served traffic |
| 4 | `/operator` leaves loading state and renders Package 10 canonical shell at desktop and mobile | **NOT REACHED** | Same |
| 5 | All 40 Package 10 screen contracts and 117 retired visual paths remain verified | **PASS (source scope)** | `package10-contract-verification.txt` (40/40 screen labels, 117 retired paths, 0 survivors) |
| 6 | Independent Codex6 evidence review and CI pass before closeout | **PENDING** | Requires this evidence PR |

## 16. Recommended remediation task

### 16a. Migration compatibility smoke probe timeout (NEW)

A separate remediation task is required:

- **Scope:** investigate why `migration-compatibility-smoke` HTTP requests to `https://oday-api-7sxbjoeozq-de.a.run.app/platform/version` and `/platform/health` time out (15s limit) during post-migration validation. Potential root causes include Cloud Run instance cold starts, database connection pool contention post-migration, or probe timeout parameter configuration.
- **Then:** once the compatibility probe issue is resolved and merged, re-dispatch this task to verify Deploy Dev completion.

## 17. Verification commands (run 30402570022)

```text
git fetch origin dev --prune
git rev-parse origin/dev                              # 7d13f8e162d035ad7318d1f659dfa0f2bd85ca65
HOME=/home/lupin /usr/bin/gh run view 30402570022 -R alfloop-dev/odayplus --json status,conclusion,jobs
HOME=/home/lupin /usr/bin/gh run download 30402570022 -R alfloop-dev/odayplus -n cloud-run-dev-validation
cat cloud-run-dev-validation/cloud-run-migration-compatibility.json
python3 scripts/e2e/check_product_grade_ci_gates.py --report
```

---

## 18. Deploy Dev run 30412416116 (SHA 79cf9b67e6, PR #488)

`Deploy Dev` run [`30412416116`](https://github.com/alfloop-dev/odayplus/actions/runs/30412416116)
was triggered by the `push` of the merge commit of PR #488
(`ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001`) at `2026-07-29T00:52:13Z`,
running on exactly the required SHA `79cf9b67e62ce9fbd762b6695a214965ea9fe258`.

| Field | Value |
|---|---|
| Head branch / SHA | `dev` / `79cf9b67e62ce9fbd762b6695a214965ea9fe258` |
| Event | `push` |
| `e2e-operational-evidence` job | **success** |
| `deploy` job | **failure** at step 13 |
| Started | `2026-07-29T00:52:13Z` |
| Completed | `2026-07-29T01:08:22Z` |

Full step-by-step receipt: `deploy-run-30412416116.json`.

### What changed from the previous run

`ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001` (PR #488) fixed `migration-compatibility-smoke` probe timeouts on cold Cloud Run revisions by introducing bounded retries for transient transport failures and provenance separation. As a result:

- Preflight: 72 checks, 0 failures (`ok: true`, `release_sha: 79cf9b67e6...`)
- Images built, pushed, cosign-signed, cosign-verified: `oday-api`, `oday-worker`, `oday-scheduler` (3× Verification PASSED)
- Migration Cloud Run Job `oday-migration-r-79cf9b67e62c` deployed and executed:
  `Execution [oday-migration-r-79cf9b67e62c-8m2kp] has successfully completed.`
- **Migration Job smoke check: PASSED** (`Cloud Run migration Job smoke passed.`)
- **Migration compatibility smoke: PASSED (RESOLVED)** (`compatibility:/platform/version:http` OK attempts=2 elapsed=20.8s, `compatibility:/platform/health:database` OK, `cloud-run-migration-compatibility-run-30412416116.json`).
- **Scheduler Cloud Run Job & Smoke: PASSED** (`oday-scheduler-r-79cf9b67e62c-fr65q` completed; `Cloud Run scheduler Job smoke passed.`).

### Where run 30412416116 failed

The deploy failed during worker Cloud Run Job execution:

```text
Executing worker Cloud Run Job...
Creating execution...
Provisioning resources.................done
Starting execution..................................................................done
Running execution...................................................................failed
Executing job failed
ERROR: (gcloud.run.jobs.execute) The execution failed.
View details about this execution by running:
gcloud run jobs executions describe oday-worker-r-79cf9b67e62c-6fhw5
Error: worker Cloud Run Job failed; deployment stopped.
```

Verbatim excerpt: `deploy-failure-excerpt-run-30412416116.log`.

## 19. Cloud Run state after run 30412416116 rollback

The automated rollback restored traffic split correctly:

| Service | Serving revision (100%) | Release SHA label |
|---|---|---|
| `oday-api` | `oday-api-00005-gin` | none (pre-run) |
| `oday-web` | `oday-web-00008-ws4` | none (pre-run) |

Snapshot receipt: `cloud-run-post-rollback-state-run-30412416116.json`.

The migration Cloud Run Job `oday-migration-r-79cf9b67e62c` and scheduler job `oday-scheduler-r-79cf9b67e62c` executed successfully at target release SHA `79cf9b67e6`.

## 20. Acceptance status (run 30412416116)

| # | Acceptance criterion | Status | Basis |
|---|---|---|---|
| 1 | Deploy Dev runs from exact merged `origin/dev` SHA and completes successfully | **FAIL** | Ran on `79cf9b67e6`; concluded `failure`; `deploy-run-30412416116.json` |
| 2 | Cloud Run API and web revisions report the deployed release SHA | **FAIL** | No revision carries the label; `cloud-run-post-rollback-state-run-30412416116.json` |
| 3 | Operator API returns live non-placeholder data and fails closed on invalid access | **NOT REACHED** | Release candidate never served traffic |
| 4 | `/operator` leaves loading state and renders Package 10 canonical shell at desktop and mobile | **NOT REACHED** | Same |
| 5 | All 40 Package 10 screen contracts and 117 retired visual paths remain verified | **PASS (source scope)** | `package10-contract-verification.txt` (40/40 screen labels, 117 retired paths, 0 survivors) |
| 6 | Independent Codex6 evidence review and CI pass before closeout | **PENDING** | Requires this evidence PR |

## 21. Recommended remediation task

### 21a. Worker Cloud Run Job execution failure (NEW)

A separate remediation task is required:

- **Scope:** investigate why `oday-worker-r-79cf9b67e62c` execution `oday-worker-r-79cf9b67e62c-6fhw5` failed during execution. Potential root causes include worker entrypoint exception, database connection or migration schema interaction, memory/timeout limits, or background task dependencies.
- **Then:** once the worker Cloud Run Job failure is resolved and merged to dev, re-dispatch this task to verify Deploy Dev completion.

## 22. Verification commands (run 30412416116)

```text
git fetch origin dev --prune
git rev-parse origin/dev                              # 79cf9b67e62ce9fbd762b6695a214965ea9fe258
HOME=/home/lupin /usr/bin/gh run view 30412416116 -R alfloop-dev/odayplus --json status,conclusion,jobs
HOME=/home/lupin /usr/bin/gh run download 30412416116 -R alfloop-dev/odayplus -n cloud-run-dev-validation
cat cloud-run-dev-validation/cloud-run-migration-compatibility.json
python3 scripts/e2e/check_product_grade_ci_gates.py --report
```

---

## 23. Deploy Dev run 30680943677 (SHA 97e3ae2e26, dev tip)

`Deploy Dev` run [`30680943677`](https://github.com/alfloop-dev/odayplus/actions/runs/30680943677)
was triggered by push to `origin/dev` at `2026-08-01T02:54:54Z`,
running on exact SHA `97e3ae2e264d00254b574d5e27ab771688f04768`.

| Field | Value |
|---|---|
| Head branch / SHA | `dev` / `97e3ae2e264d00254b574d5e27ab771688f04768` |
| Event | `push` |
| `e2e-operational-evidence` job | **success** |
| `deploy` job | **failure** at step 13 |
| Started | `2026-08-01T02:54:54Z` |
| Completed | `2026-08-01T03:13:46Z` |

Full step-by-step receipt: `deploy-run-30680943677.json`.

### What changed from previous runs

`ODP-DEPLOY-WORKER-JOB-EXECUTION-001` (PR #494) resolved worker Cloud Run job execution failures. As a result:
- Preflight: 72 checks, 0 failures (`cloud-run-preflight-run-30680943677.json`)
- Migration Cloud Run Job (`oday-migration-r-97e3ae2e264d-bczn7`): **PASSED**
- Migration compatibility smoke: **PASSED** (`cloud-run-migration-compatibility-run-30680943677.json`)
- Scheduler Cloud Run Job (`oday-scheduler-r-97e3ae2e264d-2nz95`): **PASSED**
- Worker Cloud Run Job (`oday-worker-r-97e3ae2e264d-895lx`): **PASSED (RESOLVED)**
- Cloud Run candidate revisions for API and Web were created and deployed.

### Where run 30680943677 failed

The deploy failed closed during release-aware candidate smoke testing (`cloud-run-smoke-run-30680943677.json`):
1. `/platform/health` & `/readiness` returned HTTP 503 (`smoke:/platform/health:http` and `smoke:/readiness:http` failed)
2. `data_mode` reported as missing (`smoke:/platform/health:live_data_mode` and `smoke:/readiness:live_data_mode` failed)
3. `/api/v1/operator/bootstrap` returned `data_mode=degraded data_source=operator-shell-production` instead of authoritative live data (`smoke:/api/v1/operator/bootstrap:provenance` failed)
4. Read provenance reported `origin_kind=degraded` while persistence mode was `postgresql` and `live_ready=True` (`smoke:/api/v1/operator/bootstrap:read_provenance` failed)
5. `forecastops` capability reported `PRODUCTION_MODEL_REGISTRY_UNAVAILABLE: forecast_revenue_interval: configured MLflow registry has no production alias`, resulting in `productionBindingsReady=false` and `modes.data.mode=unavailable` (`blockingReasons: ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"]`).

Verbatim excerpt: `deploy-failure-excerpt-run-30680943677.log`.

## 24. Cloud Run state after run 30680943677 rollback

Automated rollback performed successfully:
- Serving API revision `oday-api-00005-gin` restored to 100% traffic (0% candidate)
- Serving Web revision `oday-web-00008-ws4` restored to 100% traffic (0% candidate)
- Migration job `oday-migration-r-97e3ae2e264d`, scheduler job `oday-scheduler-r-97e3ae2e264d`, worker job `oday-worker-r-97e3ae2e264d` all executed successfully.

Snapshot receipt: `cloud-run-post-rollback-state-run-30680943677.json`.

## 25. Acceptance status (run 30680943677)

| # | Acceptance criterion | Status | Basis |
|---|---|---|---|
| 1 | Deploy Dev runs from exact merged `origin/dev` SHA and completes successfully | **FAIL** | Ran on `97e3ae2e26`; concluded `failure`; `deploy-run-30680943677.json` |
| 2 | Cloud Run API and web revisions report the deployed release SHA | **FAIL** | Candidate revisions rolled back to 0% traffic; `cloud-run-post-rollback-state-run-30680943677.json` |
| 3 | Operator API returns live non-placeholder data and fails closed on invalid access | **NOT REACHED** | Candidate candidate smoke failed closed |
| 4 | `/operator` leaves loading state and renders Package 10 canonical shell at desktop and mobile | **NOT REACHED** | Candidate candidate smoke failed closed |
| 5 | All 40 Package 10 screen contracts and 117 retired visual paths remain verified | **PASS (source scope)** | `package10-contract-verification.txt` (40/40 screen labels, 117 retired paths, 0 survivors) |
| 6 | Independent Codex6 evidence review and CI pass before closeout | **PENDING** | Requires this evidence PR |

## 26. Required remediation task

A separate P0 Fleet remediation task must be created to resolve live-data/provenance health composition:
- **Scope:** resolve live data mode and model registry production alias binding so candidate revisions pass `/platform/health`, `/readiness`, `/api/v1/operator/bootstrap` provenance gates with live non-degraded data.
- **Dependency:** task `ODP-P10-DEV-REDEPLOY-VERIFY-001` remains blocked on reviewed merge of the remediation task.

## 27. Verification commands (run 30680943677)

```text
git fetch origin dev --prune
git rev-parse origin/dev                              # 97e3ae2e264d00254b574d5e27ab771688f04768
HOME=/home/lupin /usr/bin/gh run view 30680943677 -R alfloop-dev/odayplus --json status,conclusion,jobs
HOME=/home/lupin /usr/bin/gh run download 30680943677 -R alfloop-dev/odayplus -n cloud-run-dev-validation
cat cloud-run-dev-validation/cloud-run-smoke.json
python3 scripts/e2e/check_product_grade_ci_gates.py --report
```



