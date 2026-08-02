# ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001 Evidence

## 1. Executive Summary

Rollout of the reviewed worktree base-advance policy (`.orchestrator/supervisor.py` from `origin/dev` commit `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`, PR #569) to the live Supervisor runtime environment and execution of systemd service restart to load the reviewed code in-memory.

- **Source Commit**: `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`
- **Target File**: `.orchestrator/supervisor.py`
- **Blob SHA256**: `35b932b6b29dd1ca2e2c228065abd4df5160f177eee17a2bb01ac5d167828a6f`
- **Blob Byte Size**: 448049 bytes
- **Deploy Driver**: `deploy.py` (same-directory sibling, fsync, chmod, verification, atomic `os.replace`, systemd restart verification)

---

## 2. Target Roots

The reviewed blob was published to all active supervisor roots on this host:

1. `/home/lupin/oday-plus/.orchestrator/supervisor.py`
2. `/home/lupin/oday-plus-supervisor-live/.orchestrator/supervisor.py`
3. `/home/lupin/oday-plus-supervisor-runtime-945a8366/.orchestrator/supervisor.py`

Backups of the pre-deployment files are saved at:
`/tmp/odp-rollout-backup/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001-real-*/`

---

## 3. Deployment Receipts & Systemd Restart

- **Negative Rehearsal**: `atomic-publish-rehearsal-negative.txt` (`deploy.py --corrupt-payload`)
  - Verified staging gate caught payload corruption and unlinked temporary sibling without modifying target files.
- **Real Deployment**: `deploy-transcript.txt`
  - Preflight gate: `pantheon-supervisor.service` MainPID 111165, `LoadState=loaded`, `ActiveState=active`, `SubState=running`.
  - Phase 1 (Stage & Verify): Materialized exact blob `de7c5eb2e3dddba2e91f37b90b0be8fdf3fd43ce`, verified sibling sha256, size, mode, and byte-for-byte equivalence across all 3 roots.
  - Phase 2 (Atomic Publish): `os.replace` succeeded across all 3 roots; verified target sha256, size, mode, and inode change.
  - Phase 3 (Import Smoke Test): `python3 -B -c "import supervisor; assert hasattr(supervisor, '_refresh_reused_worker_worktree')"` passed across all 3 roots.
  - Phase 4 (Systemd Restart & Gate): `systemctl --user restart pantheon-supervisor.service` succeeded. Verified post-restart `ActiveState=active`, `SubState=running`, `MainPID` updated (`111165` -> `114080`), and `ExecMainStartTimestamp` updated (`Sun 2026-08-02 07:04:04 UTC` -> `Sun 2026-08-02 07:04:49 UTC`).
  - Supervisor Health: `python3 scripts/supervisor_runtime_health.py` confirmed `healthy=True`, `lifecycle=running`, fresh heartbeat, and `last_loop_error=null`.

---

## 4. Real Base-Advance Reproduction & Fail-Closed Audit

- Log: `base-advance-rebase-required-repro.txt` (`python3 reproduce_base_advance.py`)
  - **Clean Diverged Worktree**: `_refresh_reused_worker_worktree` returns `ok=True` and `status=base_advance_rebase_required:local=...,base=...`, generating the expected owner rebase prompt.
  - **Dirty Worktree**: returns `ok=False` and `status=skipped_dirty_worktree` (fail-closed).
  - **Ref Mismatch**: returns `ok=False` and `status=task_head_mismatch:...` (fail-closed).
  - **Fetch Failure**: returns `ok=False` and `status=wrong_branch:...` (fail-closed).

---

## 5. Verification

- Focused supervisor tests: `/home/lupin/oday-plus/.venv/bin/pytest .orchestrator/test_*.py` (66 passed).
- Syntax compilation: `python3 -m py_compile docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/deploy.py .orchestrator/supervisor.py .orchestrator/test_supervisor.py` (code 0).
- Git diff formatting: `git diff --check` (code 0).

