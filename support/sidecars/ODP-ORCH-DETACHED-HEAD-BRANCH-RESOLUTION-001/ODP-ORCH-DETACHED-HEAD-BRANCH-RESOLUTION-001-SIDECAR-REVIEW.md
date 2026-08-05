# Sidecar Review Packet: ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW

- **Task ID**: `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`
- **Parent Title**: Stop reporting HEAD as a branch name in ReviewBus branch resolution
- **Helper Kind**: `review_packet`
- **Task Owner**: `Antigravity`
- **Reviewer**: `Codex2`
- **Phase**: Orchestrator reliability
- **Last Updated**: 2026-08-05

---

## Executive Summary

This support sidecar document provides a structured review packet, defect analysis, implementation assessment, and acceptance verification matrix for parent task `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`.

The parent task fixes a key orchestrator bug where detached HEAD checkouts (such as isolated worker worktrees or temporary git contexts) reported the string `"HEAD"` as their branch name, causing ReviewBus branch resolution to record `"HEAD"` as a valid review branch and preventing proper task PR/review-gate processing.

This sidecar task is support-only. It creates only support artifacts without modifying canonical L1 documents, core contracts, or primary runtime/orchestrator implementations.

---

## 1. Defect Analysis & Root Cause

### Background & Context
When Git is executed in a detached HEAD state (e.g. `git checkout --detach <SHA>`), the standard command `git rev-parse --abbrev-ref HEAD` outputs the literal string `"HEAD"`.

### Root Cause Flow
1. **False Branch Claim**: `.orchestrator/github_bus.py::current_branch()` previously called `git rev-parse --abbrev-ref HEAD`. In a detached HEAD state, this returned `"HEAD"`.
2. **False Branch Validation**: Downstream functions such as `branch_exists("HEAD")` executed `git show-ref --verify refs/heads/HEAD`. Because `HEAD` is a valid Git symbolic ref, `git show-ref` or `git rev-parse` resolved `HEAD` successfully.
3. **Flawed Branch Resolution**: `review_branch_for_task()` evaluated `"HEAD"` as truthy and distinct from default branch `"dev"`, recording `"HEAD"` into task status or bus records as the task's review branch.
4. **Impact**: ReviewBus missed matching real task branches, sidecar tasks were marked as having unpublished branches (`"HEAD"`), and automated `task-review-gate` triggers failed to execute.

---

## 2. Parent Implementation Assessment

Parent task `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001` resolved this flaw across `.orchestrator/github_bus.py` and added comprehensive unit tests in `.orchestrator/test_github_bus.py`.

```
                        ┌─────────────────────────────────────┐
                        │       current_branch() Invocation   │
                        └──────────────────┬──────────────────┘
                                           │
                                           ▼
                        ┌─────────────────────────────────────┐
                        │ git symbolic-ref --quiet --short    │
                        │                HEAD                 │
                        └──────────────────┬──────────────────┘
                                           │
                           ┌───────────────┴───────────────┐
                           │                               │
                Exit Code != 0                      Exit Code == 0
                (Detached HEAD)                    (Symbolic Ref)
                           │                               │
                           ▼                               ▼
                 ┌──────────────────┐           ┌────────────────────┐
                 │   Return None    │           │ Validate != "HEAD" │
                 └──────────────────┘           └──────────┬─────────┘
                                                           │
                                                           ▼
                                                ┌────────────────────┐
                                                │ Return Branch Name │
                                                └────────────────────┘
```

### Key Logic Changes (`.orchestrator/github_bus.py`)
1. **`current_branch()`**:
   - Replaced `git rev-parse --abbrev-ref HEAD` with `git symbolic-ref --quiet --short HEAD`.
   - On detached HEAD, `symbolic-ref` returns non-zero, safely yielding `None`.
   - Added explicit check: `if not branch or branch == "HEAD": return None`.

