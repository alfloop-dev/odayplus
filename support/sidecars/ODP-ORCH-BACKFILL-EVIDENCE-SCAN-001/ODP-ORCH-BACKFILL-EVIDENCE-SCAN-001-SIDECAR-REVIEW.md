# Sidecar Review Packet: ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW

- **Task ID**: `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001`
- **Helper Kind**: `review_packet`
- **Owner**: `Antigravity`
- **Reviewer**: `Claude3`
- **Status**: `review`
- **Packet Revision**: Round 2 (2026-08-07) — rewritten to accurately review parent deliverable at approved head `b81f4322` on PR #682
- **Target Artifact**: `support/sidecars/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW.md`

### Parent Pin

| Field | Value |
|---|---|
| Parent Approved Head | `b81f43223dd7bf930c4209875cf6b3f87274c64d` |
| Parent Review Gate SHA | `b81f43223dd7bf930c4209875cf6b3f87274c64d` |
| Parent Branch / PR | `task/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001` → PR #682 (`OPEN`, `BLOCKED`, not yet merged to `dev`) |
| Parent Delivering Commits | `66fd4300ebd6ac2dbce11c80631d65641237ead0`, `b81f43223dd7bf930c4209875cf6b3f87274c64d` |
| Deliverable Surface | 2 files modified (+360 insertions, -18 deletions over `dev`) |

> [!NOTE]
> This sidecar task is support-only. It creates only support artifacts (`support/sidecars/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001/*`) without modifying canonical L1 documents, core runtime contracts, or primary orchestrator implementations.

---

## 1. Problem Statement & Root Cause

