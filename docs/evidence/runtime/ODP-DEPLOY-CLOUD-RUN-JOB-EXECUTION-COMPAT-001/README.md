# ODP-DEPLOY-CLOUD-RUN-JOB-EXECUTION-COMPAT-001 evidence

## Version dependency removed

`scripts/deploy_cloud_run_waji.sh` captured every Cloud Run Job receipt with
gcloud's shortcut subcommand for a job's newest execution. That subcommand only
exists on recent gcloud releases, so whether Deploy Dev could produce migration,
scheduler, and worker proof at all depended on the runner image's CLI version
rather than on the deployment itself. On a runner without it the command exits
with an unrecognised-argument error, which aborts the deployment inside the
migration gate — before the API/Web candidates are ever deployed.

The shortcut is gone. `grep -c describe-latest scripts/deploy_cloud_run_waji.sh`
now returns `0`, and `tests/ops/test_cloud_run_live_deployment.py::
test_deploy_script_captures_job_proof_without_describe_latest` pins that.

## Delivered boundary

Both the success path (`capture_job_proof`) and the failure forensics path
(`execute_job`) now share one helper, `capture_latest_execution`:

1. `gcloud run jobs executions list --job=<job> --format=json` — a surface that
   has existed for as long as Cloud Run Jobs themselves.
2. `validate_cloud_run_live_deployment.py resolve-latest-execution` resolves the
   newest execution name from that list, under `run_locked_python`.
3. `gcloud run jobs executions describe <exact-name> --format=json` writes the
   receipt that `jobs-smoke` validates.

`resolve_latest_execution_name` reads both schemas gcloud emits across versions:
the Knative shape (`metadata.name`, `metadata.creationTimestamp`,
`metadata.labels."run.googleapis.com/job"`) and the v2 shape (`name`,
`createTime`, `job`), including RFC3339 nanosecond precision and a bare or
`items`/`executions`-wrapped array.

## Fail-closed matrix

The resolver refuses to name an execution it cannot prove is the right one, and
the shell helper exits non-zero before `describe` is reached, so no receipt file
is created. The success proof path propagates that failure into the existing
rollback trap. The failed-job forensics path deliberately swallows capture
failure with `|| true`, but explicit returns inside the helper still prevent an
empty-name `describe` or an unproven receipt when Bash disables errexit for the
OR-list call.

| Input | Outcome |
| --- | --- |
| empty list (`[]`, `{"items": []}`) | `no Cloud Run Job execution was found` |
| payload is not an array | `must be a JSON array of execution objects` |
| entry is not an object | `executions[i] is not a JSON object` |
| entry has no name | `no resolvable execution name` |
| several entries, one without a timestamp | `no creation timestamp to order by` |
| several entries, unparsable timestamp | `creation timestamp ... is unparsable` |
| newest timestamp shared by two entries | `the latest execution is ambiguous` |
| execution belongs to another job | `does not belong to job` |
| ownership references conflict or are malformed | `does not belong to job` |
| no ownership reference, even with a matching name prefix | `does not belong to job` |
| execution ran but failed | unchanged: `jobs-smoke:<kind>:execution` fails |

The last row is the pre-existing `_execution_completed` gate (`succeededCount>=1`
and `failedCount==0` with a `Completed` condition). It is unchanged and still
covered by `test_job_smoke_rejects_failed_execution_and_missing_provider_secrets`.

Job ownership is proven only by the Knative job label or v2 `job` reference.
Every ownership reference present in a mixed-schema entry must be a non-empty
string and must identify the requested job. Execution-name prefixes are not
ownership evidence because distinct job names can share a prefix (for example,
`worker-job` and `worker-job-canary`).

## Unchanged by this task

- `jobs-smoke` proof schema, report keys, and check names.
- Migration → candidate → smoke → traffic-cut → scheduler gate ordering.
- `handle_deployment_exit` traffic and Cloud Scheduler rollback semantics.
- Workflows, API, Package 10, model registry, and OperatorStateService.

The only new artifact is an additive `<kind>-execution-list.json` alongside the
existing `<kind>-execution.json` receipt in `JOB_REPORT_DIR`.

## Focused verification

Executed from the task branch at commit
`b49de876dd65c5873cb27763fb48441b7786d9a4`:

