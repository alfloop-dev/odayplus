"""Model-ready dataset-snapshot materialization (ODP-GAP-VIEWS-001).

These tests prove that live model-ready view rows are materialized into a
durable, point-in-time correct dataset snapshot with an auditable lineage and
quality header, a reproducible id, and fail-closed behaviour when the upstream
live inputs are absent.

"Process restart" is simulated by closing the durable engine and rebuilding the
durable repositories/recorder against the same on-disk SQLite file.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.learninghub.domain import PointInTimeViolation
from modules.learninghub.infrastructure import InMemoryLearningHubRepository
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.model_ready import (
    DatasetSnapshotMaterializer,
    DocumentStoreLineageRecorder,
    InMemoryLineageRecorder,
    LineageConflictError,
    MissingLiveInputError,
    build_lineage_manifest,
)
from shared.infrastructure.persistence.repositories import DurableLearningHubRepository

FEATURE_TIME = datetime(2026, 6, 27, tzinfo=UTC)


def _row(entity_id: str, *, quality: float = 1.0, training: bool = True, **extra: object) -> dict:
    row = {
        "view_name": "forecast_training_view",
        "view_version": "v1",
        "entity_id": entity_id,
        "feature_snapshot_time": "2026-06-27T00:00:00Z",
        "prediction_origin_time": "2026-06-27T00:00:00Z",
        "source_snapshot_ids": ["txn-20260626", "machine-20260626"],
        "data_quality_score": quality,
        "is_training_eligible": training,
    }
    row.update(extra)
    return row


def _durable_materializer(db_path) -> tuple[DatasetSnapshotMaterializer, SqliteEngine]:
    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)
    materializer = DatasetSnapshotMaterializer(
        DurableLearningHubRepository(store),
        DocumentStoreLineageRecorder(store),
    )
    return materializer, engine


def test_materialize_persists_snapshot_with_reproducible_id_and_lineage(tmp_path) -> None:
    materializer, engine = _durable_materializer(tmp_path / "durable.sqlite3")
    try:
        result = materializer.materialize(
            [_row("store-1", quality=0.9), _row("store-2", training=False, quality=0.8)],
            run_id="run-forecast-20260627",
        )
    finally:
        engine.close()

    snapshot = result.snapshot
    manifest = result.lineage

    # Reproducible, content-addressed id (no id was supplied).
    assert snapshot.dataset_snapshot_id.startswith("ds_")
    # Lineage indexes provenance, view versions, and the eligibility splits.
    assert manifest.source_snapshot_ids == ("machine-20260626", "txn-20260626")
    assert manifest.view_versions == {"forecast_training_view": "v1"}
    assert manifest.entity_count == 2
    assert manifest.row_count == 2
    assert manifest.training_record_count == 1
    assert manifest.scoring_record_count == 2
    # Quality flags: mean and worst-case are both surfaced.
    assert manifest.quality_score == pytest.approx(0.85)
    assert manifest.min_quality_score == pytest.approx(0.8)
    # The manifest can be projected onto the canonical snapshot registry row.
    audit_row = manifest.to_audit_snapshot_row()
    assert audit_row["snapshot_type"] == "model_ready"
    assert audit_row["row_count"] == 2
    assert audit_row["created_by_run_id"] == "run-forecast-20260627"


def test_materialized_snapshot_survives_process_restart(tmp_path) -> None:
    db_path = tmp_path / "durable.sqlite3"
    writer, engine = _durable_materializer(db_path)
    try:
        result = writer.materialize([_row("store-1")], run_id="run-1")
        snapshot_id = result.snapshot.dataset_snapshot_id
    finally:
        engine.close()

    # Rebuild against the same file -> the snapshot and its lineage are both back.
    reader, engine2 = _durable_materializer(db_path)
    try:
        reloaded = reader.get(snapshot_id)
    finally:
        engine2.close()

    assert reloaded is not None
    assert reloaded.snapshot.dataset_snapshot_id == snapshot_id
    assert reloaded.lineage.run_id == "run-1"
    assert reloaded.lineage.source_snapshot_ids == ("machine-20260626", "txn-20260626")


def test_materialize_fails_closed_on_absent_live_inputs() -> None:
    repository = InMemoryLearningHubRepository()
    materializer = DatasetSnapshotMaterializer(repository)

    with pytest.raises(MissingLiveInputError):
        materializer.materialize([], run_id="run-empty")

    # A zero-arg reader that yields nothing is treated identically (fail-closed).
    with pytest.raises(MissingLiveInputError):
        materializer.materialize(lambda: iter(()), run_id="run-empty")


def test_point_in_time_violation_blocks_materialization_without_persisting() -> None:
    recorder = InMemoryLineageRecorder()
    materializer = DatasetSnapshotMaterializer(InMemoryLearningHubRepository(), recorder)

    with pytest.raises(PointInTimeViolation):
        materializer.materialize(
            [
                _row(
                    "store-1",
                    labels={"daily_net_revenue": 1800.0},
                    label_maturity_time="2026-06-28T00:00:00Z",
                )
            ],
            run_id="run-leaky",
        )

    # Fail-closed: nothing was written for the rejected dataset.
    assert recorder.list_all() == []


def test_rematerialization_is_reproducible_and_idempotent() -> None:
    recorder = InMemoryLineageRecorder()
    materializer = DatasetSnapshotMaterializer(InMemoryLearningHubRepository(), recorder)

    first = materializer.materialize([_row("store-1"), _row("store-2")], run_id="run-a")
    second = materializer.materialize([_row("store-2"), _row("store-1")], run_id="run-b")

    # Same rows (order-independent) -> same content-addressed id, single stored row.
    assert first.snapshot.dataset_snapshot_id == second.snapshot.dataset_snapshot_id
    assert len(recorder.list_all()) == 1


def test_conflicting_lineage_under_pinned_id_is_rejected() -> None:
    repository = InMemoryLearningHubRepository()
    materializer = DatasetSnapshotMaterializer(repository)

    materializer.materialize([_row("store-1")], run_id="run-a", dataset_snapshot_id="ds-pinned")

    with pytest.raises(LineageConflictError):
        materializer.materialize(
            [_row("store-1", source_snapshot_ids=["txn-DIFFERENT"])],
            run_id="run-b",
            dataset_snapshot_id="ds-pinned",
        )


def test_build_lineage_manifest_counts_excluded_records() -> None:
    materializer = DatasetSnapshotMaterializer(InMemoryLearningHubRepository())
    result = materializer.materialize(
        [
            _row("store-1"),
            _row("store-2", training=False, exclusion_reason="label_not_mature"),
        ],
        run_id="run-x",
    )

    assert result.lineage.excluded_record_count == 1
    # Deterministic given the snapshot; recomputed manifest matches the stored one.
    recomputed = build_lineage_manifest(
        result.snapshot,
        run_id="run-x",
        materialized_at=result.lineage.materialized_at,
    )
    assert recomputed == result.lineage