### Background & Parent Problem Statement
When running backfill operations on git merge evidence for task archive snapshots, the original baseline implementation (`de58b7a4`, PR #608) suffered from false negatives and misattributions.

`git log --grep` searches the entire commit message body and subject. In real repository history, a commit message frequently cites or mentions unrelated prerequisite task IDs in its body or summary. The baseline search tool executed a single-candidate lookup per pattern (`git log --grep="task/<id>" -n 1`), took only the single newest matching candidate, and gave up if that candidate's subject was not a merge delivery. This converted mere citations in newer commits into false negatives for legitimate historical deliveries.

**Concrete False Negative Examples**:
- `ODP-PLAN-AVM-OUTCOME-001` had a valid delivery merge commit at `90bfcf6a` (PR #587), but was reported as unverifiable because a newer commit cited its ID in the commit body.
- `ODP-PLAN-GATE-REGISTRY-001` had a valid delivery merge commit at `a74aceb0` (PR #520), but was hidden behind newer commits mentioning its ID.

Furthermore, an unbounded merge-form matching attempt (`f"task/{task_id}" in subject`) caused misattributions where a commit mentioning another task (e.g. `TASK-E: also mentions TASK-C`) would incorrectly archive `TASK-C` under `TASK-E`'s delivery commit, or prefix collisions like `TASK-C` matching `TASK-C2`.

### Parent Design Decisions & Fix Architecture
1. **Strict Delivery Form Verification (`subject_delivers()`)**:
   - Requires exact matching against true task delivery shapes:
     1. Merge commit where the merge branch tail exactly equals `task/<id>` (`Merge pull request #N from <owner>/task/<id>` or `Merge pull request #N from task/<id>`).
     2. Squash merge subject starting with `<id>` (e.g., `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001: ...`).
     3. ReviewBus squash subject starting with `[ReviewBus] <id>` (e.g., `[ReviewBus] ODP-PLAN-PARALLEL-KICKOFF-20260803 (#608)`).
   - Enforces strict boundary checks (`==` on branch tail) to eliminate prefix collisions (preventing `TASK-C` from matching `TASK-C2`).
   - Explicitly rejects sidecar review subjects naming their parent (e.g., `[ReviewBus] PARENT-SIDECAR-REVIEW` does not deliver `PARENT`).
   - Returns the explicit delivery form string (`merge-commit`, `squash-subject`, `reviewbus-subject`) for snapshot audit logging.

2. **Multi-Candidate Scanning**:
   - Replaces the single-candidate-then-give-up strategy with a multi-candidate scan limit of up to 500 hits.
   - Uses a single fixed-string case-insensitive (`-F -i`) `--grep` prefilter for efficient scanning without regex pattern misinterpretation.
   - Scans through candidate commits in reverse chronological order until a commit satisfying `subject_delivers()` is discovered.

3. **Audit Trail & Backward Compatibility**:
   - Records `delivery_form` in retroactive snapshot metadata (`backfill.retroactive: true`).
   - Carries forward all safety defaults: `--dry-run` by default, `--apply` flag required for disk write, never overwriting existing snapshots.

---

## 2. Parent Implementation Assessment

### Delivered Surface (2 files changed, +360 / -18 over `origin/dev`)

1. **`scripts/orchestrator/backfill_task_archive_snapshots.py`** (+155 insertions, -18 deletions, 368 total lines)
   - Implements `subject_delivers(subject, task_id)` to evaluate whether a commit subject represents an authentic delivery for `task_id`.
   - Updates `find_merge_evidence(repo, task_id, ref)` to scan up to 500 candidate commits using `-F -i` prefiltering.
   - Parses merge pull requests with or without owner prefixes (supporting format `#227`: `Merge pull request #227 from task/<id>`).
   - Evaluates merge commit vs. squash merge preferences and records `delivery_form` inside retro snapshot payload.

2. **`scripts/orchestrator/test_backfill_task_archive_snapshots.py`** (+223 insertions, 412 total lines, 28 total tests)
   - Expands unit test coverage from 11 baseline tests to 28 tests (adding 17 targeted regression and boundary tests).
   - Validates `subject_delivers()` rules across merge branches, squash subjects, `[ReviewBus]` formats, owner-less PR merges, prefix collisions, sidecar parent naming, and non-task branch merges.
   - Tests multi-candidate scanning past body-only mentions, squash fallback behind mentions, and sidecar merge isolation.

---

## 3. Acceptance Verification Matrix

The 17 new tests added by parent commit `b81f4322` cover all acceptance criteria and edge cases:

| Ref | Target Module | Description / Rule | Verification Test |
|---|---|---|---|
| **A1** | `subject_delivers` | Accepts merge commit whose branch tail equals `task/<id>` | `test_subject_delivers_accepts_own_task_branch_merge` |
| **A2** | `subject_delivers` | Accepts squash merge subject starting with task ID (`<id>: ...`) | `test_subject_delivers_accepts_squash_subject` |
| **A3** | `subject_delivers` | Rejects task ID prefix collision (`TASK-C` vs `TASK-C2`) | `test_subject_delivers_rejects_task_id_prefix_collision` |
| **A4** | `subject_delivers` | Rejects merge commit of another task branch even if ID is in title/body | `test_subject_delivers_rejects_merge_of_another_task_branch` |
| **A5** | `subject_delivers` | Rejects commit subject that merely mentions task ID without delivery format | `test_subject_delivers_rejects_mere_mention_in_subject` |
| **A6** | `subject_delivers` | Accepts ReviewBus squash format (`[ReviewBus] <id>: ...`) | `test_subject_delivers_accepts_reviewbus_subject` |
| **A7** | `subject_delivers` | Rejects sidecar reviewbus subject naming parent task (`...PARENT-SIDECAR-REVIEW...`) | `test_subject_delivers_rejects_reviewbus_sidecar_naming_its_parent` |
| **A8** | `subject_delivers` | Accepts merge PR format without owner prefix (`Merge pull request #N from task/<id>`) | `test_subject_delivers_accepts_merge_without_owner_prefix` |
| **A9** | `subject_delivers` | Rejects merge commit of non-task branch (e.g. `feat/foo`) | `test_subject_delivers_rejects_non_task_branch_merge` |
| **A10** | `find_merge_evidence` | Scans past body-only mentions to find true merge delivery commit | `test_find_merge_evidence_scans_past_body_only_mentions` |
| **A11** | `find_merge_evidence` | Ignores sidecar merge commit when searching for parent task delivery | `test_find_merge_evidence_ignores_sidecar_merge_for_parent_task` |
| **A12** | `find_merge_evidence` | Prefers merge commit over newer squash commit | `test_find_merge_evidence_prefers_merge_over_newer_squash` |
| **A13** | `find_merge_evidence` | Falls back to squash merge when no merge commit exists | `test_find_merge_evidence_falls_back_to_squash_when_no_merge` |
| **A14** | `plan` | Planning skips tasks with only body mentions (returns no evidence) | `test_plan_skips_task_with_only_body_mentions` |
| **A15** | multi-candidate | Multi-candidate scan ensures newer unrelated commit citing task ID does not hide real merge | `test_newer_unrelated_commit_does_not_hide_real_merge` |
| **A16** | multi-candidate | Multi-candidate scan finds squash merge behind newer citation commits | `test_squash_merge_found_behind_newer_mentions` |
| **A17** | multi-candidate | Multi-candidate scan over body mentions yields nothing (unverified) | `test_only_body_mentions_still_yield_nothing` |
| **A18** | build & lint | Code compiles cleanly (`py_compile`) and passes `ruff check` | `python3 -m py_compile` & `ruff check` |

---

## 4. Verification Suite Commands & Results

Parent verification commands executed against `origin/task/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001` at approved head `b81f4322`:

```bash
# 1. Run unit test suite for backfill module (28 tests)
/home/lupin/oday-plus/.venv/bin/pytest -v scripts/orchestrator/test_backfill_task_archive_snapshots.py

# 2. Run full orchestrator test suite
pytest -v scripts/orchestrator/

# 3. Python compilation check
python3 -m py_compile \
  scripts/orchestrator/backfill_task_archive_snapshots.py \
  scripts/orchestrator/test_backfill_task_archive_snapshots.py

# 4. Ruff lint check
ruff check \
  scripts/orchestrator/backfill_task_archive_snapshots.py \
  scripts/orchestrator/test_backfill_task_archive_snapshots.py
```

### Recorded Verification Results

| Verification Check | Result | Details |
|---|---|---|
| Backfill Test Suite (28 tests) | **PASS** | 28 passed in 0.52s |
| Orchestrator Test Suite | **PASS** | **73 passed** (verified by reviewer `Antigravity2`) |
| `py_compile` | **PASS** | Clean compilation (exit code 0) |
| `ruff check` | **PASS** | All checks passed cleanly |
| Live Corpus Audit (307 task IDs) | **PASS** | 307/307 correct: 0 false negatives, 0 misattributions (11 historical misattributions fixed) |
| Live Dependency Graph Effect | **PASS** | Graph failures reduced from **33 to 2** (the 2 remaining are genuinely undelivered `ODP-PLAN-OSS-LICENSE-GATE-001`) |

---

## 5. Handoff Note, Recommendation & Residual Risk

### Recommendations for Parent Task Closeout
1. **Absorb Recommendation**: Strongly recommend that parent owner (`Claude3`) absorb this sidecar review packet and finalize PR #682.
2. **Merge Sequence**: Once PR #682 merges into `dev`, parent task `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001` should be transitioned to `done` via `scripts/ai-status.sh done`.

### Residual Risk & Maintenance Note
- **Idempotent Preservation**: The backfill tool intentionally never overwrites existing task archive snapshots.
- **Archive Cleanup**: Historical snapshots created before this fix (e.g. `ODP-PLAN-SITESCORE-OUTCOME-001` archived under sidecar PR #633 instead of squash #525) will remain as originally written unless explicitly remediated via a dedicated archive cleanup task or manual overwrite.

### Handoff Details
- **Sidecar Owner**: `Antigravity`
- **Assigned Reviewer**: `Claude3`
- **Sidecar Artifact**: `support/sidecars/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW.md`
- **State Handoff**: Handoff task `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW` to `review` state assigned to `Claude3`.

