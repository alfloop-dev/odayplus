# ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001 acceptance packet

- Sidecar task: `ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001`
- Helper kind: `acceptance_packet`
- Sidecar owner: Claude3
- Assigned sidecar reviewer: Claude
- Parent owner: Claude · Parent reviewer: Antigravity
- Observation timestamp: `2026-08-11T06:04:27Z`
- Observation base: `origin/dev` tip `529f0a2c8a722bb27430fb0d614229ef1ea6c127`

## Scope boundary

This is a support-only acceptance checklist, dependency map, and live-topology
evidence record. It does not change `.orchestrator/supervisor.py`,
`scripts/ai_status.py`, `scripts/ai-status.sh`, the rollout primitive, task
truth, canonical architecture documents, registry/governance policy, runtime
configuration, or live state. Only this sidecar artifact is added.

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

## Measured two-plane topology

The single-version-plane invariant is **currently violated in production**. Both
planes are live simultaneously and write the same `ai-status.json`.

| Fact | Code plane (supervisor runtime) | Data plane (status root) |
| --- | --- | --- |
| Path | `/home/lupin/oday-plus-supervisor-runtime-current` -> `oday-plus-supervisor-runtime-529f0a2c8a72` | `/home/lupin/oday-plus-supervisor-live` |
| Branch | `runtime-live-529f0a2c8a72` | `dev` |
| HEAD | `529f0a2c8a722bb27430fb0d614229ef1ea6c127` (= `origin/dev` tip) | `96f94cda56d509f44eb5929997b3ab7a67f1c65c` |
| Behind `origin/dev` | 0 | **1067 commits** |
| Tracked worktree | clean | **dirty** (`MM scripts/ai_status.py`, ` M scripts/ai-status.sh`) |
| `scripts/ai_status.py` | 6521 lines | 6198 lines |

`diff` between the two writer files is 845 lines. The systemd unit
`pantheon-supervisor.service` binds both planes at once:

```text
WorkingDirectory=/home/lupin/oday-plus-supervisor-runtime-current
Environment=PANTHEON_STATUS_ROOT=/home/lupin/oday-plus-supervisor-live
Environment=ORCH_STATUS_ROOT=/home/lupin/oday-plus-supervisor-live
ExecStart=.../oday-plus-supervisor-runtime-current/scripts/run-supervisor.sh \
  --config /home/lupin/.config/pantheon/supervisor-runtime.json --verbose
```

and the running config resolves `status_file` to
`/home/lupin/oday-plus-supervisor-live/ai-status.json`.

### Which writer each caller actually executes

```text
supervisor in-process import ──► runtime writer   (529f0a2c, 6521 lines)   [guarded]
supervisor subprocess sync   ──► live-root writer (96f94cda + overlay)     [unguarded]
worker  scripts/ai-status.sh ──► runtime writer   (529f0a2c, 6521 lines)   [dirty-overlay wrapper]
                                   │
                                   └── all three converge on one file:
                                       /home/lupin/oday-plus-supervisor-live/ai-status.json
```

## Root-cause findings

### D1 — the subprocess writer is bound to the data root, not the code plane

`.orchestrator/supervisor.py:34-46` enforces the single-plane rule fail-closed
for the **in-process** import:

```python
EXPECTED_AI_STATUS_PATH = (SCRIPTS_DIR / "ai_status.py").resolve()
...
if Path(str(runtime_ai_status.__file__)).resolve() != EXPECTED_AI_STATUS_PATH:
    raise RuntimeError("Supervisor must load ai_status from its immutable runtime: ...")
```

Three **subprocess** call sites bypass that guard entirely and derive the script
from the *data* root instead:

| Line | Function | Effect of using the stale writer |
| --- | --- | --- |
| 7153 | `sync_status_pipeline` | dashboard/docs-site sync runs old writer logic |
| 7201 | `sync_dispatched_task_status` | **dispatch status transitions** are written by the old writer |
| 10756 | `create_sidecar_task` | auto-created sidecars are minted by the old writer |

All three compute:

