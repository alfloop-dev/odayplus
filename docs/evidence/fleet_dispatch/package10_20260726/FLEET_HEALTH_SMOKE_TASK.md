# Package 10 Fleet Health Smoke Task

- `task_id`: `ODP-P10-FLEET-SMOKE-001`
- `status`: `ready_for_pickup`
- `owner`: `fleet-health`
- `worktree`: `/home/lupin/oday-plus-package10-final`
- `branch`: `fix/package10-final-20260725`
- `canonical_archive`: `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/Oday Plus 營運管理後台 (10).zip`
- `ack_path`: `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-FLEET-SMOKE-001.json`

## Purpose

Prove that a Fleet can pick up a repository task after the 2026-07-26
environment restart, read the canonical Package 10 source from the correct
worktree, write an isolated ACK, and return a completion result.

This task does not authorize product, test, configuration, archive, or task
ledger changes.

## Required Checks

1. Confirm the current branch is `fix/package10-final-20260725`.
2. Confirm the archived Package 10 ZIP and extracted HTML exist.
3. Compute both SHA-256 values.
4. Confirm the manifest identifies Package 10.
5. Confirm the worktree is clean before writing the ACK.
6. Write only the declared ACK path.

Expected hashes:

- ZIP: `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8`
- HTML: `cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`

## ACK Contract

The ACK must be valid JSON and contain:

- `task_id`
- `status`
- `picked_up_at`
- `completed_at`
- `worktree`
- `branch`
- `read_before_work`
- `checks`
- `changed_paths`
- `conflicts`
- `next`

`status` is `pass` only when every required check succeeds naturally.
`changed_paths` must contain only the ACK path. Any mismatch is `no_go`.

## Forbidden Paths

- `apps/**`
- `packages/**`
- `tests/**`
- `scripts/**`
- `docs/design/**`
- `docs_archive/**`
- every file under `docs/evidence/**` except the declared ACK

