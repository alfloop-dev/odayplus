# Review Packet: ODP-ORCH-DONE-DELIVERY-PROVENANCE-001

- Sidecar task: `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001`
- Sidecar owner: `Antigravity`
- Sidecar reviewer: `Claude3`
- Canonical parent owner: `Antigravity`
- Canonical parent reviewer: `Antigravity4`
- Evidence captured: `2026-08-05` UTC
- Target / Parent branch: `origin/dev`
- Merged Parent PRs: `#567`, `#586`
- Key parent commits: `91a6eee3`, `400445ab`, `8363d968`, `ad700205`, `28e841eb`, `eede5cff`, `ed27572e`
- Scope: Review packet and evidence summary only; no L1 canonical truth or runtime code modified in this sidecar.

---

## Executive Disposition & Finalization Blocker Summary

Parent task `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001` addresses post-merge done delivery provenance and checkout-liveness in the Pantheon orchestrator.

When GitHub auto-deletes an ephemeral remote task branch (`task/<TASK-ID>`) after PR merge, or when local task checkouts advance post-merge, status script closeout (`scripts/ai-status.sh done`) and supervisor priority dispatch (`.orchestrator/supervisor.py`) previously failed closed or demoted task states because the remote task ref could not be queried.

`ODP-ORCH-DONE-DELIVERY-PROVENANCE-001` remediates this by introducing `resolve_task_checkout_sha(...)` in `scripts/ai_status.py` and updating dispatch reconciliation in `.orchestrator/supervisor.py`. The solution allows merged tasks to finalize using immutable PR provenance (from GitHub API / merge commit records) even after the remote branch has been deleted.

### Canonical State & Current Finalization Blockers:

1. **CI Check Failure on Parent PR #567**:
   - In canonical `ai-status.json`, parent task `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001` remains in status `review_approved`.
   - `next`: `"CI checks for task ODP-ORCH-DONE-DELIVERY-PROVENANCE-001 failed; resolve failing checks before finalization."`
   - `ci_pending_since`: `"2026-08-02T09:33:03Z"`.
   - PR `#567` `task-review-gate` is currently failing. The parent task cannot be finalized to `done` until CI check failures on PR #567 are resolved.

2. **Approved Head Provenance Gap**:
   - Recorded `approved_head` SHA in canonical status: `eede5cffd172814e625dbd3f132970b62935d4db`.
   - Provenance Audit: `eede5cff` is **NOT** an ancestor of `origin/dev`.
   - Merge `ed27572e` carried commit `28e841eb`, not `eede5cff`.
   - Although the underlying code changes (e.g. test mock update) reached `dev` via another path so functional content is intact, the recorded `approved_head` SHA is unreachable from the target branch `origin/dev`.
   - For a task specifically establishing done-delivery provenance and ancestry gating, this is a critical provenance gap that must be recorded and cleared by the parent owner before closeout.

---

## Reviewed Change Surface

The parent task modified four core orchestrator files without altering L1 canonical architecture documents:

| File | Subsystem Role | Implementation Summary |
| --- | --- | --- |
| `scripts/ai_status.py` | Status writer & provenance engine | Implemented `resolve_task_checkout_sha(...)` to resolve checkout HEAD post-merge (checking target branch SHA, local checkout HEAD, or delivery `verified_head`) when the remote task branch is deleted. Updated `command_done` & `collect_done_delivery_metadata` to build immutable PR delivery provenance. |
| `.orchestrator/supervisor.py` | Orchestrator supervisor & dispatcher | Updated `dispatch_priority_for_task` (line 10052) and `dispatch_ready_tasks` (line 10647) to invoke `resolve_task_checkout_sha`, ensuring `owned_finalize_dispatch` remains assigned to the task owner post-merge rather than being suppressed or demoted. |
| `scripts/test_ai_status.py` | Status engine test suite | Added `DoneDeliveryProvenanceRegressionTests` covering deleted remote refs, PR provenance extraction, trailer verification, and state transitions. |
| `.orchestrator/test_supervisor.py` | Supervisor dispatch test suite | Added end-to-end regression tests including `test_post_merge_deleted_remote_branch_finalize_dispatch` to assert correct priority assignment when remote refs are absent post-merge. |

---

## Contract & Provenance Matrix

| Scenario / State | System Behavior | Verification & Evidence |
| --- | --- | --- |
| **Post-Merge Remote Ref Deletion** | `resolve_task_checkout_sha` resolves target branch SHA, local checkout HEAD, or `verified_head` when `origin/task/<TASK-ID>` is missing. | `test_post_merge_deleted_remote_branch_finalize_dispatch` passes. |
| **Finalization via `ai-status.sh done`** | Collects PR merge commit, PR status (`MERGED`), base branch, author, and CI check rollup from GitHub immutable PR provenance. | `DoneDeliveryProvenanceRegressionTests` (131 tests in `scripts.test_ai_status`) pass. |
| **Supervisor Priority Dispatch** | Prevents suppression or demotion of `owned_finalize_dispatch` when task branch PR is merged. | Invoked in `dispatch_priority_for_task` and `dispatch_ready_tasks`. |
| **Trailer & Scope Validation** | Enforces `LLM-Agent`, `Task-ID`, and `Reviewer` trailer validation against immutable PR commit metadata. | `test_ai_status.py` trailer assertion tests pass. |
| **Parent Approved Head Ancestry Gap** | Canonical `approved_head` `eede5cff` is NOT in `origin/dev` ancestry chain (`merge ed27572e` carried `28e841eb`). | Recorded in evidence packet as a provenance gap requiring parent owner reconciliation before `done`. |
| **CI Finalization Gate** | Parent PR `#567` currently fails `task-review-gate`; `ci_pending_since 2026-08-02T09:33:03Z`. | Captured as finalization blocker preventing parent `done` transition. |

---

## Verification & Test Execution Evidence

The following verification suites were executed against the current worktree on `origin/dev`:

### 1. `ai_status` Unit Tests
```bash
python3 -m unittest scripts.test_ai_status
```
**Output**: `Ran 131 tests in 0.768s - OK`

### 2. Supervisor Test Suite
```bash
PYTHONPATH=.orchestrator python3 -m unittest discover -s .orchestrator -p 'test_*.py'
```
**Output**: `Ran 591 tests - OK`

### 3. Code Style & Lint
```bash
python3 -m ruff check scripts/ai_status.py .orchestrator/supervisor.py scripts/test_ai_status.py .orchestrator/test_supervisor.py
```
**Output**: `All checks passed!`

### 4. Git Diff Check
```bash
git diff --check
```
**Output**: `Clean`

---

## Sidecar Boundary & Handoff

- **Support Only**: This document (`support/sidecars/ODP-ORCH-DONE-DELIVERY-PROVENANCE-001/ODP-ORCH-DONE-DELIVERY-PROVENANCE-001-SIDECAR-REVIEW.md`) is the sole deliverable of `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001-SIDECAR-REVIEW`.
- **Zero L1 Mutation**: No canonical architecture docs, governance policies, or runtime production code were modified.
- **Handoff Target**: `Claude3` (assigned sidecar reviewer).
