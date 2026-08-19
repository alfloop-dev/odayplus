# Sidecar review — ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001-SIDECAR-ACCEPTANCE

- Reviewer: Claude
- Review timestamp: `2026-08-17T14:30Z`
- Reviewed commit: `07504c1e846af9d3d780188ccafdb83482425f92`
- Reviewed artifact: `ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001-SIDECAR-ACCEPTANCE.md` (355 lines)
- Review base: current `dev` tip `3ad0b50333e324caf9c8f7ca1b9c0b7f442618b9`
- Decision: **reopen** (returned to owner `Antigravity2`)

## Verdict

The analysis is sound and the central defect (D1) is still live. The packet is
**not mergeable as written** because its own headline count contradicts its
evidence table, and its citations and replay block no longer resolve against
the current tree or the current live filesystem. An evidence packet whose sole
value is replayable precision must replay.

Four required corrections (R1–R4) below. All are narrow and mechanical; no
re-analysis is needed.

## What was verified and confirmed correct

Replayed against the packet's stated observation base (`529f0a2c`, this
branch's merge base):

| Claim | Result |
| --- | --- |
| D1: three subprocess sites at `supervisor.py:7153,7201,10756` computing `config_path(config, "status_file").parent / "scripts" / "ai_status.py"` | confirmed, all three present |
| In-process fail-closed guard at `supervisor.py:34-46` | confirmed |
| D3: runtime writer has zero `flock`/`fcntl`/`LOCK_EX` | confirmed, `grep -c` = 0 |
| Sidecar diff isolated to one file under `support/sidecars/…/` | confirmed |
| `git diff --check origin/dev...HEAD` | clean |
| Commit trailers (`LLM-Agent`, `Task-ID`, `Reviewer`, `Verified`) | present |

The scope boundary held: no supervisor source, writer source, canonical doc,
governance policy, runtime config, or live state file was touched.

## Required corrections

### R1 — D2 headline undercounts its own table (factual error)

Line 120 reads "**Four** functions in the running file exist in **no commit**".
The table immediately below at lines 123–129 lists **five**:
`status_transaction_lock`, `_merge_status_snapshots`, `persist_status_snapshot`,
`reconcile_orphan_sidecars`, `reconcile_orphan_sidecars_on_disk`. The
retirement checklist at lines 259–263 also lists five, and the commit body says
"five functions present in no commit". Fix line 120 to `Five`.

### R2 — D1 citations are stale; the defect is live but has moved

Refactor `58a76337` ("split supervisor by dispatch and worker domains") moved
the subprocess binding out of `supervisor.py`. At current `dev`, `supervisor.py`
contains no `ai_status.py` subprocess resolution at all — the only remaining
reference is the guard at line 33. The defect now lives at:

- `.orchestrator/status_transition.py:94` — `sync_status_pipeline` path
  (`task_reassignment_sync_failed`)
- `.orchestrator/status_transition.py:164` — dispatch sync path
  (`task_dispatch_sync_failed`)

Both still compute `sv.config_path(config, "status_file").parent / "scripts" / "ai_status.py"`
and both still `cwd` into the data root (lines 109, 201). **D1 is unfixed.** But
a parent owner following the packet's line numbers today finds nothing, and may
wrongly conclude the defect was closed. Re-point D1, the dependency map row, and
the acceptance checklist item to `status_transition.py`. Confirm whether the
third original site (`create_sidecar_task`, old line 10756) survived the split
or was removed — it is no longer locatable by that name.

### R3 — D3 and D5 have been partially overtaken; record the delta

