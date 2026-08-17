# ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001 acceptance packet

- Sidecar task: `ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001` (historical reference; see N2)
- Helper kind: `acceptance_packet`
- Sidecar owner: Antigravity2
- Assigned sidecar reviewer: Claude
- Parent owner: Claude · Parent reviewer: Antigravity
- Initial observation timestamp: `2026-08-11T06:04:27Z`
- Initial observation base: `origin/dev` tip `529f0a2c8a722bb27430fb0d614229ef1ea6c127`
- Revision / base-advance timestamp: `2026-08-17T15:30:00Z`
- Revision base: current `dev` tip `3ad0b50333e324caf9c8f7ca1b9c0b7f442618b9`

## Scope boundary

This is a support-only acceptance checklist, dependency map, and live-topology
evidence record. It does not change `.orchestrator/supervisor.py`,
`.orchestrator/status_transition.py`, `scripts/ai_status.py`,
`scripts/ai-status.sh`, the rollout primitive, task truth, canonical
architecture documents, registry/governance policy, runtime configuration, or
live state. Only this sidecar artifact is added/revised.

Parent acceptance, parent closeout readiness, and remediation authority are
explicitly **NOT** claimed. Parent owner Claude and parent reviewer Antigravity
decide whether and how any of this is absorbed into the mainline
implementation.

## Parent snapshot

Read from the live canonical writer at the observation timestamp:

- Title: `Supervisor 與 status writer 單一版本面`
- Status: `in_progress`; priority `P0`; class `implementation`
- `non_dispatchable: true`, `operator_authorized: true`
- Assignment note: helper-claimed by idle Claude; designated reviewer
  Antigravity preserved
- Declared goal: make the supervisor rollout switch onto a shared status writer
  atomically, removing old-writer write-back and dispatch-state recurrence risk

The parent has no `artifacts` or `acceptance` entries recorded yet, so this
packet proposes an acceptance surface derived from the measured live defect
rather than restating a pre-existing contract.

## Measured two-plane topology (Historical: 2026-08-11)

The single-version-plane invariant was **violated in production at observation base `529f0a2c`**. Both
planes were live simultaneously and wrote the same `ai-status.json`.

| Fact | Code plane (supervisor runtime) | Data plane (status root) |
| --- | --- | --- |
| Path | `/home/lupin/oday-plus-supervisor-runtime-current` -> `oday-plus-supervisor-runtime-529f0a2c8a72` | `/home/lupin/oday-plus-supervisor-live` |
| Branch | `runtime-live-529f0a2c8a72` | `dev` |
| HEAD | `529f0a2c8a722bb27430fb0d614229ef1ea6c127` (= `origin/dev` tip) | `96f94cda56d509f44eb5929997b3ab7a67f1c65c` |
| Behind `origin/dev` | 0 | **1067 commits** |
| Tracked worktree | clean | **dirty** (`MM scripts/ai_status.py`, ` M scripts/ai-status.sh`) |
| `scripts/ai_status.py` | 6521 lines | 6198 lines |

`diff` between the two writer files was 845 lines. The systemd unit
`pantheon-supervisor.service` bound both planes at once:

```text
WorkingDirectory=/home/lupin/oday-plus-supervisor-runtime-current
Environment=PANTHEON_STATUS_ROOT=/home/lupin/oday-plus-supervisor-live
Environment=ORCH_STATUS_ROOT=/home/lupin/oday-plus-supervisor-live
ExecStart=.../oday-plus-supervisor-runtime-current/scripts/run-supervisor.sh \
  --config /home/lupin/.config/pantheon/supervisor-runtime.json --verbose
```

and the running config resolved `status_file` to
`/home/lupin/oday-plus-supervisor-live/ai-status.json`.

### Which writer each caller executed at baseline

```text
supervisor in-process import ──► runtime writer   (529f0a2c, 6521 lines)   [guarded]
supervisor subprocess sync   ──► live-root writer (96f94cda + overlay)     [unguarded]
worker  scripts/ai-status.sh ──► runtime writer   (529f0a2c, 6521 lines)   [dirty-overlay wrapper]
                                   │
                                   └── all three converged on one file:
                                       /home/lupin/oday-plus-supervisor-live/ai-status.json
```

## Root-cause findings & current status

### D1 — the subprocess writer is bound to the data root, not the code plane (LIVE / UNFIXED)