```python
script = config_path(config, "status_file").parent / "scripts" / "ai_status.py"
```

`config_path(config, "status_file").parent` is the status root
(`/home/lupin/oday-plus-supervisor-live`), so the supervisor invokes the
1067-commit-behind writer with `sys.executable` — no wrapper, no guard. The
guard at lines 34-46 proves the invariant was already understood as
load-bearing; it is enforced on exactly one of the two writer paths.

### D2 — the subprocess writer is unversioned, not merely stale

`/home/lupin/oday-plus-supervisor-live/scripts/ai_status.py` is not a clean
old checkout. Against its own `HEAD` it carries **579 insertions and 55
deletions**, of which 182 insertions and 30 deletions are not even staged
(`MM`). Four functions in the running file exist in **no commit** — not in the
data root's `HEAD`, not in its index, and not at `origin/dev`:

| Function | Line in running file | In data-root HEAD | In index | At `origin/dev` |
| --- | --- | --- | --- | --- |
| `status_transaction_lock` | 930 | no | no | no |
| `_merge_status_snapshots` | 952 | no | no | no |
| `persist_status_snapshot` | 977 | no | no | no |
| `reconcile_orphan_sidecars` | 1381 | no | no | no |
| `reconcile_orphan_sidecars_on_disk` | 1435 | no | no | no |

A rollout, `git checkout`, or `git clean` of the data root therefore does not
converge the two planes — it silently deletes production behavior that has no
source of truth. This is the reason the parent task must treat the switch as
*atomic* rather than as a fetch-and-restart.

### D3 — asymmetric locking on one shared file (lost-update race)

The two writers use **different persistence protocols against the same file**.

Runtime writer (`origin/dev`) `main()` — no lock anywhere in the file
(`grep -n 'flock\|fcntl\|LOCK_EX'` returns nothing):

```python
state = load_state()
...
commands[command](state, args)
sync_all(state)
```

Live-root overlay writer `main()` (line 6183) — every mutating command is
serialized:

```python
with status_transaction_lock():
    state = load_state()
    state_before = deepcopy(state)
    commands[command](state, args)
    sync_all(state)
```

`save_state()` is byte-identical in both (atomic temp-file + `os.replace`), so
each individual write is atomic — but the read-modify-write *cycle* is
serialized on only one plane. A worker write through the runtime writer that
interleaves with a supervisor `sync_dispatched_task_status` through the overlay
writer can be silently overwritten by whichever `os.replace` lands last. This
is the "舊 writer 回寫" mechanism named in the parent summary, and it matches
the observed field symptom where an `ai-status.sh` invocation's log line and
status check land while the task status does not move.

### D4 — dispatch-state recurrence vector

The overlay writer's `sync_all()` calls `reconcile_orphan_sidecars(state)`
(line 4635) on **every** mutating command, and `status_transaction_lock` also
wraps `reconcile_orphan_sidecars_on_disk()` (line 1437). Neither routine exists
on the runtime plane. Sidecar/dispatch reconciliation therefore runs only when
the supervisor writes, and is invisible to every worker write. Re-materialized
or superseded sidecar records produced by one plane are not reproducible by the
other — the "派工狀態復發" surface the parent task exists to close.

### D5 — the worker plane's single-plane property rests on a dirty overlay

`/home/lupin/oday-plus-supervisor-live/scripts/ai-status.sh` currently reads:

```bash
status_root="$(cd "$(dirname "$0")/.." && pwd)"
export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"
exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"
```

This is the correct single-plane shape — data root for state, code plane for
logic — but it is an **uncommitted** modification (` M`). The canonical version
at `origin/dev` is:

```bash
exec python3 "$(dirname "$0")/ai_status.py"
```

which resolves to the 1067-behind writer. The fix is not in `dev`; reverting the
data root would move every worker back onto the stale plane. Any mainline
implementation should land this binding in version control rather than inherit
it from the live filesystem.

## Dependency map