2. **`branch_exists(branch)`**:
   - Explicitly rejects `"HEAD"` and any ref ending with `"/HEAD"` (`if not branch or branch == "HEAD" or branch.endswith("/HEAD"): return False`).

3. **`branch_head_sha(branch)` & `remote_branch_exists(branch)`**:
   - Added fail-closed guards against `"HEAD"`.

4. **`branch_has_diff(base, branch)`**:
   - Fails closed (`False`) if `base == "HEAD"` or `branch == "HEAD"`.

5. **`review_branch_for_task(config, status, task)`**:
   - Ensures candidate branch names equal to `"HEAD"` are rejected at all fallback layers.

### Test Coverage (`.orchestrator/test_github_bus.py`)
- **Real Repository Tests**:
  - `test_detached_head_yields_no_branch`: Initializes a real Git repository, detaches HEAD (`git checkout --detach HEAD`), and confirms `current_branch()` returns `None`.
  - `test_named_branch_is_still_returned`: Confirms `current_branch()` returns named branches like `task/ODP-X-001`.
- **Unit & Mock Tests**:
  - `test_current_branch_returns_none_when_detached_head`: Mocks `symbolic-ref` failure to verify `None` response.
  - `test_branch_exists_returns_false_for_head`: Confirms `branch_exists("HEAD")` and `branch_exists("origin/HEAD")` return `False`.
  - `test_review_branch_for_task_rejects_head_branch_name`: Confirms `"HEAD"` is ignored during task branch discovery.

---

## 3. Acceptance Verification Matrix

| Ref ID | Category | Defect Summary | Required Acceptance Rule | Verification Method |
|---|---|---|---|---|
| **A1** | Detached HEAD Resolution | `current_branch()` returned `"HEAD"` on detached checkouts | `current_branch()` must return `None` when HEAD is detached. | `test_detached_head_yields_no_branch` & `test_current_branch_returns_none_when_detached_head` |
| **A2** | Named Branch Resolution | `current_branch()` must not break normal branch resolution | Valid named branches (e.g. `task/ODP-ORCH-001`) are returned accurately. | `test_named_branch_is_still_returned` |
| **A3** | Branch Existence Guard | `branch_exists("HEAD")` resolved `HEAD` as valid branch | `branch_exists("HEAD")` & `branch_exists("origin/HEAD")` must return `False`. | `test_branch_exists_returns_false_for_head` |
| **A4** | Review Branch Selection | `review_branch_for_task` accepted `"HEAD"` as task branch | Candidate branches equal to `"HEAD"` must be skipped. | `test_review_branch_for_task_rejects_head_branch_name` |
| **A5** | Diff & SHA Safety | `branch_has_diff` / `branch_head_sha` operated on `"HEAD"` | Functions return `False` / `None` when given `"HEAD"`. | Source code inspection of `.orchestrator/github_bus.py` |

---

## 4. Verification Suite Commands

To verify the fixes and test suite in this environment:

```bash
# 1. Run GitHub Bus test suite
python3 -m pytest -q .orchestrator/test_github_bus.py

# 2. Run full Orchestrator supervisor & bus test suite
python3 -m pytest -q .orchestrator/test_github_bus.py .orchestrator/test_supervisor.py

# 3. Check python syntax compilation
python3 -m py_compile .orchestrator/github_bus.py .orchestrator/test_github_bus.py

# 4. Run ruff linter
ruff check .orchestrator/

# 5. Check git diff cleanliness
git diff --check origin/dev...HEAD
```

---

## 5. Handoff Note & Reviewer Transition

This sidecar review packet (`support/sidecars/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW.md`) is fully materialized and verified.

- **Assigned Reviewer**: `Codex2`
- **Next Action**: Hand off task `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW` to reviewer `Codex2` for review.
- **Parent Action**: Upon approval by `Codex2`, parent task owner `Antigravity` can incorporate this review packet into parent task `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001` closeout.