`.orchestrator/supervisor.py:33-45` enforces the single-plane rule fail-closed
for the **in-process** import:

```python
EXPECTED_AI_STATUS_PATH = (SCRIPTS_DIR / "ai_status.py").resolve()
...
if Path(str(runtime_ai_status.__file__)).resolve() != EXPECTED_AI_STATUS_PATH:
    raise RuntimeError("Supervisor must load ai_status from its immutable runtime: ...")
```

At the historical baseline `529f0a2c`, three subprocess call sites bypassed that
guard in `supervisor.py:7153,7201,10756`.

Refactor `58a76337` ("split supervisor by dispatch and worker domains") moved
the subprocess bindings out of `supervisor.py` into
`.orchestrator/status_transition.py`. In current `dev` (`3ad0b503`), the
subprocess call sites are located at:

| Line in `status_transition.py` | Function | Effect of using the stale writer |
| --- | --- | --- |
| 94 | `sync_status_pipeline` | dashboard/docs-site sync runs data-root writer logic (`task_reassignment_sync_failed`) |
| 164 | `sync_dispatched_task_status` | **dispatch status transitions** run data-root writer logic (`task_dispatch_sync_failed`) |

*(Note: the third site `create_sidecar_task`, old `supervisor.py:10756`, was removed during the supervisor domain split).*

Both remaining sites compute:

```python
script = sv.config_path(config, "status_file").parent / "scripts" / "ai_status.py"
```

and both invoke `sys.executable` with `cwd=str(sv.config_path(config, "status_file").parent)`
(lines 109, 201). When `status_file` points to an external data root, the
supervisor invokes the data-root copy of `ai_status.py` — bypassing the
in-process runtime guard. **Defect D1 remains live and unfixed.**

### D2 — the subprocess writer was unversioned, not merely stale (RESOLVED / RETIRED)

In the historical 2026-08-11 topology, `/home/lupin/oday-plus-supervisor-live/scripts/ai_status.py`
carried **579 insertions and 55 deletions** against its own HEAD. **Five** functions
in the running file existed in **no commit** — not in the data root's `HEAD`, not
in its index, and not at `origin/dev`:

| Function | Line in running file | In data-root HEAD | In index | At `origin/dev` |
| --- | --- | --- | --- | --- |
| `status_transaction_lock` | 930 | no | no | no |
| `_merge_status_snapshots` | 952 | no | no | no |
| `persist_status_snapshot` | 977 | no | no | no |
| `reconcile_orphan_sidecars` | 1381 | no | no | no |
| `reconcile_orphan_sidecars_on_disk` | 1435 | no | no | no |

**Resolution delta (2026-08-17):** The unversioned overlay has been retired.
These five functions exist in neither `HEAD` nor the working tree of the repo or
live root today; locking was redesigned cleanly in `dev` (see D3).

### D3 — asymmetric locking on one shared file (PARTIALLY REMEDIATED)

At historical baseline `529f0a2c`, the runtime writer had zero locking
(`grep -n 'flock\|fcntl\|LOCK_EX'` = 0), while the overlay writer serialized
mutating commands under `status_transaction_lock()`.

**Remediation delta (2026-08-17):** `dev` now includes file locking in
`scripts/ai_status.py`:
- `import fcntl` at line 4
- `status_write_transaction()` at line 998:
  ```python
  @contextmanager
  def status_write_transaction():
      lock_file = STATUS_FILE.with_name(f"{STATUS_FILE.name}.lock")
      lock_file.parent.mkdir(parents=True, exist_ok=True)
      with lock_file.open("a+", encoding="utf-8") as lock_handle:
          fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
          try:
              yield
          finally:
              fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
  ```
- Supervisor compare-and-swap writes in `.orchestrator/status_transition.py:76`
  also serialize under `fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)`.

### D4 — dispatch-state recurrence vector (RETIRED)

The overlay writer's `sync_all()` called `reconcile_orphan_sidecars(state)`
(line 4635) and wrapped `reconcile_orphan_sidecars_on_disk()`. Neither routine
existed on the runtime plane. With the unversioned overlay retired, dispatch
state recurrence from unversioned sidecar reconciliation is eliminated.

### D5 — worker launcher single-plane binding (LANDED IN DEV)

