# ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001: Roll out reviewed worktree base-advance policy to live Supervisor

Owner: Antigravity · Reviewer: Antigravity3 · Phase: Orchestrator Control Plane

Depends on ODP-ORCH-WORKTREE-BASE-ADVANCE-001 (PR #569, merged).

This task rolls out the reviewed worktree base-advance policy (`.orchestrator/supervisor.py`) from commit `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f` to all authoritative live Supervisor roots. Receipts and deployment driver live under `docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/`.

**Status: Deployed & verified, submitted for review.**

---

## 1. Rollout Source

| Item | Value |
| --- | --- |
| Source Commit | `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f` (PR #569) |
| Deployed Blob | `.orchestrator/supervisor.py` |
| Blob sha256 | `35b932b6b29dd1ca2e2c228065abd4df5160f177eee17a2bb01ac5d167828a6f` |
| Blob Byte Length | 448,049 bytes |

The deployed code delivers the fail-closed reusable worker-worktree lease policy:
- Fast-forwards clean task branches behind fetched base.
- Preserves clean task branches already containing fetched base.
- Supplies explicit rebase-required prompts for clean matching diverged task HEADs.
- Fail-closed blocking for dirty state, branch/repo mismatches, fetch failures, or unverifiable refs.

---

## 2. Target Roots Deployed

The reviewed `.orchestrator/supervisor.py` blob was deployed across all three authoritative supervisor target roots:

1. `/home/lupin/oday-plus` (Primary Repo Root)
2. `/home/lupin/oday-plus-supervisor-live` (Canonical Status Root)
3. `/home/lupin/oday-plus-supervisor-runtime-945a8366` (Live Running Process Root for `pantheon-supervisor.service`)

Before deployment, backups of existing files were created under `/tmp/odp-rollout-backup/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001-real-*/`.

---

## 3. Deployment Safety Protocol & Atomic Publish

Deployment was driven by `docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/deploy.py`:

1. **Preflight Gate**: Checked systemd unit `pantheon-supervisor.service` via `systemctl --user show`. Asserted `LoadState=loaded`, `ActiveState=active`, `SubState=running`, `MainPID=3805773`, `ExecMainStartTimestamp="Sun 2026-08-02 03:06:42 UTC"`, and `NRestarts=0`. Verified process cmdline is python3 running `supervisor.py`.
2. **Phase 1 (Staging & Verification)**: Materialized exact blob `de7c5eb2e3dddba2e91f37b90b0be8fdf3fd43ce` from commit `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`. Staged same-directory temporary siblings (`.supervisor.py.ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001.<pid>.tmp`), performed `os.fsync`, `os.chmod` matching target mode, and verified same filesystem, sha256 match, byte length, mode, and byte-for-byte compare.
3. **Phase 2 (Atomic Publish)**: Issued `os.replace(sibling, target)` and directory `os.fsync` across all target roots. Asserted post-publish target sha256, byte length, mode, and confirmed inode changed (proving atomic rename).
4. **Phase 3 (Import Smoke Test)**: Executed Python import probe (`python3 -B -c "import supervisor; assert hasattr(supervisor, '_refresh_reused_worker_worktree')"`) inside each root directory. All roots passed cleanly.
5. **Continuity Gate**: Re-probed `pantheon-supervisor.service` post-publish. `MainPID` (3805773), `ExecMainStartTimestamp`, and `NRestarts` (0) were 100% identical to preflight state.
6. **Negative Rehearsal**: Executed `deploy.py --corrupt-payload` to prove that any checksum or byte-verification failure immediately unlinks the temporary sibling and aborts without touching target files.

---

## 4. Verification & Testing

- **Focused Unit Tests**: Executed `PYTHONPATH=.orchestrator python3 -m unittest test_supervisor.ReusedWorkerWorktreeBaseAdvanceTests test_supervisor.ProcessQueueDispatchGuardTests` (27 tests passed cleanly).
- **Compilation Check**: `python3 -m py_compile docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/deploy.py .orchestrator/supervisor.py .orchestrator/test_supervisor.py` passed with code 0.
- **Diff Check**: `git diff --check` passed cleanly with exit code 0.

---

## 5. Artifacts and Transcripts

- Deployment Script: `docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/deploy.py`
- Deployment Transcript: `docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/deploy-transcript.txt`
- Negative Rehearsal Transcript: `docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/atomic-publish-rehearsal-negative.txt`
- Detailed Evidence Readme: `docs/evidence/runtime/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001/README.md`
