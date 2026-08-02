# ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001 Evidence

## 1. Executive Summary

Rollout of the reviewed worktree base-advance policy (`.orchestrator/supervisor.py` from `origin/dev` commit `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`, PR #569) to the live Supervisor runtime environment.

- **Source Commit**: `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`
- **Target File**: `.orchestrator/supervisor.py`
- **Blob SHA256**: `35b932b6b29dd1ca2e2c228065abd4df5160f177eee17a2bb01ac5d167828a6f`
- **Blob Byte Size**: 448049 bytes
- **Deploy Driver**: `deploy.py` (same-directory sibling, fsync, chmod, verification, atomic `os.replace`)

---

## 2. Target Roots

The reviewed blob was published to all active supervisor roots on this host:

1. `/home/lupin/oday-plus/.orchestrator/supervisor.py`
2. `/home/lupin/oday-plus-supervisor-live/.orchestrator/supervisor.py`
3. `/home/lupin/oday-plus-supervisor-runtime-945a8366/.orchestrator/supervisor.py`

Backups of the pre-deployment files are saved at:
`/tmp/odp-rollout-backup/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001-real-*/`

---

## 3. Deployment Receipts

- **Negative Rehearsal**: `atomic-publish-rehearsal-negative.txt` (`deploy.py --corrupt-payload`)
  - Verified staging gate caught payload corruption and unlinked temporary sibling without modifying target files.
- **Real Deployment**: `deploy-transcript.txt`
  - Preflight gate: `pantheon-supervisor.service` MainPID 3805773, `LoadState=loaded`, `ActiveState=active`, `SubState=running`, `NRestarts=0`.
  - Phase 1 (Stage & Verify): Materialized exact blob `de7c5eb2e3dddba2e91f37b90b0be8fdf3fd43ce`, verified sibling sha256, size, mode, and byte-for-byte equivalence across all 3 roots.
  - Phase 2 (Atomic Publish): `os.replace` succeeded across all 3 roots; verified target sha256, size, mode, and inode change.
  - Phase 3 (Import Smoke Test): `python3 -B -c "import supervisor; assert hasattr(supervisor, '_refresh_reused_worker_worktree')"` passed across all 3 roots.
  - Continuity Gate: `pantheon-supervisor.service` MainPID 3805773, `ExecMainStartTimestamp`, and `NRestarts=0` remained identical pre and post rollout.

---

## 4. Verification

- Focused supervisor tests: `PYTHONPATH=.orchestrator python3 -m unittest test_supervisor.ReusedWorkerWorktreeBaseAdvanceTests test_supervisor.ProcessQueueDispatchGuardTests` (27 passed).
- Syntax compilation: `python3 -m py_compile docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/deploy.py .orchestrator/supervisor.py .orchestrator/test_supervisor.py` (code 0).
- Git diff formatting: `git diff --check` (code 0).
