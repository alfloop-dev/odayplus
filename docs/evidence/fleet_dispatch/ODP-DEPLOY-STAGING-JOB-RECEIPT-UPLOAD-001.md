# ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001: staging receipt publication closeout

Owner: Claude3 · Reviewer: Antigravity5 · Phase: Deployment Evidence ·
2026-07-29

Apply the Cloud Run Job receipt allowlist proven in Deploy Dev to Deploy
Staging, and make the contract cover both environments from one source of truth.

Full runtime detail:
`docs/evidence/runtime/ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001/` (README plus
`simulate_staging_artifact_selection.py`, `mutate_upload_contract.py`, and the
three transcripts).

## 1. The gap

`.github/workflows/deploy-staging.yml` published its artifact through:

```yaml
path: |
  .odp_data/deployment/*.json
  .odp_data/remote-staging-proof/*.json
```

The first entry is the same non-recursive glob that lost Deploy Dev's Cloud Run
Job receipts in run 30436771086 (ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001, fixed in
`c7727020`). `actions/upload-artifact` resolves `path` with `@actions/glob`,
where a bare `*` matches one directory level; the receipts
`deploy_cloud_run_waji.sh` writes under `cloud-run-jobs/` are a level below.

**Staging has not lost a receipt yet, because it has never written one.** Every
staging run on record fails at step 8 — *"Error: Workload Identity Federation
variables are required"* — so steps 9–13 skip and the deploy script never runs.
Run
[30445252373](https://github.com/alfloop-dev/odayplus/actions/runs/30445252373)
then logs *"No files were found with the provided path:
.odp_data/deployment/\*.json"* and produces zero artifacts
(`artifacts.total_count == 0`).

**Verdict: a latent artifact-path defect, closed before its first real deploy.**
The reproduction is therefore structural — the identical pattern, evaluated over
the file tree the shared deploy script produces — not a lost-artifact
postmortem. `simulate_staging_artifact_selection.py --pre-fix` replays the path
list quoted from `88dae2e1` over that tree and reports *"DEFECT REPRODUCED: 3
Cloud Run Job receipts dropped, 2 unreviewed top-level files published."*

## 2. What shipped

- `.github/workflows/deploy-staging.yml` — the upload step's two globs become
  an explicit eight-entry allowlist: the four validator reports (preflight,
  smoke, migration-compatibility, live-e2e-gate), the three Cloud Run Job
  receipts it would have dropped, and the run-scoped remote staging proof
  `staging-${{ github.run_id }}.json` that the old second glob did publish.
  Nothing else in the workflow changes; `if: always()` and
  `if-no-files-found: ignore` are preserved so a failed deploy still publishes
  whatever it reached.
- `tests/ops/test_deploy_workflow_contract.py` — rewritten from a Deploy-Dev-only
  module into a parametrised sweep over a `DEPLOY_WORKFLOWS` table. 16 tests.

## 3. Coverage for both environments without duplicating truth

The allowlist is a second place that has to stay true, and there are now two of
them. Nothing in the tests restates a path per environment:

| Expected set | Derived from |
| --- | --- |
| Cloud Run Job receipts | `execute_job "<kind>"` call sites in `scripts/deploy_cloud_run_waji.sh` |
| top-level validator reports | that script's `NAME="${NAME:-.odp_data/deployment/...}"` defaults |
| per-environment reports | each workflow's own `--output .odp_data/...` step arguments |
| excluded raw dumps | the `capture_job_proof` / `capture_latest_execution` lines that write them |

`test_every_deploy_workflow_runs_the_same_receipt_writing_script` asserts the
premise that makes one derivation valid for two environments: both workflows
invoke `./scripts/deploy_cloud_run_waji.sh`. If an environment forks to its own
deploy script, that test fails rather than the shared expectations quietly
becoming fiction.

Two properties are asserted rather than left to review:

- nothing outside `.odp_data/deployment` may be uploaded unless a step in that
  same workflow wrote it (this is what admits staging's remote proof without
  turning `.odp_data/remote-staging-proof/` into a blanket allowance);
- `test_no_workflow_globs_into_the_deployment_report_directory` sweeps **every**
  workflow in the repository, not only the two in the table. This defect was one
  template copied twice; a third environment added later fails here instead of
  at the next incident.

## 4. Confidentiality

`cloud-run-jobs/` also holds the raw `gcloud run jobs describe`,
`executions describe`, and `executions list` dumps, which restate the deployed
env block and its secret selectors verbatim. A recursive `**` include would have
closed the receipt gap by publishing all nine of them, so the fix is an
allowlist of literal files, with globs, `!` exclusions, `..` traversal, and the
three raw-dump suffixes all asserted absent.

`${{ github.run_id }}` is the only workflow expression permitted inside a path;
anything steerable by workflow input or event payload would move path selection
back outside review.

Every allowlisted file is written by `validate_cloud_run_live_deployment.py`,
`check_live_e2e_gate.py`, or `check_remote_staging_proof.py`.
`test_uploaded_job_receipt_names_the_job_but_never_a_bound_value` builds a
receipt from a job description carrying a plaintext env value and a secret
selector, and asserts neither survives into the published report while the
env-var *name* does.

## 5. Verification

```
python3 -m pytest tests/ops/test_deploy_workflow_contract.py      -> 16 passed
python3 -m pytest tests/ops tests/e2e/test_remote_staging_proof_checker.py
                                                                  -> 452 passed, 1 failed
python3 -m ruff check / ruff format --check                       -> clean
yaml.safe_load on both deploy workflows                           -> parsed
simulate_staging_artifact_selection.py            -> 3 receipts recovered, 5 reports
                                                     preserved, 9 dumps + 3 decoys excluded
simulate_staging_artifact_selection.py --pre-fix  -> DEFECT REPRODUCED
mutate_upload_contract.py                         -> 8/8 mutations caught
```

The single failure,
`test_cloud_run_live_deployment.py::test_deploy_preflight_imports_runtime_dependencies_via_locked_python`,
is environmental: it expects the deploy script's exit 97 and gets 1 from
*"Error: required command 'uv' is not installed."* Confirmed failing identically
on the parent commit `88dae2e1` in a separate worktree; transcript in
`verification-commands.txt`.

## 6. Mutation coverage

Eight mutations, each reintroducing a form of the defect in a throwaway
worktree, all caught — including the literal pre-fix path list and a dev-side
regression, which proves the repo-wide sweep is not staging-only. Per-mutation
detail and the tests that caught each are in `mutation-transcript.txt`.

## 7. Scope

Not changed: deployment traffic, product code, Package 10 UI, API responses,
worker execution logic, the receipt writers, and `deploy-dev.yml` — whose
allowlist this task only brings under shared coverage.

Flagged, not fixed: staging's Workload Identity Federation variables are
unconfigured, which is why every staging run fails closed at step 8. That is a
deployment-environment configuration item, tracked separately; this task ensures
that when staging is configured, its first green deploy publishes its receipts
instead of discarding them.
