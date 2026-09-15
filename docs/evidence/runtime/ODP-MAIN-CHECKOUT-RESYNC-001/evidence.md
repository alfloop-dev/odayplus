# Evidence: ODP-MAIN-CHECKOUT-RESYNC-001

Generated: 2026-09-15T06:23:00Z
Task: ODP-MAIN-CHECKOUT-RESYNC-001 — 修復 config 的 checkout 相對解析並讓主 checkout 可安全同步

## 1. Root Cause Analysis

### Problem
The main checkout (`/home/lupin/odayplus`) is 1853 commits behind `origin/dev`.
It cannot be safely `git pull`ed because:

1. `scripts/ai-status.sh` contained two hardcoded absolute-path exports:
   ```bash
   export ORCH_CONFIG_PATH=/home/lupin/odayplus/.orchestrator/config.json
   export PANTHEON_CONFIG_PATH=/home/lupin/odayplus/.orchestrator/config.json
   ```
   Removing them causes ConfigError in all subcommands.

2. `scripts/ai_status.py` has 3 uncommitted local diffs that must be verified
   as semantically covered by `origin/dev` before they can be discarded.

3. Nine runtime state files are uncommitted; pulling would overwrite them with
   stale committed versions.

### Root Cause
`ai_status.py` resolves `ROOT = Path(__file__).resolve().parents[1]`, which
follows the physical file location. When exec'd from a separate runtime checkout
(via the hardcoded path `/home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py`),
`ROOT` points to that checkout — not the main checkout. Then
`CONFIG_FILE = ROOT / ".orchestrator" / "config.json"` looks for config in the
wrong place. The hardcoded exports were a bandaid.

### Fix Applied
In `scripts/ai-status.sh`:

| Before (origin/dev HEAD) | After (this PR) |
|---|---|
| `exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"` | `export ORCH_CONFIG_PATH="${ORCH_CONFIG_PATH:-${PANTHEON_STATUS_ROOT}/.orchestrator/config.json}"` |
| (hardcoded runtime checkout path) | `exec python3 "$status_root/scripts/ai_status.py" "$@"` |

Changes:
- **Config resolution**: `ORCH_CONFIG_PATH` is derived from `PANTHEON_STATUS_ROOT`
  when not already set by a Supervisor. No hardcoded absolute paths.
- **Python exec**: Uses `$status_root/scripts/ai_status.py` (script-local),
  not a hardcoded runtime checkout path. After the main checkout syncs, it
  will exec the up-to-date local copy.

## 2. Acceptance Test: Read-Only Subcommands with Cleared Config Env Vars

Environment: `ORCH_CONFIG_PATH` and `PANTHEON_CONFIG_PATH` both unset.
`PANTHEON_STATUS_ROOT=/home/lupin/odayplus` (normal fleet value).

### `show` subcommand
```
$ env -u ORCH_CONFIG_PATH -u PANTHEON_CONFIG_PATH \
    PANTHEON_STATUS_ROOT=/home/lupin/odayplus \
    AI_NAME=Antigravity7 ./scripts/ai-status.sh show ODP-MAIN-CHECKOUT-RESYNC-001
```
**Result**: Success. Returned task JSON with status `in_progress`.
**Exit code**: 0

### `prompt` subcommand
```
$ env -u ORCH_CONFIG_PATH -u PANTHEON_CONFIG_PATH \
    PANTHEON_STATUS_ROOT=/home/lupin/odayplus \
    AI_NAME=Antigravity7 ./scripts/ai-status.sh prompt ODP-MAIN-CHECKOUT-RESYNC-001
```
**Result**: Success. Returned prompt text.
**Exit code**: 0

**Verdict**: No ConfigError. Acceptance criterion met.

## 3. ai_status.py Local Diff Comparison (3 items)

All three local uncommitted changes in `/home/lupin/odayplus/scripts/ai_status.py`
have been compared against `origin/dev` (`fc4f7529`). Each is semantically
covered and can be safely discarded during sync.

### 3a. enforce_delivery_merged_gate — repository field

| Aspect | Local diff | origin/dev |
|---|---|---|
| Change | Adds `"repository": repository_slug_value` to dict passed to `is_approved_head_satisfied` | Full task dict is constructed with `id`, `branch`, `approved_head`, AND `repository` fields, all populated from local values when missing |
| Semantic | Partial fix — only adds repository | Complete fix — builds full task context dict |
| Covered? | **Yes** — origin/dev is strictly more comprehensive |

### 3b. collect_done_delivery_metadata — Task-ID casefold

| Aspect | Local diff | origin/dev |
|---|---|---|
| Change | `if field_name == "Task-ID" and actual_value.casefold() == expected_value.casefold(): continue` | Identical line present at line 3620 |
| Semantic | Case-insensitive Task-ID comparison | Same |
| Covered? | **Yes** — verbatim identical |

### 3c. resolve_task_sha — branch selection logic

| Aspect | Local diff | origin/dev |
|---|---|---|
| Change | Uses `recorded_branch` exclusively when available; conventional names only as fallback | Uses `task_explicit_branch(task)` — returns only the explicitly-set branch, not a synthesized fallback |
| Name difference | `recorded_branch` (from `task_branch_name`) | `recorded_branch` (from `task_explicit_branch`) |
| Semantic | `task_branch_name` always falls back to `f"task/{task_id}"`, so `recorded_branch` was always truthy — the "exclusive" logic is partly broken | `task_explicit_branch` returns `None` when no branch was explicitly set, so the `if/else` correctly falls back to conventional names |
| Covered? | **Yes** — origin/dev fix is strictly correct; local fix has a subtle defect |

**Verdict**: All three diffs are safely discardable. origin/dev versions are equal or superior.

## 4. Runtime State Files — Sync Handling

The main checkout has 9 modified-but-uncommitted runtime files that must NOT
be overwritten during sync:

| # | File | Type |
|---|---|---|
| 1 | `ai-status.json` | Live fleet state |
| 2 | `ai-activity-log.jsonl` | Activity log |
| 3 | `current-work.md` | Derived narrative |
| 4 | `dashboard-bundle.json` | Dashboard data |
| 5 | `docs-site/ai-status.json` | Mirror |
| 6 | `docs-site/ai-activity-log.jsonl` | Mirror |
| 7 | `docs-site/current-work.md` | Mirror |
| 8 | `docs-site/dashboard-bundle.json` | Mirror |
| 9 | `docs-site/orchestrator-state.json` | Mirror |

### Handling strategy for main checkout sync

These files are gitignored or their committed versions are intentionally stale.
The sync procedure for the main checkout (performed AFTER this PR merges) is:

1. **Pre-sync snapshot**: `cp ai-status.json ai-status.json.pre-sync-backup`
   (and same for all 9 files)
2. **Pull with rebase**: `git pull --rebase origin dev` — this updates tracked
   files. The runtime state files, being modified but not staged, will cause
   merge conflicts only if the committed version changed.
3. **Resolve any conflicts**: For these 9 files, always keep the working-tree
   version (the live state), never the incoming committed version.
4. **Verify post-sync**: Compare pre-sync backup against current working copy
   to confirm no content was lost.

**This PR does NOT modify any of these 9 files.** It only changes
`scripts/ai-status.sh`. The sync procedure above is a guide for the operator
performing the main checkout update after this PR merges.

## 5. Scope Boundary

This task does NOT:
- Commit or discard `.orchestrator/` untracked files (those belong to a
  separate control-plane task)
- Modify live config content
- Directly modify the main checkout (changes go through PR → dev → pull)
