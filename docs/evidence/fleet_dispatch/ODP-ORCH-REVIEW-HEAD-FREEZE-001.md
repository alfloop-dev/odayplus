# ODP-ORCH-REVIEW-HEAD-FREEZE-001: Freeze exact review head and stop finalize dispatch churn

Owner: Antigravity4 · Reviewer: Claude · Phase: Orchestrator Control Plane

Depends on ODP-ORCH-APPROVAL-RESUME-ROOT-001 (done).

This task changes the control plane review and finalize dispatch behavior. It touches no Package 10 UI, no design API, no worker business logic, and no cloud resources.

## 1. Summary of Control Plane Defects Addressed & Rework Fixes

1. **Post-Review Mutation Bypassing Reviewer & On-Disk Persistence (B1 Fix)**:
   - When a task in `review_approved` undergoes branch mutation (HEAD mismatch), `supervisor.py` now persists `status: "review"` directly to `ai-status.json` on disk via `write_json` and `sync_status_pipeline(config)`, popping `approved_head`.
   - Prevents in-memory-only status mutations that previously caused infinite activity logging loops and task parking.

2. **Silent Failure & Path Resolution (B2 Fix)**:
   - Added `SCRIPTS_DIR` to `sys.path` at module top in `.orchestrator/supervisor.py` so `from ai_status import resolve_task_sha, task_pr_ci_status` succeeds reliably without raising `ModuleNotFoundError` or failing open.

3. **Dead Code Cleanup & Priority Hook (B3 Fix)**:
   - Wired `dispatch_priority_for_task` into `agent_has_dispatchable_primary_work` and `dispatch_ready_tasks`, making `dispatch_priority_for_task` the single source of truth for task dispatch priority and eligibility.

4. **CI Status Check Classification (B4 Fix)**:
   - Updated `task_pr_ci_status` in `scripts/ai_status.py` to inspect check `conclusion` first before `state` or `status`. Correctly maps `CheckRun` entries with `status: COMPLETED` and `conclusion: FAILURE` to `"failure"`.

5. **CI-Pending Tracking, Timeout Escape Hatch & Resume (B5 Fix)**:
   - Supervisor tracks pending duration (`ci_pending_since_ts`). If CI is pending for > 30 minutes, writes `ci_pending_timeout` activity log entry for operator visibility.
   - Resumes owner finalize dispatch once CI reaches terminal `success`. Handles `failure` state by suppressing finalize and recording `ci_failed`.

6. **CLI Command Registration & Re-Review Flow (B6 Fix & AC2)**:
   - Registered `re_review` and `re-review` in `MUTATING_COMMANDS` and `target_task_id` in `emit_status_checks_for_changed_tasks`.
   - Updated unit tests in `test_supervisor.py` to drive execution via `ai_status.main(["ai_status.py", "re_review", ...])` and `re-review`. Added `re_review`/`re-review` to `AI_NAME_CASES` in `test_ai_status.py`.

7. **Review Gate Status Check Alignment (B7 Fix & AC4)**:
   - Corrected fallback state mapping in `emit_task_review_status_check`. Non-terminal/non-approved states (`in_progress`, `blocked`) emit `state = "failure"`, preventing unapproved or rejected tasks from passing `task-review-gate`. Only `review_approved` (with matching head) and `done` emit `success`.

8. **SHA Resolution Caching & Test Suite Green (B8 & B9 Fix)**:
   - Removed duplicate `_TASK_SHA_CACHE` declaration in `scripts/ai_status.py` and added `setUp` cache clearing to `StatusCheckEmissionTests` in `scripts/test_ai_status.py`.
   - Restored `resolve_task_sha` priority order (trying `gh pr view` for remote PR head SHA first). Both `.orchestrator` (441 tests) and `scripts/test_ai_status.py` (98 tests) suites pass cleanly.

9. **Handoff Log Message Integrity (B10 Fix)**:
   - Restored target agent in `command_handoff` log message (`f"Handoff to {to_agent}: {message}"`).

10. **Git Diff Check & Whitespace Formatting (B11 Fix)**:
    - Cleaned up EOF blank lines and stray blank line runs across `.orchestrator/test_supervisor.py` and evidence docs. `git diff --check 1e256103` is 100% clean.

11. **Unpushed Branch & Control-Plane Emission Resiliency (B12 Fix)**:
    - Updated `emit_task_review_status_check` error handling to gracefully handle HTTP 422 ("No commit found for SHA"), unauthenticated, and network errors by warning and returning cleanly without raising `RuntimeError`, ensuring local/control-plane state mutations never roll back on status emission failure.

## 2. Verification

```bash
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest .orchestrator -q -m "not requires_live_env"
# 441 passed
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest scripts/test_ai_status.py -q -p no:randomly
# 98 passed
python3 -m py_compile .orchestrator/supervisor.py .orchestrator/test_supervisor.py scripts/ai_status.py
# Clean!
git diff --check 1e256103 HEAD
# Clean!
```