- **D5 has landed.** Commit `937c72d2` ("chore(orchestrator): pin status
  launcher to live runtime") committed the exact `scripts/ai-status.sh` shape
  the packet describes at lines 184–188 as an uncommitted overlay. D5's premise
  ("the fix is not in `dev`") is now false.
- **D3 is partially remediated.** The `dev` writer now has
  `fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)` at `scripts/ai_status.py:1004`
  (`import fcntl` at line 4). The zero-lock claim held at `529f0a2c` but no
  longer describes `dev`.
- The five D2 functions exist in neither the working tree nor `HEAD` of the
  live root today — the unversioned overlay appears to have been retired rather
  than landed. The checklist item at lines 259–263 needs a resolution note
  saying which of "landed with tests" or "deliberately dropped" actually
  happened, since it happened without either being recorded.

### R4 — the Reviewer replay block no longer executes

The measured topology has been dismantled since the 2026-08-11 observation:

| Packet claim | Observed 2026-08-17 |
| --- | --- |
| data plane `/home/lupin/oday-plus-supervisor-live` | **does not exist** |
| `oday-plus-supervisor-runtime-current` -> `runtime-529f0a2c8a72` | -> `/home/lupin/odayplus` (relinked 08-17 13:44) |
| `/home/lupin/.config/pantheon/supervisor-runtime.json` | **does not exist** |
| `pantheon-supervisor.service` systemd unit | **not present** |
| two planes, 1067 commits apart | one directory; code plane == data plane |

Roughly half the commands in "Reviewer replay" (lines 279–310) now exit
non-zero. Keep the block as a dated historical record, but mark it explicitly
as bound to the `2026-08-11T06:04:27Z` observation and add the currently
executable subset — the `status_transition.py` greps and the `dev` writer lock
check — so a future reader can distinguish "this was measured then" from "this
is checkable now".

## Non-blocking observations

**N1 — sidecar owner identity is recorded three different ways.** The packet
header says `Sidecar owner: Claude3`; `ai-status.json` says owner
`Antigravity2`; the commit trailer says `LLM-Agent: Claude3`; the git author is
`Antigravity6`. Reconcile the artifact header with canonical task truth, or
correct the status record — one sidecar should not carry three identities.

**N2 — the parent task does not exist in canonical truth.**
`ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001` appears in neither `ai-status.json`
nor `ai-task-archive/tasks/`. Only the two sidecars
(`-SIDECAR-REVIEW`, `-SIDECAR-ACCEPTANCE`) are present. The packet's "Parent
owner Claude · Parent reviewer Antigravity" line and its entire handoff
disposition therefore have no addressee. This is not the sidecar's fault and
does not block the artifact, but the acceptance packet cannot be handed to a
parent that is not tracked. Raise with the orchestrator lane before treating
this checklist as an active gate.

**N3 — the negative findings section is the packet's strongest part** and
should survive the rewrite unchanged. Ruling out `command_progress`,
`save_state()`, `command_note`, `command_reopen`, and `ensure_sprint_started_at`
as plane divergence, and noting that `check_runtime_freshness.py` reports OK on
exactly this defect, is precisely the kind of evidence that stops a parent owner
from chasing the wrong symptom.

## Reviewer replay for this review

```bash
# R1
grep -n 'Four functions' support/sidecars/ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001/*ACCEPTANCE.md

# R2 — defect moved, not fixed
grep -rn 'status_file").parent / "scripts" / "ai_status.py"' .orchestrator/*.py
grep -n 'ai_status.py' .orchestrator/supervisor.py        # guard only, line 33

# R3
git log --oneline -1 -- scripts/ai-status.sh              # 937c72d2
grep -n 'flock\|fcntl\|LOCK_EX' scripts/ai_status.py      # lines 4, 1004, 1008

# R4
readlink -f /home/lupin/oday-plus-supervisor-runtime-current
test -e /home/lupin/oday-plus-supervisor-live || echo "data plane gone"

# base-of-branch checks that confirmed the packet was accurate when written
git -C <this-worktree> grep -n 'status_file").parent / "scripts" / "ai_status.py"' \
  529f0a2c -- .orchestrator/supervisor.py
```

## Disposition

Returned to owner `Antigravity2` for R1–R4. The analysis does not need to be
redone; the packet needs its count fixed, its citations re-pointed at
`status_transition.py`, and its replay block dated and split into historical
versus currently-executable. On resubmission this reviewer expects to approve.
