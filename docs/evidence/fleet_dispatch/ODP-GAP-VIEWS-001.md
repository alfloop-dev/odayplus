# Fleet Execution Brief: ODP-GAP-VIEWS-001

- Parent: ODP-GAP-VIEWS-001
- Status: review_approved
- Scope boundary: shared/infrastructure/persistence, tests/integration
- Owner: Antigravity (Reassigned from Claude)
- Reviewer: Claude2
- Release authority: PR #219 headRefOid and attached checks

## Objective

Implement model-ready views and dataset snapshot materialization with point-in-time correctness, lineage, quality flags, and reproducible snapshot ids.

## Implementation Evidence

The model-ready dataset-snapshot materialization has been implemented and verified to correctly integrate the `learninghub` domain logic with the durable persistence layer:

1. **Fail-closed on absent live inputs**:
   - Upstream source yielding no rows raises a `MissingLiveInputError`.
   - No incomplete or degraded snapshots are written to the database.

2. **Lineage Manifest & Quality Flags**:
   - `LineageManifest` records training, scoring, and excluded splits, entity/row counts, and average + worst-case quality scores.
   - Projectable onto the canonical `audit.data_snapshots` registry format.

3. **Reproducibility & Idempotency**:
   - Content-addressed `dataset_snapshot_id` generation via `build_dataset_snapshot`.
   - Re-materializing identical rows performs an in-place idempotent upsert.
   - Re-using a pinned snapshot ID with a different source lineage raises a `LineageConflictError`.

4. **Durable Lineage Storage**:
   - `DocumentStoreLineageRecorder` persists manifests independently in a SQLite collection (`learninghub.dataset_lineage`), surviving process restarts.

## Verification Evidence

All tests pass cleanly:

```bash
uv run pytest tests/integration/test_model_ready_materialization.py
```

Result:
```text
tests/integration/test_model_ready_materialization.py .......             [100%]
7 passed in 0.46s
```

Code style analysis check passes cleanly:

```bash
uv run ruff check
```

Result:
```text
All checks passed!
```

## Acceptance Criteria Status

- **Fail-closed when external live inputs are absent**: Verified (`MissingLiveInputError` raised and blocks persistence).
- **Meets scope in docs/evidence/fleet_dispatch/ODP-GAP-VIEWS-001.md**: Documented in this file.
- **Scoped task-branch PR with green required checks**: Required checks are green (PR #219).