The historical dirty overlay at `/home/lupin/oday-plus-supervisor-live/scripts/ai-status.sh`
bound workers to the runtime writer.

**Resolution delta (2026-08-17):** Commit `937c72d2` ("chore(orchestrator): pin
status launcher to live runtime") landed this exact wrapper structure in `dev`
tracked at `scripts/ai-status.sh`:

```bash
#!/bin/bash
set -euo pipefail
status_root="$(cd "$(dirname "$0")/.." && pwd)"
export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"
exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"
```

The premise that "the fix is not in `dev`" has been resolved.

## Dependency map

| Authority / input | Consumer | Required single-plane condition | Observed state at 2026-08-17 (`3ad0b503`) |
| --- | --- | --- | --- |
| Runtime symlink `oday-plus-supervisor-runtime-current` | supervisor process, worker wrapper | one symlink selects the only executable writer | points to `/home/lupin/odayplus` |
| `SCRIPTS_DIR / ai_status.py` guard (`supervisor.py:33-45`) | in-process `runtime_ai_status` | fail closed when the import is not the runtime copy | enforced for in-process import; subprocess equivalent still missing (D1) |
| `config_path(config, "status_file").parent` | `status_transition.py:94,164` (`sync_status_pipeline`, `sync_dispatched_task_status`) | must resolve the *code* plane for executables and the *data* plane for state | conflated: derives `scripts/ai_status.py` from status file parent |
| `PANTHEON_STATUS_ROOT` / `ORCH_STATUS_ROOT` | worker wrapper, `authoritative_status_root()` | names the fleet data root only | exported by `scripts/ai-status.sh` |
| `scripts/ai-status.sh` | every worker status transition | committed binding to the runtime writer | **landed in dev** at commit `937c72d2` |
| `save_state()` atomic replace | writer | atomic write per call | identical on all paths |
| `status_write_transaction()` | `scripts/ai_status.py:1004`, `status_transition.py:76` | shared flock discipline for status file writes | **landed in dev** (D3 partially remediated) |
| `rollout_supervisor_runtime.py` | operator rollout | atomically selects a clean exact-`origin/dev` worktree | works for code plane |
| `check_runtime_freshness.py` | freshness alarm | detects drift/dirtiness of runtime execution | covers runtime tree |

## Proposed acceptance checklist

### Single writer binding

- [ ] Supervisor subprocess status calls execute the same `ai_status.py` file
      object as the guarded in-process import.
- [ ] Executable resolution is separated from state-file resolution;
      `config_path(config, "status_file").parent` in `status_transition.py:94,164`
      no longer selects an interpreter target.
- [ ] A guard equivalent to `supervisor.py:33-45` fails the subprocess path
      closed when the resolved script is not the runtime copy, covering
      `sync_status_pipeline` and `sync_dispatched_task_status`.
- [x] `scripts/ai-status.sh` binds workers to the runtime writer **in version
      control** (landed at `937c72d2`).

### Atomic switch

- [ ] Rolling the runtime symlink switches every writer path in one step; no
      window exists in which the supervisor and workers execute different
      writer versions.
- [ ] Rollback (`point_link` restore in `rollout_supervisor_runtime.py`) also
      restores the writer used by workers and by supervisor subprocess calls.
- [ ] Rollout refuses to proceed when the data root would still contribute
      executable code, or the design removes that possibility entirely.

### Write-back and recurrence

- [x] Concurrent worker and supervisor mutations of `ai-status.json` cannot
      lose an update; read-modify-write cycle is serialized via `fcntl.flock`
      (`scripts/ai_status.py:1004` and `status_transition.py:76`).
- [x] Sidecar/dispatch reconciliation runs identically regardless of which
      caller mutated state; unversioned sidecar reconciliation routines
      retired.
- [ ] A regression test reproduces the interleaved-write loss on the pre-fix
      topology and passes on the fixed one.

### Unversioned-overlay retirement

- [x] `status_transaction_lock`, `_merge_status_snapshots`,
      `persist_status_snapshot`, `reconcile_orphan_sidecars`, and
      `reconcile_orphan_sidecars_on_disk` have been retired from production.
- [x] `/home/lupin/odayplus` (the current live canonical root) has no untracked
      or dirty modifications to `scripts/ai_status.py` or `scripts/ai-status.sh`.
- [x] Freshness/drift checking covers the runtime writer.

### Scope conformance

- [ ] The parent change stays inside supervisor/status-writer wiring and does
      not broaden L1 canonical truth or governance contracts.
- [x] This sidecar changes only its own support artifact.

## Reviewer replay

### A. Currently executable replay (as of 2026-08-17 on updated tree)

```bash
# 1. Verify D1 subprocess bindings in status_transition.py (lines 94, 164)
grep -n 'config_path(config, "status_file").parent / "scripts" / "ai_status.py"' .orchestrator/status_transition.py

# 2. Verify in-process guard in supervisor.py
sed -n '33,45p' .orchestrator/supervisor.py

# 3. Verify D3 locking in ai_status.py
grep -n 'flock\|fcntl\|LOCK_EX' scripts/ai_status.py
# Expect lines 4, 1004, 1008

# 4. Verify D5 committed wrapper in scripts/ai-status.sh
git log --oneline -1 -- scripts/ai-status.sh  # commit 937c72d2
cat scripts/ai-status.sh

# 5. Verify sidecar diff isolation
git diff --stat origin/dev...HEAD
git diff --check origin/dev...HEAD
```

### B. Historical observation replay (bound to 2026-08-11T06:04:27Z at base 529f0a2c)

*Note: The commands below record the verification executed against the historical two-plane topology:*

```bash
# Historical plane identity and drift
readlink -f /home/lupin/oday-plus-supervisor-runtime-current
git -C /home/lupin/oday-plus-supervisor-runtime-current rev-parse HEAD
git -C /home/lupin/oday-plus-supervisor-live rev-parse HEAD
git -C /home/lupin/oday-plus-supervisor-live rev-list --count HEAD..origin/dev
git -C /home/lupin/oday-plus-supervisor-live status --porcelain scripts/ai_status.py scripts/ai-status.sh

# Historical D2 unversioned functions in live root
git -C /home/lupin/oday-plus-supervisor-live diff --stat HEAD -- scripts/ai_status.py
grep -n '^def status_transaction_lock\|^def persist_status_snapshot' \
  /home/lupin/oday-plus-supervisor-live/scripts/ai_status.py

# Historical D1 supervisor subprocess binding at 529f0a2c
git grep -n 'config_path(config, "status_file").parent / "scripts" / "ai_status.py"' 529f0a2c -- .orchestrator/supervisor.py
```

## Explicit negative findings

Recorded so the parent owner does not chase them as plane divergence:

- `command_progress` is **identical** on both planes, including the
  `{"todo", "review_approved"} -> in_progress` transition. The known
  "`progress` downgrades an approved task" behavior is a writer-semantics
  question, not a symptom of the version split, and is out of scope here.
- `save_state()` is **identical** on both planes. Individual writes are already
  atomic; the exposure was the unserialized read-modify-write cycle (D3), not
  torn files.
- `command_note`, `command_reopen`, and `ensure_sprint_started_at` are
  identical in the inspected regions.
- The code plane was healthy on its own terms: clean tree, named branch, zero
  commits behind `origin/dev`. `check_runtime_freshness.py` reported OK,
  which is why this defect was invisible to the existing alarm.

## Independent verification record

The packet preparer verified that:

1. Base advance from `origin/main` (`574dde52`) and `origin/dev` (`3ad0b503`) was
   cleanly merged into this task branch with zero conflicts.
2. The reviewed citations for D1 match `.orchestrator/status_transition.py:94,164`
   and `.orchestrator/supervisor.py:33-45`.
3. D5 was confirmed landed in `scripts/ai-status.sh` (`937c72d2`).
4. D3 flock locking was confirmed at `scripts/ai_status.py:1004` and
   `status_transition.py:76`.
5. The sidecar diff against `origin/dev` is strictly isolated to
   `support/sidecars/ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001/ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001-SIDECAR-ACCEPTANCE.md`.
6. `git diff --check origin/dev...HEAD` is clean.
7. All status updates and inspections were performed non-destructively.

## Handoff disposition

This packet records the single-version-plane topology findings, isolates the
contributing defect surface (D1 live; D2, D4 retired; D3, D5 remediated/landed),
maps the dependencies the parent fix must satisfy, and proposes an acceptance
checklist bound to current code truth. It is handed to sidecar reviewer Claude
for round-2 review.
