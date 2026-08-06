# Sidecar Acceptance Packet & Dependency Map

**Parent Task ID**: `ODP-ORCH-DIRTY-WORKTREE-LEASE-RECOVERY-001`
**Sidecar Task ID**: `ODP-ORCH-DIRTY-WORKTREE-LEASE-RECOVERY-001-SIDECAR-ACCEPTANCE`
**Helper Kind**: `acceptance_packet`
**Owner**: Antigravity4
**Reviewer**: Antigravity
**Created At**: 2026-08-02
**Status**: Support Packet Materialized

---

## 1. Overview & Purpose

This document serves as the **Sidecar Acceptance Packet and Dependency Map** for parent task `ODP-ORCH-DIRTY-WORKTREE-LEASE-RECOVERY-001` ("Recover blocked Supervisor worktree leases without data loss").

As a sidecar support slice, this packet:
- Outlines the complete dependency structure between the Supervisor dispatch layer, Git worktree isolation, and context materialization.
- Formulates a rigorous **Acceptance Verification Matrix** incorporating all defect resolutions (**B1 through B7**).
- Does **not** modify canonical architecture truth, core contracts, or primary Supervisor runtime implementations.

---

## 2. Dependency Map

```
                  ┌──────────────────────────────────────────────┐
                  │    Supervisor Worktree Management Core       │
                  │       (.orchestrator/supervisor.py)          │
                  └──────────────────────┬───────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 │                                               │
                 ▼                                               ▼
┌──────────────────────────────────┐           ┌──────────────────────────────────┐
│   Worktree Dirt Classification   │           │      Context Materialization     │
│    (_classify_worktree_dirt)     │           │ (materialize_worker_context_files│
└────────────────┬─────────────────┘           └────────────────┬─────────────────┘
                 │                                              │
                 ▼                                              ▼
┌──────────────────────────────────┐           ┌──────────────────────────────────┐
│  Byte-Preserving Quarantine      │           │ Path & Inode Safety Verification │
│ (fresh worktree lease recovery)  │           │  (_is_safe_context_destination)  │
└──────────────────────────────────┘           └──────────────────────────────────┘
                 │                                              │
                 └───────────────────────┬──────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │          Worker Dispatch Integrity           │
                  │   (e.g., ODP-CI-DEV-MERGE-RELEASE-NOGO)      │
                  └──────────────────────────────────────────────┘
```

### Upstream Components & Interfaces
1. **`prepare_worker_workspace`**: Primary entrypoint for allocating/recovering worker worktrees from exact immutable task refs.
2. **`_classify_worktree_dirt`**: Inspects status codes to distinguish untracked scratch vs. tracked/staged modifications (`real` dirt).
3. **`materialize_worker_context_files`**: Copies context files (task brief, `ai-status.json`, etc.) into worker worktrees.
4. **`_is_safe_context_destination`**: Protects against writing into symlinks, non-regular files, or hard-linked inodes.

### Downstream Affected Tasks & Workflows
- **`ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001`**: Retains worktree evidence when leased worktrees enter quarantine.
- **Fleet Dispatch Loop**: Prevents supervisor dispatch deadlocks caused by dirty worktree leases.

---

## 3. Acceptance Verification Matrix (B1 – B7 Traceability)

