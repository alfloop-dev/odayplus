# Sidecar Review Packet: ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW

- **Task ID**: `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001`
- **Helper Kind**: `review_packet`
- **Owner**: `Antigravity4`
- **Reviewer**: `Claude3`
- **Status**: `review`
- **Packet Revision**: round 1 (2026-08-07) — initial sidecar review packet and evidence summary
- **Target Artifact**: `support/sidecars/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW.md`

### Parent Pin

| Field | Value |
|---|---|
| Parent approved head | `46e64a53` |
| Parent merge-base with `dev` | `46e64a53` |
| Landed on `dev` as | `de58b7a4` — *[ReviewBus] ODP-PLAN-PARALLEL-KICKOFF-20260803 (#608)* |
| Deliverable surface | 2 files, 438 lines (see §2) |

> [!NOTE]
> This sidecar task is support-only. It creates only support artifacts (`support/sidecars/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001/*`) without modifying canonical L1 documents, core runtime contracts, or primary orchestrator implementations.

---

## 1. Problem Statement & Root Cause

### Background
During Control Pack 3.1 rollout, dependent downstream deployment tasks were blocked because nine prerequisite tasks were merged into `origin/dev` on 2026-07-28 but lacked corresponding task archive snapshot files in `ai-task-archive/tasks/`.

Because `task_archive.py` and `check_task_dependency_resolvability.py` require either live board presence (`ai-status.json`) or an archived snapshot (`ai-task-archive/tasks/<task_id>.json`) to resolve dependencies, those nine tasks failed dependency resolution checks despite having landed on `dev`.

### Design Decisions & Safety Controls
1. **Dynamic Git Re-derivation**: Rather than trusting hardcoded lists or documentation text (which may say "still requires... before merge" even for merged tasks), the backfill script dynamically searches `git log` on target ref (default `origin/dev`) for `Merge pull request ... /task/<id>` or squash merge commits referencing the task ID.
2. **Idempotence & Safety**: Skip tasks whose archive snapshots already exist. Never overwrite existing archive files.
3. **Dry-Run by Default**: Require explicit `--apply` flag to mutate filesystem.
4. **Audit Traceability**: Mark backfilled snapshots with `backfill.retroactive: true`, storing `merge_commit`, `merge_pr`, `merge_subject`, `created_by: "ODP-RUNBOOK-TASK-DEPENDENCY-GRAPH-REPAIR"`, and clear notes indicating retroactive derivation.
5. **Index Separation**: Leave `index.json` untouched on write, allowing `rebuild_archive_index()` to regenerate the index by globbing `tasks/*.json`.

### Candidate Tasks Addressed (9 total)
1. `ODP-AUTH-RUNTIME-RECONCILE-001`
2. `ODP-MODEL-READY-COMPOSE-001`
3. `ODP-LEARNINGHUB-PROD-FIX-001`
4. `ODP-HEATZONE-PIT-LABEL-AUTHORITY-001`
5. `ODP-P10-DEV-LANDING-FIX-001`
6. `ODP-OPERATOR-LIVE-PREFLIGHT-001`
7. `ODP-FORECAST-LEARNINGHUB-TEMPORAL-COMPOSE-001`
8. `ODP-MODEL-CAPABILITY-READINESS-001`
9. `ODP-P10-R3CD-DEV-COMPOSE-001`

---

## 2. Parent Implementation Assessment

### Delivered Modules (2 files, 438 lines)
1. **`scripts/orchestrator/backfill_task_archive_snapshots.py`** (249 lines)
   - CLI tool to dynamically discover git merge evidence and generate retroactive archive snapshots.
   - Flags: `--archive-dir` (required), `--repo` (default `.`), `--ref` (default `origin/dev`), `--task` (repeatable filter), `--dry-run` (default), `--apply`.
   - Core functions:
     - `find_merge_evidence(repo, task_id, ref)`: Searches git log for merge PR or squash commit.
     - `find_repo_artifacts(repo, task_id, limit=4)`: Discovers evidence files under `docs/evidence/`.
     - `build_snapshot(task_id, evidence, artifacts)`: Constructs standard snapshot dictionary with retroactive backfill metadata.
     - `plan(repo, archive_dir, task_ids, ref)`: Computes planned writes, skipped existing, and skipped unverified tasks.
     - `main(argv)`: CLI entry point handling arguments, validation, reporting, and writing.

2. **`scripts/orchestrator/test_backfill_task_archive_snapshots.py`** (189 lines, 11 tests)
   - Comprehensive test suite covering git log parsing, snapshot structure, retroactive metadata, planning logic, dry run safety, apply execution, index preservation, and missing directory handling.

---

## 3. Acceptance Verification Matrix

| Ref | Module | Summary | Verification Method |
|---|---|---|---|
| **A1** | backfill | Prefers merge commit over squash merge when discovering evidence. | `test_find_merge_evidence_prefers_merge_commit` |
| **A2** | backfill | Accepts squash merge containing task ID when merge PR commit is absent. | `test_find_merge_evidence_accepts_squash_merge` |
| **A3** | backfill | Returns `None` when no git merge evidence exists for a candidate task. | `test_find_merge_evidence_returns_none_when_absent` |
| **A4** | backfill | Snapshot payload structure satisfies `task_archive.task_satisfies_dependency` requirements (`status: "done"`, `terminal_outcome: "completed"`). | `test_snapshot_shape_satisfies_dependency_rule` |
| **A5** | backfill | Snapshot is explicitly marked with `backfill.retroactive: true` and audit trailers. | `test_snapshot_is_labelled_retroactive` |
| **A6** | backfill | Skipping unverified tasks without git merge evidence. | `test_plan_skips_unverified_tasks` |
| **A7** | backfill | Idempotent execution: existing archive snapshot files are never overwritten. | `test_plan_never_overwrites_existing_snapshot` |
| **A8** | backfill | Safety default: `--dry-run` writes nothing to disk. | `test_dry_run_writes_nothing` |
| **A9** | backfill | `--apply` writes valid, JSON-parseable snapshot file for verified tasks. | `test_apply_writes_resolvable_snapshot` |
| **A10** | backfill | Does not touch `index.json` when applying backfill snapshots. | `test_apply_does_not_touch_index_json` |
| **A11** | backfill | Fails closed with error exit code 1 if `--archive-dir` does not exist. | `test_missing_archive_dir_fails_closed` |
| **A12** | both | Code compiles cleanly (`py_compile`) and passes `ruff check`. | `python3 -m py_compile` and `ruff check` |

---

## 4. Verification Suite Commands & Results

Run from repo root with python 3.12 environment (`/home/lupin/oday-plus/.venv/bin/pytest`):

```bash
# 1. Run backfill snapshot unit tests (11 tests)
/home/lupin/oday-plus/.venv/bin/pytest -v scripts/orchestrator/test_backfill_task_archive_snapshots.py

# 2. Syntax compilation
python3 -m py_compile \
  scripts/orchestrator/backfill_task_archive_snapshots.py \
  scripts/orchestrator/test_backfill_task_archive_snapshots.py

# 3. Lint check
/home/lupin/.local/bin/ruff check \
  scripts/orchestrator/backfill_task_archive_snapshots.py \
  scripts/orchestrator/test_backfill_task_archive_snapshots.py

# 4. Live dry-run verification against canonical archive root
python3 scripts/orchestrator/backfill_task_archive_snapshots.py \
  --archive-dir /home/lupin/oday-plus-supervisor-live/ai-task-archive/tasks \
  --repo /home/lupin/oday-plus-supervisor-live \
  --ref HEAD --dry-run
```

### Recorded Results (2026-08-07)

| Command | Result |
|---|---|
| pytest suite (1) | **11 passed in 0.56s** |
| `py_compile` (2) | Clean (exit 0) |
| `ruff check` (3) | **All checks passed!** |
| Live dry-run (4) | **Clean**: 9 tasks checked, 9 already present in canonical archive, 0 written. |

---

## 5. Handoff Note & Reviewer Transition

This review packet is complete and ready for handoff to `Claude3`.

- **Owner**: `Antigravity4`
- **Assigned Reviewer**: `Claude3`
- **Deliverables**: Support sidecar review packet `support/sidecars/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW.md`.
- **Parent Task Scope**: `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001` parent implementation delivered `backfill_task_archive_snapshots.py` and `test_backfill_task_archive_snapshots.py`.
- **Status Transition**: Handoff task `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW` to `review` state assigned to `Claude3`.
