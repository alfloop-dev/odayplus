# Source-Document Materialization Dev & Live Validation Report

- **Task ID**: `ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001`
- **Phase**: Orchestrator Control Plane
- **Owner**: `Antigravity`
- **Reviewer**: `Codex5`
- **Base Branch**: `origin/dev` (HEAD `5a1aee5b`)
- **Task Branch**: `task/ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001`
- **PR**: [#606](https://github.com/alfloop-dev/odayplus/pull/606) (Base `dev`)

---

## 1. Executive Summary

Task `ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001` ports and validates first-class source document materialization and brief rendering across the `origin/dev` tip and live Supervisor control plane.

This verification proves:
1. **Control-Plane Atomic Deployment**: All core control-plane bytes (`.orchestrator/common.py`, `.orchestrator/supervisor.py`, `.orchestrator/github_bus.py`, `scripts/ai_status.py`, `scripts/ai-status.sh`) were published atomically to the live Supervisor root (`/home/lupin/oday-plus-supervisor-live`) via same-directory temporary siblings, byte verification, and `os.replace` renames.
2. **Package 10 Probe Materialization**: Owner (`Antigravity`) and Reviewer (`Codex5`) worker dispatches for Package 10 source documents (`PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md`, `PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md`, `manifest.json`) receive **identical 64-hex SHA manifests** (`materialized_source_manifest`) and can read all files.
3. **Fail-Closed Security Matrix (B1-B17)**: Missing, stale, ambiguous, external, symlinked, modified, or hash-mismatched Package 10 source docs fail closed before worker entry.

---

## 2. Live Supervisor Deployment Receipts & Rollback Commands

Control plane bytes were published atomically to `/home/lupin/oday-plus-supervisor-live`:

| File | SHA256 (Before / After) | Inode (Before / After) | Mode | Rollback Command | Status |
|---|---|---|---|---|---|
| `.orchestrator/common.py` | `ad818a12...` / `ad818a12...` | `897557` / `897539` | `664` | `install -m 664 /home/lupin/oday-plus-supervisor-live/.orchestrator/backups/ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001/common.py.bak /home/lupin/oday-plus-supervisor-live/.orchestrator/common.py` | `published_atomically` |
| `.orchestrator/supervisor.py` | `b5097527...` / `b5097527...` | `897558` / `897557` | `664` | `install -m 664 /home/lupin/oday-plus-supervisor-live/.orchestrator/backups/ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001/supervisor.py.bak /home/lupin/oday-plus-supervisor-live/.orchestrator/supervisor.py` | `published_atomically` |
| `.orchestrator/github_bus.py` | `86047ea8...` / `86047ea8...` | `897559` / `897558` | `664` | `install -m 664 /home/lupin/oday-plus-supervisor-live/.orchestrator/backups/ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001/github_bus.py.bak /home/lupin/oday-plus-supervisor-live/.orchestrator/github_bus.py` | `published_atomically` |
| `scripts/ai_status.py` | `5283abf7...` / `5283abf7...` | `897560` / `897559` | `775` | `install -m 775 /home/lupin/oday-plus-supervisor-live/.orchestrator/backups/ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001/ai_status.py.bak /home/lupin/oday-plus-supervisor-live/scripts/ai_status.py` | `published_atomically` |
| `scripts/ai-status.sh` | `7cfb4dc9...` / `7cfb4dc9...` | `897561` / `897560` | `775` | `install -m 775 /home/lupin/oday-plus-supervisor-live/.orchestrator/backups/ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001/ai-status.sh.bak /home/lupin/oday-plus-supervisor-live/scripts/ai-status.sh` | `published_atomically` |

---

## 3. Package 10 Probe Task Materialization Receipts

Probe task `source_docs`:
1. `docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md`
2. `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md`
3. `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/manifest.json`

### `materialized_source_manifest` Comparison (Owner vs Reviewer)

- **Manifest Equality**: `True` (`manifest_owner == manifest_reviewer`)
- **Owner Files Readable**: `True`
- **Reviewer Files Readable**: `True`
- **Canonical SHA Match**: `True`

```json
[
  {
    "relative_path": "docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md",
    "sha256": "ed0e4e29fdbb0197fdcd1d932057a93072921ad7de8180a5dbb0f30272e6c93b"
  },
  {
    "relative_path": "docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md",
    "sha256": "24efa4645b5ee6231b220b7027ce90bbae4d577ecd6ac818f295d7e3251b14d7"
  },
  {
    "relative_path": "docs_archive/00_source_zips/operator_console/r7-20260720-package-10/manifest.json",
    "sha256": "2e84b7d29cd897845eb2e2d0f30ac62e6e60b4afd0a1b64ad5a68e4445358ec1"
  }
]
```

---

## 4. Security Invariants & Fail-Closed Matrix (B1 - B17)

- **B1 Context Fallback**: `config_path` defaults `status_file` to `ROOT / ai-status.json`.
- **B2 Freshness Check**: `is_task_brief_stale` validates `## Source Documents` section & `SHA256:` header.
- **B3 Hash Computation**: Computes exact 64-hex SHA256 digests via `hashlib`.
- **B4 Legacy Brief Stale Handling**: Briefs lacking SHA256 header or source section mark as stale.
- **B5 Tracked File No-Clobber**: Pre-existing tracked worktree files are preserved without overwrite.
- **B6 Destination Path Traversal**: `validate_destination_context_path` resolves and rejects paths outside workspace root.
- **B7 Hash Mismatch Fail-Closed**: Mismatched tracked files fail closed for mutating/P0 tasks.
- **B8 Immutable Source Manifest**: `materialized_source_manifest` attached to request metadata with exact 64-hex digests.
- **B9 Read/Copy Failures**: `shutil.copy2` errors fail closed before worker entry.
- **B10 External Path/Symlink Validation**: Rejects leading `/` and validates directory child symlinks.
- **B11 Archive Ambiguity Check**: Binds 13 canonical task fields and checks archived task snapshots for conflicts.
- **B12 Directory Inventory Equality**: Tree hash comparison ensures owner and reviewer worktree trees match identically.
- **B13 Centralized Ambiguity Enforcement**: Validates archive ambiguity before task brief materialization/cache reuse.
- **B14 Mandatory 64-Hex SHA**: Rejects null or invalid digests for mutating/P0 tasks.
- **B15 Candidate Discovery Preservation**: Shadowing of candidate discovery helpers prevented.
- **B16 Symlink Component Rejection**: Checks all path components for `islink` to prevent internal/external symlink escape.
- **B17 Formatting Integrity**: `git diff --check` and `ruff check` clean.

---

## 5. Test Suite Verification

- **`test_task_brief_source_docs.py`**: 30/30 passed
- **`test_common.py`**: 19/19 passed
- **`test_dispatch_policy.py`**: 25/25 passed
- **`test_ai_status.py`**: 119/119 passed
- **`test_supervisor.py`**: 345 passed, 127 subtests passed
- **`ruff check`**: Clean (0 errors)
- **`git diff --check`**: Clean