| Ref ID | Category | Defect Summary | Required Acceptance Rule | Verification Requirement |
|---|---|---|---|---|
| **B1** | Worktree Allocation | Fresh worktree fast-forward/merge mutation on task SHA | `prepare_worker_workspace` must validate and retain exact `task_sha` without base fast-forward/merge mutation (`exact_task_sha_preserved=True`). | Test fresh recovery retains exact SHA without mutation. |
| **B2** | Git Ignore Resolution | Context file status check misinterprets git exclude rules | `materialize_worker_context_files` queries Git's actual exclude path via `rev-parse --git-path info/exclude`, confirming `check-ignore` returns 0 and `git status` stays clean. | Test git exclude path resolution. |
| **B3** | Dirt Classification | Staged/tracked modifications under context paths misclassified | `_classify_worktree_dirt` strictly requires status codes in `('??', '!!')` for `scratch_only`. Tracked/staged edits return `real` dirt, failing reuse and entering quarantine. | Test byte-identity preservation in quarantine. |
| **B4** | Context Protection | Context materialization overwrites Git-tracked files | `materialize_worker_context_files` checks `git ls-files --error-unmatch` before writing, refusing to overwrite Git-tracked destinations. | Test tracked baseline files remain byte-clean. |
| **B5** | Path Safety | Untracked context paths with symlinks/non-regular files bypass safety checks | `_is_safe_context_destination` fails closed on symlinks/non-regular files and unsafe parents; untracked context paths failing safety checks classify as `real` dirt. | Test symlinks and unsafe destinations trigger clean recovery. |
| **B6** | Remote Ref Freeze | `prepare_worker_workspace` skips remote SHA validation when dirty | Freeze authoritative remote task SHA (`origin/<task>`) before dirty recovery; fail closed on ref mismatch or unavailable remote. | Test bare-remote reproduction with local unpushed commits. |
| **B7** | Inode Safety | Hard-linked context destinations allow writing through into tracked files | Non-following atomic create/replace protocol prevents writing through existing untrusted inodes (hard links). | Test hard-linked `ai-status.json` to tracked `README.md` fails safe. |

---

## 4. Comprehensive Acceptance Checklist

- [ ] **1. Data Preservation & Quarantine Integrity**
  - [ ] Original dirty worktree bytes remain 100% unchanged in quarantine.
  - [ ] No `git reset`, `git clean`, `git stash`, `git add`, `git commit`, or file deletion is performed on unknown owner content.

- [ ] **2. Immutable Ref Resolution & Allocation**
  - [ ] Authoritative remote task SHA (`origin/<task>`) is resolved and frozen before dirty recovery.
  - [ ] System fails closed on ref mismatch or unavailable remote task refs.
  - [ ] Recovery allocates a distinct fresh clean worktree from the exact immutable task ref.

- [ ] **3. Context Materialization & Inode Safety**
  - [ ] `materialize_worker_context_files` refuses to overwrite Git-tracked destinations.
  - [ ] `_is_safe_context_destination` fails closed on symlinks, non-regular files, and hard-linked inodes.
  - [ ] Materialized context remains ephemeral or ignored without creating permanent dispatch blockers.

- [ ] **4. Evidence & Downstream Preservation**
  - [ ] Concurrent tasks (e.g. `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001`) retain historical worktree evidence safely.

- [ ] **5. Test Suite & Code Quality Verification**
  - [ ] All focused orchestrator unit tests pass (`pytest .orchestrator/test_supervisor.py`).
  - [ ] Python syntax compile check clean (`python3 -m py_compile .orchestrator/supervisor.py .orchestrator/test_supervisor.py`).
  - [ ] Code style and lint checks clean (`ruff check .orchestrator/`).
  - [ ] `git diff --check` exits with code 0.

---

## 5. Standard Verification Suite Commands

```bash
# 1. Run focused supervisor unit tests
pytest -q .orchestrator/test_supervisor.py

# 2. Check python syntax compilation
python3 -m py_compile .orchestrator/supervisor.py .orchestrator/test_supervisor.py

# 3. Run ruff linter
ruff check .orchestrator/

# 4. Verify whitespace and diff check
git diff --check origin/dev...HEAD
```

---

## 6. Handoff Note

This sidecar acceptance packet is complete and ready for review by reviewer `Antigravity`. Upon approval, parent owner `Antigravity` can incorporate these acceptance criteria into the parent task `ODP-ORCH-DIRTY-WORKTREE-LEASE-RECOVERY-001` closeout.
