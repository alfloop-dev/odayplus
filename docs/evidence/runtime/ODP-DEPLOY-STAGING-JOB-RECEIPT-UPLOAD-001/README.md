# ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001 — runtime evidence

Deploy Staging published its artifact through the same non-recursive glob that
lost Deploy Dev's Cloud Run Job receipts. This directory holds the reproduction,
the selection proof, and the mutation run that shows the new contract test bites.

Anchor commit: `ce76cb9e`. Deliverable:
`.github/workflows/deploy-staging.yml` and
`tests/ops/test_deploy_workflow_contract.py`.

## The defect

`scripts/deploy_cloud_run_waji.sh` writes three Cloud Run Job validation
receipts under `.odp_data/deployment/cloud-run-jobs/`:

```
cloud-run-jobs/migration-validation.json
cloud-run-jobs/scheduler-validation.json
cloud-run-jobs/worker-validation.json
```

The staging upload step asked for:

```yaml
path: |
  .odp_data/deployment/*.json
  .odp_data/remote-staging-proof/*.json
```

`actions/upload-artifact` resolves `path` with `@actions/glob`, where a bare `*`
matches within one directory level. `cloud-run-jobs/` is one level below, so
every receipt was outside the pattern. The same list is open at the top: any
JSON that lands in `.odp_data/deployment/` gets published, reviewed or not.

## How this differs from the Deploy Dev incident

Deploy Dev lost real receipts: run 30436771086 wrote three passing ones and
published none (ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001, fixed in `c7727020`).

Staging has lost nothing yet, because it has never produced anything to lose.
`run-30445252373-staging-status.txt` shows the ten most recent staging runs —
all failing — and where: step 8, `Validate release authority, WIF, and live
runtime`, exits on *"Error: Workload Identity Federation variables are
required."* Steps 9–13 skip, so `deploy_cloud_run_waji.sh` never runs. The
upload step still fires under `if: always()` and reports *"No files were found
with the provided path: .odp_data/deployment/\*.json"*; the run's artifact
`total_count` is `0`.

So the reproduction here is structural, not a lost-artifact postmortem: the
identical pattern, over the file tree the shared deploy script produces. This is
a latent defect being closed before its first real deploy, and stating it that
way is the honest reading of the evidence.

## Files

| File | What it shows |
| --- | --- |
| `run-30445252373-staging-status.txt` | `gh` transcript: every staging run fails at the WIF gate, the upload finds nothing, zero artifacts |
| `simulate_staging_artifact_selection.py` | Re-runnable selection harness over either deploy workflow |
| `staging-selection-proof.txt` | Its output: pre-fix (defect reproduced), post-fix staging, and Deploy Dev for comparison |
| `mutate_upload_contract.py` | Reintroduces eight forms of the defect in a throwaway worktree |
| `mutation-transcript.txt` | Its output: 8/8 caught, with the tests that caught each |
| `verification-commands.txt` | pytest, ruff, and workflow-parse transcript |

## Selection proof

`simulate_staging_artifact_selection.py` builds the 20-file tree a green staging
deploy leaves on the runner — 4 validator reports, 3 Job receipts, 9 raw gcloud
dumps, the run-scoped remote staging proof, and 3 decoy files that do *not*
exist today (`api-env.json`, `sbom.json`, `cloud-run-jobs/worker-env-dump.json`)
— and evaluates both path lists over it. The new allowlist is read out of the
workflow rather than restated, so the script cannot drift from CI.

Pre-fix (`--pre-fix`, evaluating the list quoted from `88dae2e1`):

```
DEFECT REPRODUCED: 3 Cloud Run Job receipts dropped, 2 unreviewed top-level files published.
```

Post-fix:

```
ASSERTIONS OK: 3 Job receipts recovered, 5 prior reports preserved,
9 raw gcloud dumps and 3 decoys excluded.
```

The two decoys are the point of the second number: the old glob was not only
blind below the top level, it was open *at* it.

