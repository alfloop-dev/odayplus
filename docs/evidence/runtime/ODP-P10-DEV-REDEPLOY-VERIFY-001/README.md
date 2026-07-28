# ODP-P10-DEV-REDEPLOY-VERIFY-001 runtime evidence

- Task: `ODP-P10-DEV-REDEPLOY-VERIFY-001`
- Owner: Claude2
- Independent reviewer: Codex6
- Target ref: `origin/dev` @ `450c7faddda32155fadce6c36cfdeed623a385a3`
  (merge of PR #474, `ODP-DEPLOY-SCRIPT-LOCKED-PYTHON-001`)
- Result: **BLOCKED — the dev redeploy failed, so Package 10 Operator runtime
  parity is not proven and must not be claimed.**

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
| `deploy-run-30362772798.json` | full job/step receipt for the failed Deploy Dev run |
| `deploy-failure-excerpt.log` | verbatim ANSI-stripped log excerpts: preflight, migration job, failure, rollback |
| `cloud-run-preflight.json` | unmodified `cloud-run-dev-validation` artifact from the run |
| `cloud-run-post-rollback-state.json` | `oday-api` / `oday-web` traffic, revisions, and release-SHA labels after rollback |
| `migration-job-receipt.json` | structured migration receipt at the target release SHA |
| `worker-dispatch-receipts.json` | the two preserved Antigravity3 quota failures plus Supervisor events |
| `package10-contract-verification.txt` | 40-screen contract and 117-retired-path re-verification at `origin/dev` |
