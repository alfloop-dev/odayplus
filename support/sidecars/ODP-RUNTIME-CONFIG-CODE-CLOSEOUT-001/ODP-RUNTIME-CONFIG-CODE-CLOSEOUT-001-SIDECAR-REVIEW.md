# ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001 review packet

Prepared by: `Codex8`

Base-advance updates by: `Claude2` (sidecar owner after helper claim; reviewer `Codex4` preserved)

Sidecar task: `ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001-SIDECAR-REVIEW`

Parent task: `ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001`

Canonical parent owner / reviewer at inspection: `Antigravity2` / `Claude`

Sidecar reviewer: `Codex4`

Packet recipient / parent owner: `Antigravity2`

Inspected: `2026-08-08` UTC

Last re-verified: `2026-08-08` UTC against `origin/dev` at `7430ba85`

Scope: support-only review packet and evidence summary. This sidecar does not
modify L1 canonical truth, runtime code, registry code, governance policy, or the
parent implementation.

## Review disposition

**Ready for parent-owner finalization; no blocking finding in the scoped review.**

The exact approved parent head
`f7bd3d9b2d52c5f4eb3864c963379feea6836c90` is merged into `dev` by PR
[#714](https://github.com/alfloop-dev/odayplus/pull/714), merge commit
`40fb18eb82cf03e521213ae3f10b23108e048c73`. GitHub reports all five recorded
checks successful (`orchestrator`, `product`, `performance-gate`,
`product-e2e-gate`, and `task-review-gate`). Canonical task state records the
same SHA as both `review_gate_sha` and `approved_head`.

**Parent state update (later than the original inspection).** The parent task has
since been finalized. Canonical status no longer lists it as active; it is
archived at `ai-task-archive/tasks/ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001.json`
with `terminal_status: done`, `terminal_outcome: completed`, archived
`2026-08-08T15:08:15Z`, owner `Antigravity2`, reviewer `Claude`. This packet is
therefore retrospective supporting evidence, not a gate on the parent closeout.
Its review disposition and evidence boundaries below are unchanged and remain
accurate for the approved parent head.

Independent focused checks pass on the current `origin/dev` descendant. The
implementation meets the five parent acceptance criteria at the repository
contract level. The evidence boundaries below should remain explicit: the
parent changes do not constitute a new live rollback drill, and two consumers
are established by source inspection plus their existing focused tests rather
than by the new all-roles test alone.

## Reviewed parent surface

The parent commit changes these repository-owned layers:

| Layer | Files | Delivered behavior |
| --- | --- | --- |
| Shared runtime configuration | `shared/runtime_config.py` | Defines one release-identity precedence order and tenant resolution helper. |
| Runtime consumers | API, Worker, Scheduler, Notifications, Cloud Run Job Entrypoint | Routes release identity reads through `get_release_identity()`. |
| Deploy configuration | `product_ops/deployment/deploy_cloud_run_waji.sh` | Requires a tenant value before deployment and writes both scheduler and generic tenant keys from the selected value. |
| Contract verification | `tests/ops/test_runtime_config_code_closeout.py`, `tests/ops/test_cloud_run_live_deployment.py` | Adds release hierarchy, role-consumer, tenant fail-closed, deploy precondition, and rollback-symbol coverage. |
| Completion evidence | `docs/evidence/completion/ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001/` | Supplies implementation, verification, and closeout summaries. |

No L1 architecture or policy document is changed by the parent commit.

## Acceptance and evidence matrix

| Parent acceptance criterion | Review result | Evidence and boundary |
| --- | --- | --- |
| All runtime roles consume one release identity | **Met** | `get_release_identity()` prioritizes `ODAY_RELEASE_SHA`, then the four documented compatibility sources. API, Scheduler, Worker, Notifications, and Job Entrypoint call it. The new `test_all_runtime_roles_consume_unified_release_identity` dynamically asserts API/Scheduler/Worker; Notifications and Job Entrypoint are confirmed by source inspection and focused existing tests run below. Web receives the same deployment SHA through `NEXT_PUBLIC_ODAY_RELEASE_SHA` in the deploy script. |
| Required environment values fail closed | **Met for the parent deploy-script boundary** | `deploy_cloud_run_waji.sh` exits before mutation when both tenant variables are absent, and its env-file serializer independently rejects the same condition. Workflow-level dev/staging tenant defaults remain outside the changed parent surface, so this does not prove a global no-default policy. |
| Scheduler tenant configuration is wired | **Met** | The serializer writes both `ODP_SCHEDULED_INGESTION_TENANT_ID` and `ODP_TENANT_ID` from the selected required value; the scheduler ingestion path consumes those environment keys. `resolve_tenant_id(required=True)` is fail-closed, although the shared helper itself is not yet the scheduler's active resolver. |
| Rollback targets are explicit | **Met at repository contract level** | Traffic and scheduler-trigger snapshots are captured before deployment mutation; rollback flags and restore calls name the recorded API, Web, Scheduler, and Worker targets. The new test verifies this structure by source assertions. It is not fault-injection, readback-equality, or exact-SHA live rollback evidence. |
| Repo-owned deploy contract tests and evidence are delivered | **Met** | The new contract module has five tests; the parent evidence directory contains `implementation.md`, `verification.md`, and `closeout.md`. Independent reruns and merged-head CI are green. |

## Evidence-quality notes

These are non-blocking scope notes for accurate parent closeout language:

1. `test_all_runtime_roles_consume_unified_release_identity` says it verifies
   Notifications and Entrypoint, but its body directly exercises only API,
   Scheduler, and Worker. The other two consumers are visible in the parent diff;
   existing notification and job-entrypoint tests also pass independently.
2. `test_deploy_script_contains_explicit_rollback_targets` proves that named
   snapshot, arm, and restore hooks exist. It must not be cited as a successful
   live rollback or exact restore-readback drill.
3. The workflow currently supplies explicit `tenant-dev` / `tenant-staging`
   defaults. Therefore the fail-closed claim is precise only at the deploy script
   boundary after workflow environment resolution.
4. Parent evidence files retain the original `Antigravity3` / `Claude2`
   attribution. Canonical status now assigns `Antigravity2` / `Claude` after
   reassignment. This is historical attribution drift, not an implementation
   failure; the finalizer should use canonical status for the current ownership
   record.

## Independent verification

Re-executed after the second mandatory base advance requested by `Codex4`. The
verified tree contains merge commit `2c9d6f65`, which composes `origin/dev` at
`7430ba85`, retains the earlier compose `48df99eb` and both previously pushed
sidecar heads `96999e0c` and `3b4cb332`, and contains parent merge commit
`40fb18eb`. The merge was a non-destructive `git merge origin/dev`; no history
was reset, discarded, or force-pushed:

```text
uv run pytest -q tests/ops/test_runtime_config_code_closeout.py tests/ops/test_deploy_workflow_contract.py
# 21 passed

uv run pytest -q tests/ops/test_cloud_run_live_deployment.py -k test_deploy
# 9 passed

uv run pytest -q tests/ops/test_cloud_run_job_entrypoint.py
# 12 passed

uv run pytest -q tests/reliability/test_runtime_observability.py -k round8_oncall_adapter_authenticity_and_sha_enforced
# 1 passed

bash -n product_ops/deployment/deploy_cloud_run_waji.sh
# exit 0

uv run ruff check shared/runtime_config.py apps/api/oday_api/main.py apps/scheduler/oday_scheduler/main.py apps/worker/oday_worker/main.py modules/notifications/infrastructure/adapters.py product_ops/deployment/cloud_run_job_entrypoint.py tests/ops/test_runtime_config_code_closeout.py tests/ops/test_cloud_run_live_deployment.py
# All checks passed

git diff --check
# clean

git merge-base --is-ancestor f7bd3d9b2d52c5f4eb3864c963379feea6836c90 origin/dev
# exit 0

git merge-base --is-ancestor origin/dev HEAD
# exit 0

git merge-base --is-ancestor 96999e0c97d5ad70e0e67094f553eed846141039 HEAD
# exit 0

git merge-base --is-ancestor 3b4cb332d82e2fc577d433c87aa6a0bb00f01c01 HEAD
# exit 0

test "$(git diff --name-only origin/dev...HEAD)" = "support/sidecars/ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001/ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001-SIDECAR-REVIEW.md"
# exit 0
```

43 focused tests pass (21 + 9 + 12 + 1). Static, ancestry, and scope checks all
exit 0.

## Sidecar closeout note

`Codex4` approved the support-only packet at exact pushed head `96999e0c`. Two
mandatory base advances followed:

1. `origin/dev` at `2a3336c8`, merge-composed as `48df99eb`, packet update
   `3b4cb332`.
2. `origin/dev` at `7430ba85`, merge-composed as `2c9d6f65` after `Codex4`
   reopened the task on `2026-08-08T16:53:45Z` because `3b4cb332` did not contain
   the then-current base, plus this packet update.

Both composes preserve the full task lineage and a normal non-force push path.
The 43 focused tests and all static, ancestry, and scope checks above were rerun
on the composed tree before this support-only metadata update.

PR #721 remains open. On the previous head `3b4cb332` the four CI checks
`orchestrator`, `performance-gate`, and `product-e2e-gate` were successful with
`product` still in progress, and `task-review-gate` reported failure because the
reopen moved the task out of review. Both resolve on the new pushed head once
`Codex4` re-reviews. Because this commit advances the branch beyond `3b4cb332`,
`Codex4` must confirm the new exact pushed head before PR #721 can enter the
merge queue and before the owner can mark the sidecar `done` after merge.

## Parent-owner closeout handoff

The parent no longer needs this packet as a closeout input: `Antigravity2` and
`Claude` already finalized `ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001` to `done` on
`2026-08-08T15:08:15Z`, and PR #714 remains `MERGED` at merge commit
`40fb18eb82cf03e521213ae3f10b23108e048c73` (merged `2026-08-08T13:00:06Z`).

The packet therefore lands as retained review evidence. Its residual value is the
four evidence-quality notes above, which stay open as accuracy constraints on how
the delivered parent work is described — in particular, that the rollback test is
a structural source assertion rather than a live drill, and that the fail-closed
claim holds at the deploy-script boundary rather than globally. Anyone citing
this parent's evidence later should carry those boundaries forward.

This sidecar makes no new canonical contract claim and does not replace the
parent's independent approval. Its sole deliverable is this review packet.