```text
bash -n scripts/deploy_cloud_run_waji.sh
uv run --frozen pytest tests/ops/test_cloud_run_live_deployment.py -q   # 59 passed
uv run --frozen ruff check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
uv run --frozen ruff format --check scripts/deployment/validate_cloud_run_live_deployment.py
git diff --check
```

All commands passed.

Verification is not limited to source-text assertions. A focused shell harness
extracts the real `capture_latest_execution` function from the deploy script and
runs it against a stubbed `gcloud` that records every invocation:

- with two executions listed, the recorded calls are
  `run jobs executions list --job=worker-job ...` followed by
  `run jobs executions describe worker-job-00002 ...` — the newest name, by
  exact name, with no shortcut subcommand in the log, and the receipt file
  contains that execution.
- with an empty list, a nameless entry, or non-JSON list output, the helper
  exits non-zero, `describe` is never invoked, and the receipt file does not
  exist.

### Re-verification after merging `dev`

PR #479 went `BEHIND` when `origin/dev` advanced to
`8efe18580d7a66fe40fb5483b1b3779d12c5810b`. That commit was merged into the task
branch as merge commit `499c0f28e9a03bfc375cf1a98ef7f00c5783ed35`. The merge was
conflict-free and touched only
`docs/evidence/runtime/ODP-ORCH-CLAUDE-TASKOUTPUT-LIFECYCLE-001/`; the reviewed
deploy and validator scope is byte-identical, and `git diff origin/dev HEAD`
still reports exactly the four task artifacts (543 insertions, 32 deletions).

The same focused checks were re-executed at `499c0f28`, all passing:

```text
bash -n scripts/deploy_cloud_run_waji.sh
pytest tests/ops/test_cloud_run_live_deployment.py -q   # 59 passed
ruff check .
ruff format --check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
git diff --check origin/dev HEAD
```

`uv` must be on `PATH` for the suite to pass:
`test_deploy_preflight_imports_runtime_dependencies_via_locked_python` executes
the real deploy script, whose `require_command` guard exits `1` when `uv` is
missing. That guard is owned by ODP-DEPLOY-SCRIPT-LOCKED-PYTHON-001 and is not
touched by this task.

### OR-list fail-closed remediation

Codex6's exact-head review at `f9aa4261` identified that Bash disables errexit
inside `capture_latest_execution` when `execute_job` invokes it as
`capture_latest_execution ... || true`. The helper now returns explicitly when
the execution list command fails, the resolver rejects its payload, the
resolved name is empty, or the exact-name describe fails.

The shell harness now also invokes the real extracted helper in that same
OR-list context. Empty, nameless, and malformed execution lists are swallowed
as best-effort forensic failures while still proving that `describe` is never
called and no execution receipt is created.

The full focused checks were re-executed at anchor commit `077aeb2a`, all
passing:

```text
bash -n scripts/deploy_cloud_run_waji.sh
uv run --frozen pytest tests/ops/test_cloud_run_live_deployment.py -q   # 62 passed
uv run --frozen ruff check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
uv run --frozen ruff format --check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
git diff --check origin/dev HEAD
```

### Ownership convergence remediation

Codex6's exact-head review at `0960cfd` found two ownership gaps in the list
resolver: a matching Knative label could mask a conflicting v2 `job` reference,
and an unlabelled execution such as `worker-job-canary-*` could pass the
`worker-job` name-prefix fallback.

The resolver now requires at least one explicit ownership reference, requires
every present reference to be a non-empty string naming the requested job, and
does not infer ownership from the execution name. Dedicated regressions cover
both the conflicting-reference and shared-prefix cases.

The focused checks were re-executed against the task-owned working-tree diff on
top of `0960cfd`, all passing:

```text
bash -n scripts/deploy_cloud_run_waji.sh
uv run --frozen pytest tests/ops/test_cloud_run_live_deployment.py -q   # 63 passed
uv run --frozen ruff check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
uv run --frozen ruff format --check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
git diff --check origin/dev
```

Exact-head CI and independent Codex6 review remain required before merge. After
merge, ODP-P10-DEV-REDEPLOY-VERIFY-001 must be re-run from the exact merged SHA.
