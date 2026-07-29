# ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001 — runtime evidence

Owner: Claude3 · Reviewer: Antigravity5 · Phase: Deployment Evidence · 2026-07-29

Publish the structured Cloud Run Job validation receipts that Deploy Dev
already produces and silently drops.

| File | What it is |
| --- | --- |
| `run-30436771086-artifact-contents.txt` | §1 — the published artifact vs. the receipts the same run wrote |
| `simulate_artifact_selection.py` | §3 — re-runnable old-glob vs. new-allowlist selection proof |
| `artifact-selection-proof.txt` | §3 — that script's recorded output |
| `mutation-transcript.txt` | §4 — six mutations, and which test catches each |
| `verification-commands.txt` | §5 — pytest / ruff / workflow-parse transcript |

## 1. The gap, reproduced

Deploy Dev run
[30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086)
at head SHA `93ae1b2e75e1056c2bfeccd1d59e25e354f4f21f` published artifact
`cloud-run-dev-validation` (id `8718228990`, 6 743 bytes). Its complete
contents:

```
cloud-run-migration-compatibility.json
cloud-run-preflight.json
cloud-run-smoke.json
```

The same run's deploy log shows three Cloud Run Job receipts written — all
three passing — in the minutes before the upload step ran:

```
08:58:31Z  Cloud Run migration Job smoke passed.
           report=.odp_data/deployment/cloud-run-jobs/migration-validation.json
09:01:00Z  Cloud Run scheduler Job smoke passed.
           report=.odp_data/deployment/cloud-run-jobs/scheduler-validation.json
09:01:27Z  Cloud Run worker Job smoke passed.
           report=.odp_data/deployment/cloud-run-jobs/worker-validation.json
```

None of the three is in the artifact. The cause is one character of glob
semantics: the upload step's `path: .odp_data/deployment/*.json` is not
recursive, so `cloud-run-jobs/` — the whole Job-receipt tree — was never a
candidate. `if-no-files-found: ignore` meant the omission was silent.

This is the run that carried the ODP-DEPLOY-WORKER-JOB-EXECUTION-001 fix. The
receipts proving the worker Job finally executed cleanly are exactly the ones
that died with the runner.

## 2. What changed

`.github/workflows/deploy-dev.yml`, upload step only — one glob replaced by an
explicit seven-entry allowlist:

```yaml
path: |
  .odp_data/deployment/cloud-run-preflight.json
  .odp_data/deployment/cloud-run-smoke.json
  .odp_data/deployment/cloud-run-migration-compatibility.json
  .odp_data/deployment/live-e2e-gate.json
  .odp_data/deployment/cloud-run-jobs/migration-validation.json
  .odp_data/deployment/cloud-run-jobs/scheduler-validation.json
  .odp_data/deployment/cloud-run-jobs/worker-validation.json
```

An allowlist rather than a recursive include, because `cloud-run-jobs/` is not
a directory of publishable files. `capture_job_proof` and
`capture_latest_execution` also write the raw `gcloud run jobs describe`,
`executions describe`, and `executions list` output there — `*-job.json`,
`*-execution.json`, `*-execution-list.json`, nine files on a green deploy —
and those restate the deployed env block and its secret selectors verbatim.
`**/*.json` would have published all nine.

Artifact layout is unchanged for readers: `upload-artifact@v4` roots the
archive at the least common ancestor of what it matched, which stays
`.odp_data/deployment`, so the four existing reports keep their current names
and the receipts arrive under `cloud-run-jobs/`.

## 3. Nothing sensitive is uploaded

Two independent arguments, because "the files we upload happen to be clean" is
weaker than "only these files can be uploaded".

**The set is closed.** Every entry is a literal path. No wildcard, no
exclusion pattern, nothing outside `.odp_data/deployment/`, no `..`. A file
that is not named cannot be published, including one added later. The
`.env` files, traffic snapshots, and candidate descriptions the deploy script
handles all live in `mktemp` files outside the repository tree
(`scripts/deploy_cloud_run_waji.sh` lines 115–122) and are `rm -f`'d by
`cleanup()`; the operator bearer token is `unset` there too. None of them was
ever a candidate under either path.

`simulate_artifact_selection.py` builds the 19-file tree a green deploy
leaves — four validator reports, three receipts, nine raw dumps, and three
decoys that do not exist today — reads the allowlist out of the workflow, and
records which files each path selects. Result: the three receipts are
recovered, the four reports preserved, and the nine dumps plus three decoys
excluded.

