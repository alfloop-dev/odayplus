# ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001 Runtime Evidence Index

Original rollout owner: Antigravity2
Original reviewer: Antigravity5
Current remediation owner: Claude2
Current reviewer: Antigravity2
Phase: Orchestrator Control Plane Live Rollout
Rollout date: 2026-07-31 · Last remediation: 2026-08-17

This directory contains the executable drivers, deployment transcripts, probes, and rollback receipts for rolling PR #505 (merge commit `6af7b86ba4aa34d5bf26142f64f3cb96c429b557`) into the live Supervisor.

## Artifacts

- `deploy.py`: Python atomic deployment driver using same-directory verified temporary siblings + `os.replace`.
- `live_probes.py`: Fail-closed live probes for B23, B24, N3 executed against an isolated temporary status root copied from the live root. The live root is resolved from `PANTHEON_STATUS_ROOT`, then the `oday-plus-supervisor-runtime-current` symlink, then the retired `/home/lupin/oday-plus-supervisor-live` path.
- `deploy-transcript.txt`: Complete transcript of atomic publish across `/home/lupin/oday-plus-supervisor-live` and `/home/lupin/oday-plus`.
- `restart-transcript.txt`: Controlled supervisor restart receipt proving new MainPID (262802), active/running state, and unchanged NRestarts (0), while explicitly recording that unrelated-worker termination was not compliant and the required pre-write heartbeat was not captured.
- `probes-transcript.txt`: Fail-closed live probe execution output. Run 1 (2026-07-31, retired live root) and Run 2 (2026-08-17, relocated live root) — both 3/3 OK.
- `live-state-2026-08-17.txt`: Read-only re-verification of the live runtime after the root relocation — deployed bytes, PR #505 ancestry, freeze control points present in the live files, and the note that the 2026-07-31 byte freeze is superseded by ordinary `dev` tracking.
- `source-verification.txt`: Source blob SHA and SHA256 hashes matching merge commit `6af7b86ba4aa34d5bf26142f64f3cb96c429b557`.
- `rollback-evidence.txt`: Pre-deployment backups and exact rollback commands.
