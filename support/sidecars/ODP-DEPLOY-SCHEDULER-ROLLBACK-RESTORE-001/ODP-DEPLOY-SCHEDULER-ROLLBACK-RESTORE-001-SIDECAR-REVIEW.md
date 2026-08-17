# ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001 review packet

Prepared by: Codex2

Parent owner: Antigravity6

Sidecar reviewer / packet recipient: Antigravity6

Inspected: 2026-08-02

Scope: support-only review packet; no canonical runtime, contract, registry, governance, or L1 truth was changed.

## Review disposition

**Not ready for parent approval at the inspected head.** The local parent candidate is anchor commit `cb043c972106fbde9696cf9b95c09fc04bb90625`. It is one commit ahead of and 40 commits behind `origin/dev`, has no matching remote task branch or task PR, and has not received exact-head CI or the task's required independent review.

The focused unit/static checks are green, but two direct fault injections show that restoration can report success after a failed readback or failed deletion. The checked-in JSON evidence is also not linked to a workflow run or exact release SHA and differs materially from the observed Deploy Dev configuration. It must not be treated as durable live rollback proof.

## Candidate and source inventory

Parent anchor commit:

- `cb043c972106fbde9696cf9b95c09fc04bb90625`
- Subject: `ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001: anchor scheduler trigger restoration repair`
- Changed surfaces: `product_ops/deployment/cloud_scheduler_trigger.py`, `product_ops/deployment/cloud_run_release_traffic.sh`, scheduler-focused additions to `tests/ops/test_cloud_run_live_deployment.py`, and six files under `docs/evidence/runtime/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001/`.
- No change in the candidate writes scheduler pre/post restore receipts to a durable workflow artifact path. `restore_scheduler_trigger` uses a temporary readback file and deletes it; `.github/workflows/deploy-dev.yml` does not upload scheduler restore snapshots or equality results.

Observed failure runs:

