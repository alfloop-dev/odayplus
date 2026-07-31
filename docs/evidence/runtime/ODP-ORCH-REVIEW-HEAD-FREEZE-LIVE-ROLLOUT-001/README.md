# ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001 Runtime Evidence Index

Owner: Antigravity2
Reviewer: Antigravity5
Phase: Orchestrator Control Plane Live Rollout
Date: 2026-07-31

This directory contains the executable drivers, deployment transcripts, probes, and rollback receipts for rolling PR #505 (merge commit `6af7b86ba4aa34d5bf26142f64f3cb96c429b557`) into the live Supervisor.

## Artifacts

- `deploy.py`: Python atomic deployment driver using same-directory verified temporary siblings + `os.replace`.
- `live_probes.py`: Fail-closed live probes for B23, B24, N3 executed against isolated temporary status root.
- `deploy-transcript.txt`: Complete transcript of atomic publish across `/home/lupin/oday-plus-supervisor-live` and `/home/lupin/oday-plus`.
- `restart-transcript.txt`: Controlled supervisor restart receipt proving new MainPID (262802), active/running state, unchanged NRestarts (0), and fresh heartbeat.
- `probes-transcript.txt`: Fail-closed live probe execution output (3/3 OK).
- `source-verification.txt`: Source blob SHA and SHA256 hashes matching merge commit `6af7b86ba4aa34d5bf26142f64f3cb96c429b557`.
- `rollback-evidence.txt`: Pre-deployment backups and exact rollback commands.
