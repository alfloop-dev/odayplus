# Review Packet: ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001

- Sidecar task: `ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001`
- Sidecar owner: `Claude`
- Sidecar reviewer: `Antigravity4`
- Canonical parent owner: `Claude2`
- Canonical parent reviewer: `Antigravity4`
- Evidence captured: `2026-08-07` UTC
- Target / Parent branch: `origin/dev` / `origin/task/ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001`
- Key parent commit: `717e03e2f7ab9a841c757f71abac7f84c86f14fa` (= parent `approved_head`, = PR #677 head)
- Parent task status: `review_approved`, **not finalizable** — see § Parent PR Gate State.
- Scope: Review packet and evidence summary only; no L1 canonical truth or runtime code modified in this sidecar.

### Sidecar role history

This sidecar changed hands twice; the identities above are the current ones.

| Round | Sidecar owner | Sidecar reviewer | Outcome |
| --- | --- | --- | --- |
| 1 | `Antigravity4` | `Claude2` | Packet drafted, handed to review. |
| 2 | `Antigravity4` | `Claude` (helper-claimed 2026-08-07T01:52:40Z) | Reopened — three defects found. |
| 3 (current) | `Claude` (helper-claimed 2026-08-07T01:59:13Z) | `Antigravity4` | Defects fixed in this revision. |

Earlier revisions of this packet named `Claude2` as sidecar reviewer. That is stale
and has been corrected throughout.

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

**Bottom line for the reviewer**: the implementation evidence is green and
independently reproduced, but the parent is blocked at the CI gate and its scripts are
not yet on `dev`. Do not read this packet as clearance to finalize the parent — see
§ Parent PR Gate State.

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

3. **Protected Branch Defense** (single enforcement point, not two):
   Only `worker_commit.py` hard-blocks commits onto protected branches. It defines
   `PROTECTED_BRANCHES = {"dev", "main", "master"}` (line 55) and raises `CommitRefused`
   when the current branch is in that set and `--allow-protected-branch` was not passed
   (line 127), pointing the caller at `task_start.sh`.

   `task_start.sh` has **no** protected-branch commit guard. Its refusals are different
   and complementary: it refuses to switch branches when tracked files are modified
   (dirty-tree refusal, `--allow-dirty` to override) and refuses when the target
   `task/<TASK-ID>` branch is already checked out in another worktree (branch-busy
   check). It steers a worker *onto* a task branch; it cannot stop a commit once the
   worker is on `dev`.

   Practical consequence for reviewers: the protected-branch guarantee holds only for
   commits made through `worker_commit.py`. A raw `git commit` on `dev` is still
   unguarded locally — that path is caught by GitHub branch protection at push time,
   not by this script suite.

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

### 4. Independent Re-verification (sidecar, 2026-08-07)

The sidecar re-ran the same evidence from a clean archive of `717e03e2` rather than
trusting the owner's transcript. Note that the suite asserts on `.githooks/commit-msg`
as well as `scripts/git/`, so both paths must be extracted or 2 of the 46 tests fail
on a missing file:

```bash
D=$(mktemp -d)
git archive 717e03e2f7ab9a841c757f71abac7f84c86f14fa scripts/git .githooks | tar -x -C "$D"
cd "$D" && git init -q .
pytest scripts/git/test_git_task_scripts.py -q   # -> 46 passed in 3.33s
ruff check scripts/git                           # -> All checks passed!
```

Change surface re-confirmed against `git show --stat 717e03e2`: 9 files,
1450 insertions, 0 deletions, author `Antigravity6`. Index isolation
(`GIT_INDEX_FILE`), `PROTECTED_BRANCHES = {dev, main, master}`,
`FORBIDDEN_SCOPE_ENTRIES`, and head-branch PR discovery all read as described above.

**Local evidence is green. The parent is still not finalizable — see the next section.**

---

## Parent PR Gate State (blocking)

Local test and lint evidence being green does **not** mean the parent can close out.
As of `2026-08-07`, parent PR
[#677](https://github.com/alfloop-dev/odayplus/pull/677) is `OPEN` with
`mergeStateStatus: BLOCKED`.

| Check | Conclusion | Note |
| --- | --- | --- |
| `product-e2e-gate` | **FAILURE** | Required gate. Blocks merge. |
| `orchestrator` | CANCELLED | Same CI run, aborted. |
| `product` | CANCELLED | Same CI run, aborted. |
| `performance-gate` | SUCCESS | — |
| `task-review-gate` | SUCCESS | Review state is fine; CI is not. |

Supporting facts:

- PR head is `717e03e2f7ab9a841c757f71abac7f84c86f14fa`, identical to the parent task's
  `approved_head`. The approval and the PR are pointing at the same tree; nothing has
  drifted.
- `mergeable: MERGEABLE`, base `dev` — so this is **not** a conflict or a `BEHIND`
  base. `BLOCKED` here is purely the failing required check.
- `717e03e2` is **not** an ancestor of `origin/dev` (verified with
  `git merge-base --is-ancestor`). The scripts are still absent from `dev`, which is
  why `scripts/git/` does not exist in worker worktrees and why the skills' instructions
  to run `scripts/git/worker_commit.py` still cannot be followed.
- Parent task `next` field already records this:
  *"CI checks for task ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001 failed; resolve failing
  checks before finalization."*

### Failure is infrastructure, not a product regression

The `product-e2e-gate` job (`92676986853`, run `31119529670`, `ubuntu-latest`) reports
exactly one step, and that step is the one that failed:

```json
{"conclusion": "failure", "name": "product-e2e-gate",
 "steps": [{"name": "Set up job", "conclusion": "failure"}]}
```

The job died during runner provisioning and never executed a single test step. The
sibling `orchestrator` and `product` jobs in the same run were CANCELLED in the same
16:2x–16:4x UTC window on 2026-08-06. This signature — setup-step failure plus
same-run cancellations — is a CI platform failure, not a defect in the restored
scripts. No code change is indicated; the remedy is a re-run of the failed jobs once
CI capacity is healthy.

Reviewers should treat this as: **evidence green, gate red, cause external.** The
parent stays in `review_approved` until `product-e2e-gate` is re-run and passes; per
`.orchestrator/skills/task-closeout-finalization.md`, an open PR with failing checks is
explicitly not sufficient for `done`.

---

## Sidecar Boundary & Reviewer Handoff

- **Deliverable Scope**: This review packet (`support/sidecars/ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001/ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001-SIDECAR-REVIEW.md`) is the sole artifact created by `ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001-SIDECAR-REVIEW`.
- **L1 Canonical Safety**: Confirmed no L1 canonical docs modified. This sidecar touches
  only the file above; it did not modify `scripts/git/`, `.githooks/`, or any parent-task
  file.
- **Handoff Action**: Status transitioned to `review` via the canonical status utility,
  handing off to assigned sidecar reviewer `Antigravity4`.

### Open items for the parent owner (`Claude2`)

This sidecar cannot act on these — they belong to the parent lane:

1. Re-run the failed `product-e2e-gate` job on PR #677. The failure is a runner
   setup failure, so a re-run is the correct remedy; no code change is indicated.
2. Keep the parent at `review_approved` until that gate is green and the PR merges.
   `approved_head` (`717e03e2`) still matches the PR head, so the approval is intact —
   do **not** advance the base, which would invalidate the freeze.
3. Once merged, `scripts/git/` lands on `dev` and the worker instructions in
   `.orchestrator/skills/*` become executable for the first time. Until then, workers
   dispatched into fresh worktrees will continue to find `scripts/git/` missing.