That run also shows the old glob's other half. It published two of the three
decoys (`api-env.json`, `sbom.json`) purely because they sat at the top of the
directory. The defect was not only that the glob missed a subdirectory — it
was open at the top, so any future JSON written there would ship unreviewed.

**The contents are redacted by construction.** Each allowlisted file is
written by `scripts/deployment/validate_cloud_run_live_deployment.py` (or
`scripts/e2e/check_live_e2e_gate.py`) and carries `secret_values_redacted:
true`. A receipt holds the job kind, job name, execution name, selected
provider IDs, required and bound secret *env-var names*, and the check list —
never a bound value. `_secret_binding_proof` reports *how* a binding is
malformed, never what it holds; the live E2E gate additionally runs every
detail through `_redactor`, which strips the operator bearer token and any
`Bearer …` string. Confirmed against the real artifact from run 30436771086:
all three published reports carry the flag, and a scan for bearer / token /
password / api-key / private-key / `ya29.` / JWT patterns matches only
env-var *names* (`ODP_..._API_KEY`, `..._TOKEN`) in the preflight check list.

`test_uploaded_job_receipt_names_the_job_but_never_a_bound_value` pins this
at the layer that produces the file: it feeds `cloud_run_job_checks` a job
description carrying a plaintext env value and a `secretKeyRef`, and asserts
the emitted report reproduces neither while still carrying the redaction flag.

## 4. The path contract is tested, and the test is tested

`tests/ops/test_deploy_workflow_contract.py` (8 tests) parses the workflow as
YAML and derives its expectations from `scripts/deploy_cloud_run_waji.sh`
rather than restating them:

- job kinds from the literal `execute_job "<kind>"` call sites → each needs a
  `<kind>-validation.json` entry
- top-level report paths from the `NAME="${NAME:-…}"` defaults → none may be
  dropped
- the excluded suffixes are anchored to the lines that write them, including
  `-execution-list.json`, which is derived from the execution path
  (`"${execution_file%.json}-list.json"`) rather than named outright

A contract test that passes on the broken workflow is worth nothing, so each
assertion was mutation-checked in an isolated worktree
(`mutation-transcript.txt`):

| Mutation | Caught by |
| --- | --- |
| M1 restore the shipped `*.json` glob | receipt coverage + report coverage + wildcard exclusion |
| M2 `**/*.json` recursive include | same three |
| M3 drop `worker-validation.json` | receipt coverage |
| M4 add raw `worker-job.json` | dump exclusion |
| M5 deploy script gains a 4th job kind, workflow untouched | receipt coverage |
| M6 drop `live-e2e-gate.json` | report coverage |

M5 is the drift case the allowlist introduces: a future Cloud Run Job whose
receipt nobody remembers to publish now fails CI instead of vanishing.

## 5. Verification

```
python3 -m pytest tests/ops/test_deploy_workflow_contract.py \
                 tests/ops/test_cloud_run_live_deployment.py
    -> 1 failed, 363 passed
python3 -m ruff check   tests/ops/test_deploy_workflow_contract.py <evidence script>
    -> All checks passed!
python3 -m ruff format --check <same>   -> 2 files already formatted
python3 -c "yaml.safe_load(open('.github/workflows/deploy-dev.yml'))"
    -> parsed OK; jobs: ['e2e-operational-evidence', 'deploy']; 13 deploy steps
```

The single failure is
`test_cloud_run_live_deployment.py::test_deploy_preflight_imports_runtime_dependencies_via_locked_python`:
`Error: required command 'uv' is not installed.` It reproduces unchanged on a
clean tree at the merge base (`git stash -u` → same failure), it exercises
`scripts/deploy_cloud_run_waji.sh`, which this task does not touch, and it is
an absent binary in the worker sandbox rather than a defect. CI installs `uv`
via `astral-sh/setup-uv@v5`; exact-head CI is the authority for it.

## 6. Out of scope, and one flagged gap

No deployment behaviour, Cloud Run traffic, Package 10 UI, API response, or
worker logic is touched. `scripts/deploy_cloud_run_waji.sh` and
`validate_cloud_run_live_deployment.py` are unchanged — the receipts were
already correct and already written; only their publication was broken.

**Flagged for a follow-up task:** `.github/workflows/deploy-staging.yml` line
154 still carries the same non-recursive `.odp_data/deployment/*.json` glob and
loses Job receipts identically. It is outside this task's writable paths, so it
is reported rather than fixed. The contract test is deliberately scoped to
deploy-dev; extending it to staging is the natural first step of that task.
