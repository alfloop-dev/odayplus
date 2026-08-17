# ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001: Cloud Run Job receipt publication closeout

Owner: Claude3 · Reviewer: Antigravity5 · Phase: Deployment Evidence ·
2026-07-29

Publish the structured Cloud Run Job validation receipts that Deploy Dev
already writes, through an explicit allowlist that cannot carry secrets.

Full runtime detail:
`docs/evidence/runtime/ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001/` (README §1–§6 plus
`simulate_artifact_selection.py`, `mutation-transcript.txt`).

## 1. The gap

Deploy Dev run
[30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086)
(head `93ae1b2e75e1056c2bfeccd1d59e25e354f4f21f`) published artifact
`cloud-run-dev-validation` containing exactly three files —
`cloud-run-preflight.json`, `cloud-run-smoke.json`,
`cloud-run-migration-compatibility.json`. The same run's log shows it had
written three more, all passing, minutes earlier:
`cloud-run-jobs/{migration,scheduler,worker}-validation.json`.

`path: .odp_data/deployment/*.json` is not recursive, so the entire
`cloud-run-jobs/` receipt tree was never a candidate, and
`if-no-files-found: ignore` kept the loss silent. That run carried the
ODP-DEPLOY-WORKER-JOB-EXECUTION-001 worker fix, so the receipts proving the
worker Job finally executed cleanly are precisely the ones that were lost.

**Verdict: an artifact-path defect. The receipts were correct and already
written; only their publication was broken.**

## 2. What shipped

- `.github/workflows/deploy-dev.yml` — the upload step's glob becomes an
  explicit seven-entry allowlist: the four validator reports it already
  published (preflight, smoke, migration-compatibility, live-e2e-gate) plus
  the three Cloud Run Job receipts it dropped. Nothing else in the workflow
  changes.
- `tests/ops/test_deploy_workflow_contract.py` — 8 tests that parse the
  workflow as YAML and derive the expected file set from
  `scripts/deploy_cloud_run_waji.sh`, so the allowlist and the receipt writers
  cannot drift apart.

An allowlist rather than a recursive include: `cloud-run-jobs/` also holds the
raw `gcloud run jobs describe` / `executions describe` / `executions list`
dumps (`*-job.json`, `*-execution.json`, `*-execution-list.json`, nine files on
a green deploy), which restate the deployed env block and its secret selectors
verbatim. `**/*.json` would have published all nine.

## 3. Nothing sensitive is uploaded

- **Closed set.** Every entry is a literal path — no wildcard, no exclusion
  pattern, nothing outside `.odp_data/deployment/`, no `..`. A file that is not
  named cannot be published, including one added later. Env files, traffic
  snapshots, candidate descriptions, and the operator bearer token live in
  `mktemp` files outside the repository tree and are removed by `cleanup()`.
- **Redacted contents.** Every allowlisted file is validator-written and
  carries `secret_values_redacted: true`: check names, outcomes, and env-var
  *names* only, never a bound value. Confirmed against the real artifact from
  run 30436771086, where a bearer/token/password/api-key/JWT scan matches only
  env-var names in the preflight check list.
- `simulate_artifact_selection.py` records the selection over a 19-file green-
  deploy tree: 3 receipts recovered, 4 reports preserved, 9 raw dumps and 3
  decoys excluded. It also shows the old glob publishing two decoys it had no
  reason to — the glob was open at the top as well as blind below it.

## 4. Verification

```
python3 -m pytest tests/ops/test_deploy_workflow_contract.py \
                 tests/ops/test_cloud_run_live_deployment.py   -> 1 failed, 363 passed
python3 -m ruff check / format --check (new files)             -> clean
python3 -c "yaml.safe_load(.github/workflows/deploy-dev.yml)"  -> parsed OK
```

Each contract assertion was mutation-checked in an isolated worktree: the
shipped glob, a `**/*.json` include, a dropped receipt, an added raw dump, a
fourth job kind added to the deploy script, and a dropped top-level report all
fail the suite. Transcript in `mutation-transcript.txt`.

The one failure —
`test_deploy_preflight_imports_runtime_dependencies_via_locked_python`,
`required command 'uv' is not installed` — reproduces unchanged on a clean tree
and exercises a file this task does not touch. Exact-head CI, which installs
`uv`, is the authority for it.

## 5. Scope

No deployment behaviour, Cloud Run traffic, Package 10 UI, API response, or
worker logic changed. `scripts/deploy_cloud_run_waji.sh` and
`scripts/deployment/validate_cloud_run_live_deployment.py` are untouched.

**Flagged, not fixed:** `.github/workflows/deploy-staging.yml` (line 154)
carries the same non-recursive glob and loses Job receipts identically. It is
outside this task's writable paths and needs its own task; extending the
contract test to staging is that task's natural first step.
