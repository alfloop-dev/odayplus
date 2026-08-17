# Review Packet: ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001

- Sidecar task: `ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001`
- Sidecar owner: `Antigravity`
- Assigned sidecar reviewer / parent reviewer: `Antigravity3`
- Parent owner: `Codex`
- Evidence captured: `2026-08-05` UTC
- Parent branch: `origin/task/ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001`
- Exact reviewed parent HEAD: `dbe0f10cb9f9d194ac32cabbac402c040d30451a`
- Key parent anchor commit: `2061383db62bbe46880e9d74135c94eec16af846`
- Scope: review packet and evidence summary only; no parent implementation or canonical truth modified.

## Executive disposition

Parent task `ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001` resolves a critical review-bus defect where GitHub review PR creation fell back to local status-root HEAD or an agent's workspace branch when the exact task branch was published on origin.

The implementation introduces exact origin ref resolution (`remote_branch_head_sha`), updates `review_branch_for_task` to check `task/<TASK-ID>` origin refs first, recovers immediately from cached `skipped_unpublished_branch` states once origin refs appear, and tracks `remote_ref` in `bus_state`.

All 30 unit tests in `.orchestrator/test_github_bus.py` pass cleanly in 0.04s, `py_compile` succeeds without warnings, and `git diff --check` is clean. The parent implementation at exact HEAD `dbe0f10c` is **REVIEW-APPROVED and ready for parent owner closeout**.

## Reviewed change surface

Compared with `origin/dev` at `865931a6`, parent branch `task/ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001` modifies two orchestrator control-plane files:

| File | Module Role | Review Observation |
| --- | --- | --- |
| `.orchestrator/github_bus.py` | GitHub ReviewBus coordinator | Replaces loose branch existence checks with `remote_branch_head_sha` using exact `ls-remote --heads` parsing. Prioritizes task-scoped origin branch over agent/status-root fallbacks. Recovers from cached `skipped_unpublished_branch` on origin ref discovery. Records `remote_ref` and remote `head_sha`. |
| `.orchestrator/test_github_bus.py` | Unit test suite | Adds test coverage for exact `ls-remote` ref parsing (`test_remote_branch_head_sha_requires_exact_origin_ref`), task origin ref resolution when owner branch differs (`test_upsert_review_pr_uses_task_origin_ref_when_status_root_and_owner_branch_differ`), and false unpublished state recovery (`test_upsert_review_pr_recovers_false_unpublished_state_from_task_origin_ref`). |

No L1 canonical documents, runtime contracts, database schemas, or product application files are touched by this change.

## Feature & Contract Verification Matrix

| Functionality / Boundary | Pre-change behavior | Post-change behavior | Verification Status |
| --- | --- | --- | --- |
| **Exact Remote Ref Matching** | `ls-remote` string matching could match branch prefixes (e.g. `task/FOO-SIDECAR`) | Matches exact `refs/heads/<branch>` string from `ls-remote --heads` | Verified (`test_remote_branch_head_sha_requires_exact_origin_ref`) |
| **Task Origin Branch Priority** | Selected local worktree `branch_exists` or status-root agent branch | Checks remote origin ref `task/<TASK-ID>` first before fallback | Verified (`test_upsert_review_pr_uses_task_origin_ref_when_status_root_and_owner_branch_differ`) |
| **Unpublished Cache Recovery** | Remained in cached `skipped_unpublished_branch` for TTL window even after branch push | Evaluates `remote_branch_head_sha` and clears skip state immediately upon origin push | Verified (`test_upsert_review_pr_recovers_false_unpublished_state_from_task_origin_ref`) |
| **Branch Diff Evaluation** | Diff checked local base vs local branch | `branch_has_diff` checks `origin/{base}` and `{base}` against exact remote `head_sha` | Verified (30/30 unit tests pass) |

## Independent verification at exact parent HEAD

The following commands were run in the workspace at parent HEAD `dbe0f10cb9f9d194ac32cabbac402c040d30451a`:

```bash
python3 -m unittest discover -s .orchestrator -p 'test_github_bus.py'
# Ran 30 tests in 0.040s - OK

python3 -m py_compile .orchestrator/github_bus.py .orchestrator/test_github_bus.py
# Clean (0 errors)

git diff --check
# Clean (0 whitespace/formatting errors)
```

## Reviewer attention points

1. **No Stale Branch Drift**: `remote_branch_head_sha` queries `ls-remote` directly at runtime, ensuring that background workers running in isolated worktrees always operate against the true origin ref rather than local worktree state.
2. **Backward Compatibility**: Local branch fallback paths remain intact for local un-pushed development branches while prioritizing origin task branches for automated ReviewBus workflows.
3. **State Audit Integrity**: `bus_state` records `remote_ref` (`refs/heads/task/<TASK-ID>`) alongside `head_sha` for durable auditing.

## Recommended reviewer disposition

- **RECOMMENDATION**: APPROVE parent task `ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001`.
- The implementation is narrow, clean, fully tested, and correctly resolves origin ref provenance issues in `github_bus.py`.

## Sidecar boundary and handoff

This sidecar artifact (`ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001-SIDECAR-REVIEW.md`) is a non-canonical support review packet.
It has been handed off to assigned reviewer `Antigravity3`. Parent owner `Codex` can proceed with closeout and PR merge.