| Authority / input | Consumer | Required single-plane condition | Current observed state |
| --- | --- | --- | --- |
| Runtime symlink `oday-plus-supervisor-runtime-current` | supervisor process, worker wrapper | one symlink selects the only executable writer | holds `runtime-529f0a2c8a72`; correct, but only the in-process path is bound to it |
| `SCRIPTS_DIR / ai_status.py` guard (supervisor.py:34-46) | in-process `runtime_ai_status` | fail closed when the import is not the runtime copy | enforced; no subprocess equivalent exists |
| `config_path(config, "status_file").parent` | `sync_status_pipeline`, `sync_dispatched_task_status`, `create_sidecar_task` | must resolve the *code* plane for executables and the *data* plane for state | conflated: resolves the data plane for both |
| `PANTHEON_STATUS_ROOT` / `ORCH_STATUS_ROOT` | worker wrapper, `authoritative_status_root()`, sidecar creation | names the fleet data root only | correct as data; also used to locate code |
| `scripts/ai-status.sh` | every worker status transition | committed binding to the runtime writer | correct behavior, uncommitted (D5) |
| `save_state()` atomic replace | both writers | atomic write per call | identical on both planes; not sufficient without a shared RMW lock |
| `status_transaction_lock()` | overlay writer only | one lock discipline shared by all writers of `ai-status.json` | present on one plane, absent on the other (D3) |
| `sync_all()` → `reconcile_orphan_sidecars()` | overlay writer only | reconciliation identical for every writer | plane-exclusive (D4) |
| `rollout_supervisor_runtime.py` | operator rollout | atomically selects a clean exact-`origin/dev` worktree | works for the code plane; does not converge or validate the data-root writer |
| `check_runtime_freshness.py` | freshness alarm | detects drift/dirtiness of the runtime the service executes | points at the code plane only; the 1067-behind dirty data-plane writer is outside its scope |

## Proposed acceptance checklist

All items are **unchecked**: the parent is `in_progress` and no implementation
commit exists at the observation base.

### Single writer binding

- [ ] Supervisor subprocess status calls execute the same `ai_status.py` file
      object as the guarded in-process import.
- [ ] Executable resolution is separated from state-file resolution;
      `config_path(config, "status_file").parent` no longer selects an
      interpreter target.
- [ ] A guard equivalent to supervisor.py:34-46 fails the subprocess path
      closed when the resolved script is not the runtime copy, covering all
      three call sites (`sync_status_pipeline`, `sync_dispatched_task_status`,
      `create_sidecar_task`).
- [ ] `scripts/ai-status.sh` binds workers to the runtime writer **in version
      control**, so a clean data-root checkout cannot reintroduce D5.

### Atomic switch

- [ ] Rolling the runtime symlink switches every writer path in one step; no
      window exists in which the supervisor and workers execute different
      writer versions.
- [ ] Rollback (`point_link` restore in `rollout_supervisor_runtime.py`) also
      restores the writer used by workers and by supervisor subprocess calls.
- [ ] Rollout refuses to proceed when the data root would still contribute
      executable code, or the design removes that possibility entirely.

### Write-back and recurrence

- [ ] Concurrent worker and supervisor mutations of `ai-status.json` cannot
      lose an update; the read-modify-write cycle is serialized identically for
      every writer.
- [ ] Sidecar/dispatch reconciliation runs identically regardless of which
      caller mutated state, so retired dispatch state cannot recur.
- [ ] A regression reproduces the interleaved-write loss on the pre-fix
      topology and passes on the fixed one.

### Unversioned-overlay retirement

- [ ] `status_transaction_lock`, `_merge_status_snapshots`,
      `persist_status_snapshot`, `reconcile_orphan_sidecars`, and
      `reconcile_orphan_sidecars_on_disk` are either landed in `dev` with tests
      or deliberately dropped with a recorded rationale — not left as an
      uncommitted production overlay.
- [ ] After the change, `/home/lupin/oday-plus-supervisor-live` has no tracked
      modifications to `scripts/ai_status.py` or `scripts/ai-status.sh`.
- [ ] Freshness/drift checking covers the data root's writer, or the data root
      no longer holds an executable writer.

