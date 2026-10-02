# ODP-DEV-EGRESS-READBACK-RECOVERY-001 — egress readback and EXIT-trap recovery repair

- Recorded at: 2026-10-02T19:01:00Z
- Source base: `origin/dev` 96a2a05a486c4a832a3cc94f4f6028dcb20a777e
- Failed run: Runtime Release deploy 37049356942 (validation artifact 11246440237)
- Scope: parser/recovery code only. No deployment, release request, lease,
  migration, image rebuild, network or IAM change was made by this task.
  Failure/deployment evidence for run 37049356942 stays with the parent task
  ODP-DEV-LIVE-DEPLOY-EXECUTION-001 (Claude2); it is not copied or edited here.

## Observed failure (from the shared sanitized diagnostics)

- `public-egress-probe.json`: `reason=vpc_egress_not_all_traffic`,
  `vpc_egress=""`, `execution=not_run`, job `oday-worker-r-6140d0ef633c`.
- The deploy env passed `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`; the GCP
  CreateJob audit for the candidate jobs carries
  `spec.template.metadata.annotations["run.googleapis.com/vpc-access-egress"]="all-traffic"`.
- Recovery log: `Error: previous-release state could not be determined`, then
  successful deletes of `oday-api` and all three `-r-6140d0ef633c` candidate
  jobs, then `Error: one or more Cloud Run recovery actions failed.`

## Root causes

1. The inline readback parser in `run_public_egress_probe` only looked at
   `vpcAccess.egress` paths. gcloud returns the v1 Job shape, where egress is
   the execution-template annotation, so it printed nothing and exited 0; the
   empty string then failed the ALL_TRAFFIC comparison.
2. `release_recovery_mode` and the bootstrap-delete path of
   `restore_service_traffic` ended success paths with a bare `return`. Inside
   an EXIT trap, bash reports the status of the last command before the trap
   (the failed deployment step, 1), so completed recovery read back as failed.

## Repair

- `cloud_run_job_vpc_egress` (in `cloud_run_release_traffic.sh`) reads every
  known v1 annotation and v2 `vpcAccess.egress` location, normalizes
  `all-traffic`/`all`/`ALL_TRAFFIC` to `ALL_TRAFFIC` and
  `private-ranges-only`/`PRIVATE_RANGES_ONLY` to `PRIVATE_RANGES_ONLY`, and
  exits non-zero on non-JSON, missing, empty, unrecognised, non-string or
  contradictory readback (receipt reason `job_readback_invalid`). The probe
  still refuses anything other than `ALL_TRAFFIC` before executing the job.
- Trap-reachable success paths return 0 explicitly; the delete path returns 1
  when `gcloud run services delete` fails. The original deployment exit status
  is still the script's exit status.

## Verification (local, Python 3.12 via `uv run --frozen --python 3.12`)

- `pytest -q tests/ops/test_egress_readback_recovery.py tests/ops/test_first_release_recovery.py tests/ops/test_first_release_no_traffic.py tests/ops/test_deploy_workflow_contract.py`
  → exit 0, 134 passed.
- `bash -n` on both scripts; `ruff check` / `ruff format --check` on the new test.
- A/B: the new tests against the base 96a2a05a helper fail. The EXIT-trap
  harness (real `trap ... EXIT` entered after a failed step, all candidates
  present, every delete succeeding) reports `rollback_status=1` on base and
  `rollback_status=0` with this repair; the base inline parser fed the v1
  `all-traffic` annotation exits 0 with empty output.
- Genuinely failed cleanup (service or job delete failing) still yields a
  non-zero recovery status, and remaining candidates are still deleted.

## Follow-up (coordinator)

This source change needs a fresh exact candidate/build and a new exact-tuple
deployment authorization after merge; nothing here authorizes a deployment.
