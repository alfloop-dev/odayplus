# ODP-ORCH-SIDECAR-ARCHIVED-ID-LOOP-001 Evidence

## Defect

Sidecar ids are derived deterministically by `sidecar_task_id(parent, kind)`
(`.orchestrator/supervisor.py`). `existing_sidecar_signatures()` only scanned
still-live tasks in `ai-status.json`, so as soon as a sidecar was archived its
`parent:kind` signature disappeared from the "already exists" set and the next
underutilization wave re-proposed the same id. `ai_status.py assign` then
rejected it with:

```
Task <id> is archived. Create a new follow-up task instead of reusing the archived task id.
```

Each rejection was logged as `sidecar_task_create_failed` and consumed a slot in
the idle-capacity budget for that wave.

## Fix

`.orchestrator/supervisor.py`

- `sidecar_archive_tasks_dir(config)` — resolves the archive root the same way
  the `assign` child process does (`ORCH_STATUS_ROOT` / `PANTHEON_STATUS_ROOT`,
  falling back to the config status-file parent), so the supervisor checks the
  exact archive that will reject it.
- `archived_sidecar_state(config)` — one pass over
  `<status root>/ai-task-archive/tasks/*.json`, returning both the archived
  `parent:kind` signatures and the set of archived task ids.
- `existing_sidecar_signatures(status, archived_signatures=None)` — folds the
  archived signatures into the live "already exists" set.
- `build_catalog_sidecar_candidates()` / `build_dynamic_sidecar_candidates()` —
  new `archived_task_ids` parameter; a candidate is dropped when its
  deterministic `sidecar_id` is already owned by the archive. The id check
  also covers legacy snapshots that carry no `helper_parent`/`helper_kind`
  metadata, which the signature check alone cannot see.
- `dispatch_underutilization_sidecars()` — scans the archive once per wave and
  threads both sets into the two builders.

## Verification

Unit tests (`.orchestrator/test_supervisor.py::ArchivedSidecarCandidateTests`,
7 new cases):

```
python3 -m pytest .orchestrator/test_supervisor.py
# 374 passed, 137 subtests passed
```

Coverage over the observed production failures — replayed the live activity log
against the live archive with `archived_sidecar_state()`:

```
total sidecar_task_create_failed events: 1963
distinct failing sidecar ids: 15; now filtered by archive: 15
NOT covered: []
```

Every id that produced a `sidecar_task_create_failed` is present in
`ai-task-archive/tasks/`, so all 15 are now dropped during candidate generation
rather than at `assign` time. Replaying the 2026-08-08T08:51 and 08:55 waves
(6 archived ids between them) yields zero archived-id candidates after the fix.

Acceptance item "three consecutive sidecar waves report zero
`sidecar_task_create_failed`" is a runtime observation and must be confirmed on
the live supervisor after this change is deployed; the log replay above is the
static equivalent.