### Scope conformance

- [ ] The parent change stays inside supervisor/status-writer wiring and does
      not broaden L1 canonical truth or governance contracts.
- [ ] This sidecar changes only its own support artifact.

## Reviewer replay

Every claim above is reproducible from these commands.

```bash
# Plane identity and drift
readlink -f /home/lupin/oday-plus-supervisor-runtime-current
git -C /home/lupin/oday-plus-supervisor-runtime-current rev-parse HEAD
git -C /home/lupin/oday-plus-supervisor-live rev-parse HEAD
git -C /home/lupin/oday-plus-supervisor-live rev-list --count HEAD..origin/dev
git -C /home/lupin/oday-plus-supervisor-live status --porcelain scripts/ai_status.py scripts/ai-status.sh

# D2: the running writer is unversioned
git -C /home/lupin/oday-plus-supervisor-live diff --stat HEAD -- scripts/ai_status.py
grep -n '^def status_transaction_lock\|^def persist_status_snapshot' \
  /home/lupin/oday-plus-supervisor-live/scripts/ai_status.py
git -C /home/lupin/oday-plus-supervisor-live show HEAD:scripts/ai_status.py \
  | grep -c '^def status_transaction_lock'   # expect 0

# D1: supervisor subprocess binding
grep -n 'config_path(config, "status_file").parent / "scripts" / "ai_status.py"' \
  .orchestrator/supervisor.py                # expect 7153, 7201, 10756
sed -n '34,46p' .orchestrator/supervisor.py  # the in-process guard

# D3: asymmetric locking
grep -c 'flock\|fcntl\|LOCK_EX' \
  /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py   # expect 0
sed -n '6180,6190p' /home/lupin/oday-plus-supervisor-live/scripts/ai_status.py

# Running config binds status_file to the data root
python3 -c "import json;print(json.load(open('/home/lupin/.config/pantheon/supervisor-runtime.json'))['paths']['status_file'])"

# Sidecar isolation
git diff --stat origin/dev...HEAD
git diff --check origin/dev...HEAD
```

## Explicit negative findings

Recorded so the parent owner does not chase them as plane divergence:

- `command_progress` is **identical** on both planes, including the
  `{"todo", "review_approved"} -> in_progress` transition. The known
  "`progress` downgrades an approved task" behavior is a writer-semantics
  question, not a symptom of the version split, and is out of scope here.
- `save_state()` is **identical** on both planes. Individual writes are already
  atomic; the exposure is the unserialized read-modify-write cycle (D3), not
  torn files.
- `command_note`, `command_reopen`, and `ensure_sprint_started_at` are
  identical in the inspected regions.
- The code plane is healthy on its own terms: clean tree, named branch, zero
  commits behind `origin/dev`. `check_runtime_freshness.py` would report OK,
  which is why this defect is invisible to the existing alarm.

## Independent verification record

The packet preparer verified, at the observation timestamp, that:

1. the reviewed `.orchestrator/supervisor.py` in this sidecar worktree is
   byte-identical to the one in the running runtime
   (`oday-plus-supervisor-runtime-529f0a2c8a72`), so the line numbers cited
   above describe the code actually executing in production;
2. the sidecar diff against `origin/dev` is limited to
   `support/sidecars/ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001/ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001-SIDECAR-ACCEPTANCE.md`;
3. `git diff --check` is clean;
4. no live status file, supervisor source, writer source, runtime symlink, or
   configuration was modified while preparing this packet — all inspection was
   read-only.

No parent test suite was run: the parent has no implementation commit at this
base, so there is nothing to replay beyond the topology evidence above.

## Handoff disposition

This packet records the live two-plane topology with measured evidence, isolates
five contributing defects (D1-D5), maps the dependencies the parent fix must
satisfy, and proposes an acceptance checklist bound to those measurements. It
is handed to sidecar reviewer Claude for review. Parent owner Claude and parent
reviewer Antigravity retain sole authority over whether any of this is absorbed
into `ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001`. Parent acceptance and parent
closeout readiness are **not** claimed.