## Why an allowlist rather than `**`

`cloud-run-jobs/` also holds the raw `gcloud run jobs describe`,
`executions describe`, and `executions list` dumps (`*-job.json`,
`*-execution.json`, `*-execution-list.json`). Those restate the deployed
environment block and its secret selectors verbatim. A recursive include would
have fixed the receipt gap by publishing all nine of them.

Every file in the allowlist is written by
`validate_cloud_run_live_deployment.py`, `check_live_e2e_gate.py`, or
`check_remote_staging_proof.py`, and carries redacted check results only —
`test_uploaded_job_receipt_names_the_job_but_never_a_bound_value` builds a
receipt from a job description carrying a plaintext env value and a secret
selector and asserts neither survives into the report.

## Staging's extra report

The old glob also published `.odp_data/remote-staging-proof/*.json` — the one
thing a deployment-directory allowlist alone would have thrown away. The
replacement names it exactly:

```yaml
.odp_data/remote-staging-proof/staging-${{ github.run_id }}.json
```

which is the same file the `Verify configured staging authority endpoint` step
writes with `--output ".odp_data/remote-staging-proof/staging-${GITHUB_RUN_ID}.json"`.
`test_upload_allowlist_publishes_every_report_the_workflow_itself_writes` reads
that `--output` back out of the workflow and requires the upload to match it, so
renaming the proof breaks the test rather than silently emptying the artifact.
`${{ github.run_id }}` is the only expression permitted in a path — anything
steerable by workflow input or event payload would move path selection back
outside review.

## Contract coverage for both environments

`tests/ops/test_deploy_workflow_contract.py` was a Deploy-Dev-only module. It is
now a parametrised sweep over a `DEPLOY_WORKFLOWS` table, with truth derived
once rather than restated per environment:

- the Job receipt set comes from `execute_job` call sites in
  `deploy_cloud_run_waji.sh`, and `test_every_deploy_workflow_runs_the_same_receipt_writing_script`
  asserts both workflows actually run that script — the premise that makes one
  derivation valid for two environments;
- the top-level report set comes from the script's `NAME="${NAME:-...}"`
  defaults;
- per-environment reports come from each workflow's own `--output` arguments;
- `test_no_workflow_globs_into_the_deployment_report_directory` sweeps *every*
  workflow in the repository, not just the two in the table. The defect was one
  template copied twice; a third environment added later fails here.

## Mutation run

Eight mutations, each reintroducing a form of the defect, all caught
(`mutation-transcript.txt`):

| Mutation | Caught by |
| --- | --- |
| staging reverts to the original glob pair | the repo-wide sweep + all four staging path assertions |
| staging drops the worker Job receipt | `..._covers_every_job_receipt_the_deploy_script_writes[staging]` |
| staging drops the remote staging proof | `..._publishes_every_report_the_workflow_itself_writes[staging]` |
| staging uploads a raw gcloud describe dump | `..._excludes_raw_gcloud_dumps_and_wildcards[staging]` |
| staging re-adds a recursive wildcard | the repo-wide sweep + `..._excludes_raw_gcloud_dumps_and_wildcards[staging]` |
| staging proof path drifts from its writer | `..._publishes_every_report_the_workflow_itself_writes[staging]` |
| staging publishes an unjustified proof-dir file | `..._publishes_every_report_the_workflow_itself_writes[staging]` |
| dev regresses to the glob | the repo-wide sweep + all four dev path assertions |

## Scope

Not changed: deployment traffic, product code, Package 10 UI, API responses,
worker execution logic, the receipt writers, and `deploy-dev.yml` — whose
allowlist this task only brings under shared coverage.

Not fixed, and out of scope: staging's WIF variables are unconfigured, which is
why every staging run fails. That is a deployment-environment configuration
item, not an artifact-path one. This task makes sure that when staging is
configured, its first green deploy publishes the receipts instead of discarding
them.
