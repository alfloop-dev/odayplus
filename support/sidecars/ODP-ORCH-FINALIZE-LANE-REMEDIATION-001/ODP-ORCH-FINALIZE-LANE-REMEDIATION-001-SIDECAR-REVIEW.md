# Sidecar Review Packet: ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW

- **Task ID**: `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001`
- **Helper Kind**: `review_packet`
- **Owner**: `Antigravity`
- **Reviewer**: `Claude2`
- **Status**: `review`
- **Created At**: `2026-08-05`
- **Target Artifact**: `support/sidecars/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW.md`

---

## Core Notice & Scope Boundary

> [!NOTE]
> This sidecar task is support-only. It creates only support artifacts (`support/sidecars/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001/*`) without modifying canonical L1 documents, core runtime contracts, or primary orchestrator implementations.

---

## 1. Defect Analysis & Root Cause

### Background & Problem Statement
In the Pantheon Orchestrator architecture, the Supervisor's finalize step acts purely as an observer. When inspecting tasks in `review_approved` (or agents in `finalize` status), if the CI status probe returns failure or non-green status:
- The supervisor writes a log line (e.g., `"resolve failing checks before finalization."` or `"finalize dispatch suppressed."`) and continues.
- There is no automated mechanism in `.orchestrator` to re-trigger CI workflows (`gh run rerun` / `workflow_dispatch`) or automatically rebase / advance task branches onto `dev`.
- Consequently, completed and reviewed tasks could become permanently stranded in `review_approved`.

### Five Root Cause Classifications
Parent task `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001` identifies and categorizes five distinct root cause categories for stranded tasks in the finalize lane:

1. **`ALREADY_MERGED`**:
   - **Condition**: The task branch commits have already landed in `dev` (e.g. via merged PR), but `ai-status.json` board state was never updated.
   - **Remedy**: Close out task status directly without attempting to re-open a PR (which would fail with "No commits between dev and task/...").
2. **`NO_PR` / `MISSING_PR`**:
   - **Condition**: Task branch was created and pushed, but no open GitHub PR exists (often caused by the ReviewBus detached-HEAD bug recorded as `state="skipped_unpublished_branch"`).
   - **Remedy**: Open a pull request targeting `dev`.
3. **`MISSING_REQUIRED_CHECK`**:
   - **Condition**: PR exists and reported checks pass, but a required check (such as `task-review-gate`) never reported (e.g., task not registered on board).
   - **Remedy**: Register task on board / trigger required gate workflow.
4. **`CI_STALE` / `STALE_BASE`**:
   - **Condition**: PR CI failed on a branch commit that is behind `origin/dev` tip.
   - **Remedy**: Advance task branch onto `dev` base and re-run CI to rule out phantom failures.
5. **`CI_FAILED`**:
   - **Condition**: PR CI failed on the current base SHA (genuine check failure).
   - **Remedy**: Inspect failing check logs and push fix commit.

---

## 2. Parent Implementation Assessment

Parent task `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001` delivers diagnostic tools in `scripts/orchestrator/`:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        Finalize Lane Remediation Diagnostics                           │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│  [ ai-status.json ] ──────────────► finalize_lane_doctor.py                           │
│                                           │                                            │
│                      ┌────────────────────┼────────────────────┐                       │
│                      ▼                    ▼                    ▼                       │
│              ALREADY_MERGED            NO_PR          MISSING_REQUIRED_CHECK           │
│             (Direct Closeout)       (Create PR)          (Register Board)              │
│                                           │                    │                       │
│                      ┌────────────────────┴────────────────────┘                       │
│                      ▼                    ▼                                            │
│                  CI_STALE             CI_FAILED                                        │
│             (Rebase on dev)          (Fix Code)                                        │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Delivered Modules
1. **`scripts/orchestrator/finalize_lane_doctor.py`**:
   - Classifies stranded finalize tasks against severity order (`ALREADY_MERGED`, `NO_PR`, `MISSING_REQUIRED_CHECK`, `CI_STALE`, `CI_FAILED`, `CI_PENDING`, `READY`).
   - Supports CLI flags: `--status` (path to `ai-status.json`), `--repo` (repo root), `--base` (base branch name, default: `dev`), `--emit-commands` (output precise non-destructive remedy commands), `--required-check` (filter required checks).
   - Exit code signals stranded task status (exit 1 if tasks require remediation, exit 0 if all clean or only pending/ready).
2. **Test Suites**:
   - `scripts/orchestrator/test_finalize_lane_doctor.py`
   - Covers severity ranking (`ALREADY_MERGED` > `NO_PR`), missing required check rules, unmerged branch handling, exit code calculations, and JSON status parsing.

> [!NOTE]
> **Parent Branch Scope & Generated State Mirrors**:
> Parent branch `task/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001` (commit `b3bb9de3`) contains 11 touched files (6,621 insertions), but 9 of those files are generated state mirrors (`ai-status.json`, `current-work.md`, `dashboard-bundle.json`, `docs-site/*`). The sole core deliverables are `scripts/orchestrator/finalize_lane_doctor.py` (316 lines) and `scripts/orchestrator/test_finalize_lane_doctor.py` (196 lines).

---

## 3. Acceptance Verification Matrix

| Ref ID | Category | Summary | Verification Method |
|---|---|---|---|
| **A1** | Diagnosis Categorization | Correctly identifies and separates the 5 root causes (`ALREADY_MERGED`, `NO_PR`, `MISSING_REQUIRED_CHECK`, `CI_STALE`, `CI_FAILED`). | `scripts/orchestrator/test_finalize_lane_doctor.py` |
| **A2** | Landed Work Handling | `ALREADY_MERGED` outranks `NO_PR` and prevents invalid `gh pr create` calls. | `test_finalize_lane_doctor.py::test_already_merged_outranks_no_pr` |
| **A3** | CLI Diagnostic Surface | `finalize_lane_doctor.py` parses `ai-status.json` and reports stranded tasks with remedies & exit code signals. | `scripts/orchestrator/test_finalize_lane_doctor.py` |
| **A4** | Non-Destructive Operation | Read-only diagnosis; `--emit-commands` prints explicit remedies without executing state mutations. | Source inspection & test suite |
| **A5** | Code Quality | Clean PEP 8 / Ruff linting and zero type/syntax errors across diagnostic tools. | `ruff check` & `py_compile` |

---

## 4. Verification Suite Commands

To run and verify the diagnostic tools and test suite:

```bash
# 1. Run orchestrator test suite
python3 -m pytest -q scripts/orchestrator/test_finalize_lane_doctor.py

# 2. Check syntax compilation
python3 -m py_compile scripts/orchestrator/finalize_lane_doctor.py

# 3. Run ruff code linter
python3 -m ruff check scripts/orchestrator/

# 4. Check git diff cleanliness
git diff --check origin/dev...HEAD
```

---

## 5. Handoff Note & Reviewer Transition

This sidecar review packet (`support/sidecars/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW.md`) is completed and ready for review.

- **Assigned Reviewer**: `Claude2`
- **Next Action**: Hand off task `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW` to reviewer `Claude2`.
- **Parent Task Alignment**: Upon approval by `Claude2`, parent task owner `Antigravity` can incorporate this review packet into parent task `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001` closeout.