### Deterministic Unit & On-Disk Regression Tests (`ReviewHeadFreezeTests`)
- `test_approve_saves_approved_head_and_rejects_same_owner_reviewer`
- `test_command_done_rejects_mutated_head`
- `test_supervisor_reverts_mutated_approved_head_to_review_on_disk` (asserts against real on-disk status file)
- `test_task_pr_ci_status_handles_checkrun_completed_failure` (verifies B4 CheckRun parsing)
- `test_dispatch_priority_for_task_and_agent_primary_work` (verifies B3 runtime integration)
- `test_explicit_re_review_command` (verifies AC2 CLI transition via main)
- `test_supervisor_suppresses_finalize_dispatch_on_pending_ci` (verifies AC3 / B5)
- `test_task_review_gate_status_check_pending_on_head_mismatch` (verifies AC4)

## 3. Composed Head Merge & Re-Review Verification

```bash
# Merged current origin/dev (ad4a066e) into task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
git merge origin/dev -m "ODP-ORCH-REVIEW-HEAD-FREEZE-001: merge origin/dev"

# Combined CI suite verification:
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest -m "not requires_live_env" .orchestrator scripts
# 545 passed, 10 deselected in 12.37s

/home/lupin/oday-plus-supervisor-live/.venv/bin/ruff check .orchestrator scripts
# All checks passed!
```

## 4. Round 8 (Claude3) — B18 / B19 / B20

Round 7 rejected `d4bca25b`. This round is verified with the method that round 7
established: every fail-closed claim gets a **mutant**, not a read-through. A gate is
only considered covered if deleting it turns the suite red.

### 4.1 CI-faithful sandbox

```bash
mkdir -p /tmp/freeze-r8c && git archive HEAD | tar -x -C /tmp/freeze-r8c
cp .orchestrator/config.example.json .orchestrator/config.json   # what `make bootstrap` does in CI
env -u PANTHEON_STATUS_ROOT -u AI_NAME pytest -m "not requires_live_env" \
    .orchestrator scripts -q --junitxml=/tmp/r8-new2.xml
# exit=0   tests=607 failures=0 errors=0 skipped=0
env -u PANTHEON_STATUS_ROOT ruff check .orchestrator scripts
# All checks passed!
```

Baseline at round-7 head was 603 tests; this round adds 4.

### 4.2 B18 — FIXED (test integrity)

`test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci` now pins
`ai_status.task_pr_ci_status` to `("OPEN", "success")` in both head sub-cases, so the
head gate is the only thing that can produce `None`; adds the `head=match` +
`ci="success"` -> `assertEqual(prio, 1)` positive control; and adds a drifted-head
sub-case. It no longer reaches the real `gh`-shelling CI probe from a unit test (AC5
determinism).

| Mutant | Round 7 | Round 8 |
|---|---|---|
| m6 — head gate fails **open** when head unresolvable | SURVIVED | **KILLED** |
| m12 — head-resolution `except Exception: return None` -> `pass` | SURVIVED | **KILLED** |

Both are killed by exactly
`test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci`.

No behaviour change: `.orchestrator/supervisor.py` was not touched for B18. The shipped
gate was already correct; only its test was vacuous.

### 4.3 B19 — FIXED (delivery sequencing)

`origin/dev` was merged into the task branch as a plain two-parent merge (`8a53a645`,
parents `b9d640e4` + `0b04761a`), preserving reviewer commit `619f30a8`. The round-6
whitespace defect is folded into this same push:

```bash
git diff origin/dev...HEAD --check
# (clean — was: docs/evidence/fleet_dispatch/...md:84: new blank line at EOF)
```

### 4.4 B20 — approved-head immutability, fail-closed approval, operator signal

Three defects, all in the fail-open direction, all now covered by mutants.

**B20-a — approving without a resolvable head silently disabled the freeze.**
`command_approve` recorded the head under `if approved_sha:`. Since `command_done` and
both supervisor dispatch gates are guarded by `if approved_head:`, an unresolvable head
did not fail closed — it opted the task out of the integrity check entirely, defeating
AC1 for that task. The head is now resolved *before* any mutation, and an unresolvable
or raising probe aborts the approval with the task left in `review`.