| Run | Exact dev SHA | What the durable log proves | What it does not prove |
| --- | --- | --- | --- |
| [30745285034](https://github.com/alfloop-dev/odayplus/actions/runs/30745285034) | `80ba278623b8d4ad4ce81ea749a5aee030e5c18d` | Live E2E failed; prior API/Web traffic was restored; rollback then logged `Restoring the recorded Cloud Scheduler trigger targets` followed by the aggregate restore failure. | The log has no per-trigger command/result, so it does not identify which trigger(s) or exact failing `gcloud` invocation. |
| [30747676117](https://github.com/alfloop-dev/odayplus/actions/runs/30747676117) | `96f94cda56d509f44eb5929997b3ab7a67f1c65c` | Same aggregate scheduler restoration failure after API/Web rollback. | Same missing per-trigger diagnosis. |
| [30751698299](https://github.com/alfloop-dev/odayplus/actions/runs/30751698299) | `aff272d3da55967497d2aba0e72d569b9b15ff70` | A later exact-dev-SHA deployment failed closed for independent MLflow/RBAC gates. | This SHA predates and does not contain parent anchor `cb043c97`; it cannot verify the proposed scheduler repair. |

At both failure SHAs, the restore implementation obtains fields and then calls `gcloud scheduler jobs update http`, in this order: `oday-scheduler-trigger`, then `oday-worker-trigger`. Because the log only emits an aggregate error, it is not evidence that one particular trigger or flag failed. The parent evidence README's asserted OIDC-schema root cause is also not established by these logs: run `30745285034` visibly reports deployed scheduler jobs using `httpTarget.oauthToken` and the `oday-dev-scheduler@...` account. A per-trigger trace, Cloud Audit Log record, or faithful reproduction is still required.

## Acceptance matrix

| Parent acceptance criterion | Status at `cb043c97` | Evidence / required follow-up |
| --- | --- | --- |
| Identify the exact failure in runs `30745285034` and `30747676117`, including trigger and `gcloud` operation | **Not met** | Runs prove only aggregate scheduler restore failure. Add per-trigger start/result/exit diagnostics and establish the exact failing operation from a faithful reproduction or audit log. Do not promote the current OIDC explanation as observed fact. |
| Capture the complete pre-deploy contract: URI, auth, method/body, schedule/time zone, retry, paused state | **Partially implemented; live evidence absent** | Helper supports these fields. Checked-in JSON has no run ID, exact SHA, artifact ID, capture command, or provenance. Its cron and service-account values differ from the observed Deploy Dev environment, so it cannot serve as the exact-run snapshot. |
| Restore both triggers with supported commands, exact redacted equality, and idempotence | **Not met** | Unit paths pass, but readback describe/validation failure is accepted as success; delete failure for an originally absent trigger is swallowed. No exact-head live proof or idempotent retry drill exists. |
| A failure on one trigger still attempts the other, emits per-trigger diagnostics, and remains failed closed | **Partially met in unit test** | `test_scheduler_trigger_restore_partial_failure_continues_and_reports_diagnostics` proves the caller attempts trigger 2 after a simulated trigger-1 update failure. Additional tests must cover readback failure, delete failure, resume failure, and ambiguous existence checks. |
| Candidate API/Web traffic remains zero; prior traffic and scheduler targets are proven restored | **Historical traffic evidence only** | Failure-run logs show the previous service revisions returned to 100% and candidate revisions at 0%. There is no scheduler readback receipt tied to the candidate head, and no live run containing `cb043c97`. |
| Focused tests cover success, partial failure, missing trigger, quoting/body, OIDC, idempotent retry; exact pushed head has CI and independent review | **Not met** | Six scheduler tests pass, but quoting/body fidelity and idempotent retry mutation are not demonstrated. There is no remote task head, PR, exact-head CI, or independent review. |
| Exact-dev-SHA live rollback drill produces durable redacted receipts with zero drift | **Not met** | The repository JSON files are static and unattributed. No workflow run contains the repair, and the workflow does not durably upload scheduler restore receipts. |

## Blocking findings

### 1. Readback failure is fail-open

In `product_ops/deployment/cloud_run_release_traffic.sh`, lines 154-167 at the anchor head compare snapshots only inside a compound success condition. If `gcloud scheduler jobs describe` fails, produces an empty file, or produces a snapshot that fails validation, execution skips comparison and reaches the success return.

Direct fault injection against the anchor head made the readback `describe --format=json` return 42. Observed result:

```text
Restoring Cloud Scheduler trigger 'oday-scheduler-trigger'...
Cloud Scheduler trigger 'oday-scheduler-trigger' successfully restored.
readback_failure_restore_status=0
```

Required change: treat describe, non-empty, validation, and equality as four mandatory fail-closed gates with per-trigger diagnostics. Add a regression test that expects non-zero for each failure mode.

### 2. Originally absent trigger deletion is fail-open

Lines 104-110 run `gcloud scheduler jobs delete ... || true` and immediately return success. This conflates an idempotent NOT_FOUND outcome with authentication, authorization, transport, and service failures.

Direct fault injection made delete return 43. Observed result:

```text
Trigger 'absent-trigger' was absent prior to deploy; deleting candidate if present...
delete_failure_restore_status=0
```

Required change: first establish absence with a supported readback or classify only an explicit NOT_FOUND as success. All other delete failures must be diagnosed and returned non-zero.

### 3. Trigger existence and resume errors are also collapsed

The current `describe` probe treats every non-zero result as "missing" and switches to `create`; it cannot distinguish NOT_FOUND from permission or transient failures. The ENABLED path also runs `resume ... || true`. Both paths violate the requirement that rollback restoration itself remain observable and fail closed.

Required change: classify errors explicitly, propagate non-NOT_FOUND failures, and verify the final state through mandatory readback.

### 4. Equality normalization changes or hides contract values

`generate_restore_args` and `redact_snapshot` turn a missing/empty POST body into `{}` and synthesize a `Content-Type` header. That may be a useful deployment default, but it is not exact restoration, and applying the same normalization before comparison can hide the mutation. Binary/non-UTF-8 bodies also fall back to passing the base64 text as the message body, which is not proven byte-equivalent.

Required change: preserve the captured body bytes and header presence exactly (for example, through a controlled temporary body file), or explicitly narrow the accepted contract and prove semantic equivalence. Add quoting, delimiter, empty-body, Unicode/binary, repeated-header, and retry-zero cases.

### 5. Evidence provenance conflicts with the live records

The checked-in pre/post JSON files name `oday-cloud-scheduler@...`, use worker schedule `0 * * * *` and scheduler schedule `*/15 * * * *`, and point at `79cf9b67e62c` jobs. The observed Deploy Dev logs use the `oday-dev-scheduler@...` account and workflow schedules worker `*/5 * * * *` / scheduler `0 * * * *`. The JSON files contain no run ID, exact SHA, artifact ID/digest, capture command, or generator metadata.

Required change: generate redacted receipts during an exact-head drill, retain both raw-on-runner comparison inputs and a safe published normalization, attach artifact IDs/digests, and link them from the evidence README. Static illustrative fixtures must be labelled as such.

## Verified checks

Executed independently in the clean parent worktree at `cb043c97`:

```text
.venv/bin/pytest -q tests/ops/test_cloud_run_live_deployment.py -k scheduler_trigger
# 6 passed

.venv/bin/ruff check product_ops/deployment/cloud_scheduler_trigger.py tests/ops/test_cloud_run_live_deployment.py
# All checks passed

bash -n product_ops/deployment/cloud_run_release_traffic.sh
bash -n product_ops/deployment/deploy_cloud_run_waji.sh
# passed
```

Additional read-only review checks:

```text
git diff --stat origin/dev...task/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001
git ls-remote --heads origin '*ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001*'
gh pr list --state all --search 'ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001'
gh run view 30745285034 --json headSha,headBranch,status,conclusion,url,jobs
gh run view 30747676117 --json headSha,headBranch,status,conclusion,url,jobs
gh run view 30751698299 --json headSha,headBranch,status,conclusion,url,jobs
```

## Reviewer handoff

Antigravity6 should use this packet to update the parent branch, not merge this support artifact into canonical truth automatically. Before requesting parent approval:

1. Rebase or refresh the parent candidate onto current `dev` and resolve the 40-commit drift.
2. Repair the fail-open paths above and add focused regressions for readback, delete, resume, ambiguous existence, exact body/header fidelity, and repeated/idempotent rollback.
3. Add durable redacted scheduler restore receipts to the Deploy Dev artifact allowlist with exact SHA, run URL, artifact ID/digest, per-trigger outcomes, traffic before/after, and equality result.
4. Push the exact parent head, open the task PR, and obtain CI plus independent review from the assigned reviewer (the reviewer must not be the parent owner).
5. Run an exact-head live rollback drill. Keep candidate traffic at zero on failure and prove both prior trigger contracts and prior service traffic through post-rollback readback.

This sidecar packet makes no approval claim for the parent task.
