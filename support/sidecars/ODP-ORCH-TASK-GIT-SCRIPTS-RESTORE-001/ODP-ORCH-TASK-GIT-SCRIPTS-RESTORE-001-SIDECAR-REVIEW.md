# Review Packet: ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001

- Sidecar task: `ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001`
- Sidecar owner: `Antigravity4`
- Sidecar reviewer: `Claude2`
- Canonical parent owner: `Claude2`
- Canonical parent reviewer: `Antigravity4`
- Evidence captured: `2026-08-07` UTC
- Target / Parent branch: `origin/dev` / `origin/task/ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001`
- Key parent commit: `717e03e2f7ab9a841c757f71abac7f84c86f14fa`
- Scope: Review packet and evidence summary only; no L1 canonical truth or runtime code modified in this sidecar.

---

## Executive Summary & Background

Parent task `ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001` restores the per-task git workflow helper suite under `scripts/git/` and the associated `.githooks/commit-msg` hook.

### Operational Context & Root Cause

1. **Contract Inconsistency**:
   Every background worker wakeup prompt, both closeout skills (`.orchestrator/skills/worker-anchor-commit.md`, `.orchestrator/skills/task-closeout-finalization.md`), `.orchestrator/auto_commit_archive.py`, `.orchestrator/watch_events.py`, and `scripts/orchestrator/diagnose_finalize_lane_remediation.py` explicitly instruct agents to use `scripts/git/task_start.sh`, `worker_commit.py`, and `task_finalize.sh`.

2. **Missing Canonical Directory**:
   `scripts/git/` did not exist on `origin/dev`. Prior history contained only transient 31-line implementations (`cbc10c8c`, `28909226`) that were subsequently removed.

3. **Shared-Index Vulnerability Remediation**:
   Without `worker_commit.py`, background workers risk staging files outside their task scope or inheriting dirty staging left behind by concurrent/interrupted worker execution (the 2026-05-16 sweep-in incident `e06f5cf2`).

Parent commit `717e03e2` re-author-restores these scripts against their existing interface contracts, backed by 46 unit tests and static lint checks.

---

## Reviewed Change Surface

Parent task commit `717e03e2` introduces 9 files without altering L1 canonical architecture documents or core runtime contracts:

| File | Subsystem Role | Implementation Summary |
| --- | --- | --- |
| `.githooks/commit-msg` | Git Hook | Executable commit-msg hook enforcing standard Pantheon commit trailers (`LLM-Agent`, `Task-ID`, `Reviewer`) and subject rules via `check_commit_trailers.py`. |
| `scripts/git/README.md` | Documentation | Comprehensive operational guide for per-task git workflow scripts and operating rules. |
| `scripts/git/check_commit_scope.py` | Scope Guard | Validates that staged files stay strictly within declared task scope boundaries; rejects whole-worktree wildcards (`.`, `-A`, `*`). |
| `scripts/git/check_commit_trailers.py` | Trailer Guard | Validates commit subject formatting/length and enforces required metadata trailers (`LLM-Agent`, `Task-ID`, `Reviewer`). |
| `scripts/git/install_hooks.sh` | Installer | Shell script enabling opt-in installation of repository git hooks. |
| `scripts/git/task_start.sh` | Branch Launcher | Idempotently creates or resumes `task/<TASK-ID>` from `dev` tip, refusing worktrees with uncommitted dirty changes belonging to other tasks. |
| `scripts/git/task_finalize.sh` | PR & Merge Trigger | Pushes task branch, auto-discovers PR by head branch (per `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001`), undrafts, and enables GitHub auto-merge. |
| `scripts/git/worker_commit.py` | Worker Commit Engine | Stages into a private `GIT_INDEX_FILE` seeded from `HEAD`, verifies scope and trailers before committing, blocks protected branches (`dev`, `main`, `master`), rejects empty commits, and resets scoped index entries. |
| `scripts/git/test_git_task_scripts.py` | Test Suite | Comprehensive unit test suite (46 tests) covering branch creation, index isolation, scope validation, trailer checks, PR discovery, and error guards. |

---

## Safety & Architectural Boundary Compliance

1. **Zero L1 Mutation**:
   No changes were made to L1 canonical architecture documents (`TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, etc.), governance rules, or primary business logic.

2. **Index Isolation Guard**:
   `worker_commit.py` guarantees multi-tenant safety by initializing a private per-task index (`GIT_INDEX_FILE=/tmp/git-index-task-<TASK-ID>`). Concurrent workers operating in shared worktrees will not pollute or swallow each other's staged files.

3. **Protected Branch Defense**:
   `worker_commit.py` and `task_start.sh` hard-block direct commits to `dev`, `main`, and `master`, enforcing the per-task ephemeral branch + PR auto-merge pipeline.

4. **ReviewBus & PR Discovery Alignment**:
   `task_finalize.sh` resolves open PRs using exact head branch matching (`head: task/<TASK-ID>`), fully compatible with `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001`.

---

## Verification & Test Execution Evidence

The implementation on parent commit `717e03e2` was verified using the repository test runner:

### 1. `test_git_task_scripts.py` Unit Test Suite
```bash
/tmp/odp-oss-review-BpLhRf/.venv/bin/pytest scripts/git/test_git_task_scripts.py
```
**Output**:
```text
collected 46 items

scripts/git/test_git_task_scripts.py .............................. [ 30%]
................................                                     [100%]

============================== 46 passed in 4.10s ==============================
```

### 2. Static Analysis & Lint Check
```bash
/tmp/odp-oss-review-BpLhRf/.venv/bin/ruff check scripts/git
```
**Output**:
```text
All checks passed!
```

### 3. Git Diff & Whitespace Verification
```bash
git diff --check
```
**Output**: Clean.

---

## Sidecar Boundary & Reviewer Handoff

- **Deliverable Scope**: This review packet (`support/sidecars/ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001/ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001-SIDECAR-REVIEW.md`) is the sole artifact created by `ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001-SIDECAR-REVIEW`.
- **L1 Canonical Safety**: Confirmed no L1 canonical docs modified.
- **Handoff Action**: Status transitioned to `review` via canonical status utility, handing off review to assigned reviewer `Claude2`.