**B20-b — `approved_head` was silently overwritable.** Every transition back to `review`
(`handoff`, `reopen`, `re_review`, and the supervisor's head-drift demotion) pops
`approved_head`, so a task in `review` still carrying one is inconsistent state.
`command_approve` now refuses to overwrite a *differing* uncleared head and directs the
operator to `re_review`; re-approving at the same head is still allowed.

**B20-c — suppression was silent.** The unresolved-head path and the catch-all
unresolved-CI path both used a bare `continue`, unlike the `pending` and `failure`
branches, so a task sat in `review_approved` with no `next` and no activity-log entry.
Both now emit once (`approved_head_unresolved`, `ci_status_unresolved`) using the same
write-only-if-changed pattern, so they do not re-log every supervisor cycle.

The round-7 non-blocking note about the `if approved_head:` backward-compatibility
bypass is now an explicit comment at the dispatch gate.

### 4.5 Mutation results (all applied to the throwaway sandbox copy)

| # | Mutation | Result |
|---|---|---|
| m13 | `command_approve`: `if not approved_sha: raise` -> `if False:` | **KILLED** by `test_approve_fails_closed_when_approved_head_cannot_be_resolved` |
| m14 | `command_approve`: immutability guard -> `if False:` | **KILLED** by `test_approve_refuses_to_overwrite_uncleared_approved_head` |
| m15 | `command_approve`: resolve `except` -> `approved_sha = None` | **KILLED** by `test_approve_fails_closed_when_approved_head_cannot_be_resolved` |
| m16 | `dispatch_ready_tasks`: `approved_head_unresolved` signal -> `elif False:` | **KILLED** by `test_supervisor_emits_operator_signal_for_silent_finalize_suppression` |
| m17 | `dispatch_ready_tasks`: `ci_status_unresolved` signal -> bare `continue` | **KILLED** by the same test |
| m18 | `dispatch_ready_tasks`: `elif now_ts - start_ts > 1800:` -> `elif False:` | **KILLED** by `test_ci_pending_escalates_to_operator_after_timeout` |
| m10 | `dispatch_ready_tasks`: entire `if ci_status == "pending":` -> `if False:` | **KILLED** (SURVIVED in round 7) |

m10 was round 7's non-blocking observation: the CI-pending branch's side effects
(`ci_pending_since_ts` bookkeeping and the 30-minute escalation notice) had zero
coverage, because the later catch-all still suppressed dispatch. AC3's escalation half
is now covered.

### 4.6 New tests

- `test_approve_fails_closed_when_approved_head_cannot_be_resolved` (B20-a; carries a
  resolvable-head positive control)
- `test_approve_refuses_to_overwrite_uncleared_approved_head` (B20-b; carries a
  same-head re-approval positive control)
- `test_supervisor_emits_operator_signal_for_silent_finalize_suppression` (B20-c;
  carries a head-match/CI-green dispatch positive control, and asserts signals are
  emitted once rather than every cycle)
- `test_ci_pending_escalates_to_operator_after_timeout` (m10 gap / AC3 escalation)

`scripts/test_ai_status.py::test_approve_creates_owner_finalize_handoff` was updated:
it previously relied on the B20-a fail-open and shelled out to `gh` for a task id with
no branch. It now pins `resolve_task_sha` and asserts the frozen head.

### 4.7 Scope

```bash
git diff origin/dev...HEAD --stat
# .orchestrator/supervisor.py | .orchestrator/test_supervisor.py
# scripts/ai_status.py        | scripts/test_ai_status.py
# docs/evidence/**            (fleet_dispatch + runtime review records)
```

No Package 10 UI, API worker logic, or cloud resources were touched (AC6).

### 4.8 Full mutation matrix on the delivered head

All ten mutants applied to a throwaway sandbox copy; originals restored after each run.
Round 7 had three survivors, all now killed.

```
m6    -> KILLED   | test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci
m12   -> KILLED   | test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci
m3    -> KILLED   | test_command_done_fails_closed_when_sha_unresolved_or_raises
m13   -> KILLED   | test_approve_fails_closed_when_approved_head_cannot_be_resolved
m14   -> KILLED   | test_approve_refuses_to_overwrite_uncleared_approved_head
m15   -> KILLED   | test_approve_fails_closed_when_approved_head_cannot_be_resolved
m16   -> KILLED   | test_supervisor_emits_operator_signal_for_silent_finalize_suppression
m17   -> KILLED   | test_supervisor_emits_operator_signal_for_silent_finalize_suppression
m18   -> KILLED   | test_ci_pending_escalates_to_operator_after_timeout
m10   -> KILLED   | test_ci_pending_escalates_to_operator_after_timeout
```

### 4.9 Non-blocking observation — the suite writes to tracked status files

Running the suite from the repository root (rather than a sandbox) appends real entries
to the tracked `ai-activity-log.jsonl` and rewrites `dashboard-bundle.json` /
`docs-site/dashboard-bundle.json`. This is pre-existing: the committed log already
contains `REG-002` / `APP-001-SIDECAR-BFF-HANDOFF` fixture lines from an earlier leaked
run, and the leak comes from `ai_status.append_log` / `sync_all` in tests that do not
patch them.

Scope taken here was to not make it worse:

- the three suppression sub-cases of
  `test_supervisor_suppresses_finalize_dispatch_on_unresolved_head_or_unknown_ci` now
  patch `supervisor.write_activity_log` / `supervisor.write_json`, because the new B20-c
  signals would otherwise have made an existing test start writing to the real log;
- both new approve tests patch `ai_status.append_log`.

The remaining pre-existing leak (`REG-002` family in `scripts/test_ai_status.py`) is left
alone deliberately — fixing it would expand this diff into unrelated tests. It is worth a
follow-up task, ideally an autouse fixture that points the status root at a tmp_path for
the whole suite.
