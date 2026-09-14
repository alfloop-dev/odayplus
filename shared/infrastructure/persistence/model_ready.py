"""Model-ready dataset-snapshot materialization (ODP-GAP-VIEWS-001).

The Learning Hub domain already knows how to *assemble* a point-in-time correct
:class:`~modules.learninghub.domain.DatasetSnapshot` with a reproducible id
(:func:`~modules.learninghub.domain.build_dataset_snapshot`), and the durable
repositories already know how to *store* one opaque snapshot blob. What was
missing is the materialization step that ties live model-ready view inputs to
durable storage with an auditable lineage/quality header:

* **Fail-closed on absent live inputs** -- an empty upstream never produces a
  persisted (empty or partial) snapshot; callers get a
  :class:`MissingLiveInputError` instead of a silently degraded dataset.
* **Lineage + quality flags** -- every materialization records a queryable
  :class:`LineageManifest` (source snapshot ids, view versions, row/entity
  counts, training/scoring/excluded splits, aggregate + worst-case quality)
  alongside the snapshot, so downstream training/scoring can prove provenance.
* **Reproducible + idempotent** -- re-materializing the same rows yields the
  same ``dataset_snapshot_id`` and upserts in place; re-using that id with a
  different source lineage is rejected as a :class:`LineageConflictError`.

The manifest is persisted through the same document-store backend the rest of
the durable layer uses, so it survives a process restart with the snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from modules.learninghub.domain import (
    DatasetSnapshot,
    ModelReadyRecord,
    build_dataset_snapshot,
)
from shared.domain.models import (
    CURRENT_MEASUREMENT_SCHEMA_VERSION,
    DataSnapshot,
    QualityScoreMeasurement,
)

MODEL_READY_SNAPSHOT_TYPE = "model_ready"

# The *dataset* layout version stamped on a manifest. It is deliberately
# unrelated to ``measurement_schema_version``: a pipeline may legitimately keep
# emitting a "v1" dataset shape while its quality scores are genuinely
# measured under the post-cutover contract.
DEFAULT_SCHEMA_VERSION = "v1"

# A source is either the concrete rows or a zero-arg reader that produces them.
SourceRows = Iterable["ModelReadyRecord | Mapping[str, Any]"]
SourceReader = Callable[[], SourceRows]


class MaterializationError(RuntimeError):
    """Base error for model-ready dataset materialization."""


class MissingLiveInputError(MaterializationError):
    """Raised when materialization is attempted with no live source rows.

    Fail-closed: an empty upstream never yields a persisted (empty or partial)
    snapshot. Callers must surface this rather than silently continue.
    """


class LineageConflictError(MaterializationError):
    """Raised when a reproducible snapshot id is re-materialized with drifted lineage."""


class SnapshotSink(Protocol):
    """Durable/in-memory sink for a snapshot (subset of ``LearningHubRepository``)."""

    def save_dataset_snapshot(self, snapshot: DatasetSnapshot) -> DatasetSnapshot: ...
    def get_dataset_snapshot(self, dataset_snapshot_id: str) -> DatasetSnapshot | None: ...


@dataclass(frozen=True)
class LineageManifest(QualityScoreMeasurement):
    """Queryable lineage + quality header for a materialized dataset snapshot.

    Manifests are persisted as pickles by :class:`DocumentStoreLineageRecorder`,
    so the measurement marker inherited from ``QualityScoreMeasurement``
    resolves a pre-cutover manifest (no marker in its pickled state) back to
    ``"v1"`` instead of silently adopting the new default.
    """

    dataset_snapshot_id: str
    snapshot_type: str
    run_id: str
    schema_version: str
    view_versions: Mapping[str, str]
    source_snapshot_ids: tuple[str, ...]
    entity_count: int
    row_count: int
    training_record_count: int
    scoring_record_count: int
    excluded_record_count: int
    quality_score: float | None
    min_quality_score: float | None
    feature_snapshot_time: datetime
    prediction_origin_time: datetime
    time_range: tuple[datetime, datetime]
    storage_uri: str
    materialized_at: datetime
    measurement_schema_version: str = CURRENT_MEASUREMENT_SCHEMA_VERSION
    quality_score_status: str | None = None

    def to_data_snapshot(self) -> DataSnapshot:
        """Canonical :class:`DataSnapshot` for this materialization.

        This is the production write path for the sixth canonical measured
        column: the audit registry row below is derived from the canonical
        model rather than assembled from raw manifest fields, so the
        model-ready pipeline inherits the model's absence semantics instead of
        re-deriving a parallel (and previously wrong) status of its own.
        """
        return DataSnapshot(
            snapshot_id=self.dataset_snapshot_id,
            snapshot_type=self.snapshot_type,
            source_id=",".join(self.source_snapshot_ids),
            snapshot_time=self.feature_snapshot_time,
            storage_uri=self.storage_uri,
            schema_version=self.schema_version,
            row_count=self.row_count,
            quality_score=(
                round(self.quality_score, 2) if self.quality_score is not None else None
            ),
            created_by_run_id=self.run_id,
            measurement_schema_version=self.measurement_schema_version,
            quality_score_status=self.quality_score_status,
        )

    def to_audit_snapshot_row(self) -> dict[str, Any]:
        """Row shaped for the canonical ``audit.data_snapshots`` registry."""
        snapshot = self.to_data_snapshot()
        return {
            "snapshot_type": snapshot.snapshot_type,
            "source_id": snapshot.source_id,
            "snapshot_time": snapshot.snapshot_time,
            "storage_uri": snapshot.storage_uri,
            "schema_version": snapshot.schema_version,
            "row_count": snapshot.row_count,
            "quality_score": snapshot.effective_quality_score,
            "quality_score_status": snapshot.effective_quality_score_provenance,
            "measurement_schema_version": snapshot.measurement_schema_version,
            "created_by_run_id": snapshot.created_by_run_id,
        }


class LineageRecorder(Protocol):
    """Durable/in-memory store for :class:`LineageManifest` records."""

    def record(self, manifest: LineageManifest) -> LineageManifest: ...
    def get(self, dataset_snapshot_id: str) -> LineageManifest | None: ...
    def list_all(self) -> list[LineageManifest]: ...


@dataclass
class InMemoryLineageRecorder:
    """In-memory lineage recorder (default when no durable store is supplied)."""

    _manifests: dict[str, LineageManifest] = field(default_factory=dict)

    def record(self, manifest: LineageManifest) -> LineageManifest:
        self._manifests[manifest.dataset_snapshot_id] = manifest
        return manifest

    def get(self, dataset_snapshot_id: str) -> LineageManifest | None:
        return self._manifests.get(dataset_snapshot_id)

    def list_all(self) -> list[LineageManifest]:
        return list(self._manifests.values())


class DocumentStoreLineageRecorder:
    """Durable lineage recorder over ``SqliteDocumentStore``.

    Persists manifests in their own collection so lineage/quality can be read
    back independently of the (opaque) snapshot blob, and survives a restart.
    """

    _COLLECTION = "learninghub.dataset_lineage"

    def __init__(self, store: Any) -> None:
        self._store = store

    def record(self, manifest: LineageManifest) -> LineageManifest:
        self._store.put(self._COLLECTION, manifest.dataset_snapshot_id, manifest)
        return manifest

    def get(self, dataset_snapshot_id: str) -> LineageManifest | None:
        return self._store.get(self._COLLECTION, dataset_snapshot_id)

    def list_all(self) -> list[LineageManifest]:
        return list(self._store.list_all(self._COLLECTION))


@dataclass(frozen=True)
class MaterializedSnapshot:
    """A persisted dataset snapshot paired with its lineage/quality manifest."""

    snapshot: DatasetSnapshot
    lineage: LineageManifest


def _iter_rows(source: SourceRows | SourceReader) -> list[Any]:
    rows = source() if callable(source) else source
    return list(rows)


def build_lineage_manifest(
    snapshot: DatasetSnapshot,
    *,
    run_id: str,
    storage_uri: str | None = None,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    measurement_schema_version: str = CURRENT_MEASUREMENT_SCHEMA_VERSION,
    materialized_at: datetime | None = None,
) -> LineageManifest:
    """Derive the lineage/quality header for a built :class:`DatasetSnapshot`."""
    records = snapshot.records
    quality_scores = [
        record.data_quality_score
        for record in records
        if record.data_quality_score is not None
    ]
    excluded = sum(1 for record in records if record.exclusion_reason)
    mean_quality = sum(quality_scores) / len(quality_scores) if quality_scores else None
    return LineageManifest(
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        snapshot_type=MODEL_READY_SNAPSHOT_TYPE,
        run_id=run_id,
        schema_version=schema_version,
        view_versions=dict(snapshot.view_versions),
        source_snapshot_ids=tuple(snapshot.source_snapshot_ids),
        entity_count=snapshot.entity_count,
        row_count=len(records),
        training_record_count=snapshot.training_record_count,
        scoring_record_count=snapshot.scoring_record_count,
        excluded_record_count=excluded,
        quality_score=round(mean_quality, 4) if mean_quality is not None else None,
        min_quality_score=round(min(quality_scores), 4) if quality_scores else None,
        feature_snapshot_time=snapshot.feature_snapshot_time,
        prediction_origin_time=snapshot.prediction_origin_time,
        time_range=snapshot.time_range,
        storage_uri=storage_uri or f"model-ready://{snapshot.dataset_snapshot_id}",
        materialized_at=materialized_at or datetime.now(UTC),
        measurement_schema_version=measurement_schema_version,
    )


class DatasetSnapshotMaterializer:
    """Materialize model-ready view rows into a durable, lineage-tracked snapshot."""

    def __init__(
        self,
        repository: SnapshotSink,
        lineage_recorder: LineageRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._lineage = lineage_recorder or InMemoryLineageRecorder()

    def materialize(
        self,
        source: SourceRows | SourceReader,
        *,
        run_id: str,
        dataset_snapshot_id: str | None = None,
        require_training_eligible: bool = False,
        storage_uri: str | None = None,
        schema_version: str = DEFAULT_SCHEMA_VERSION,
        materialized_at: datetime | None = None,
    ) -> MaterializedSnapshot:
        """Read live inputs, validate point-in-time, and persist with lineage.

        Raises :class:`MissingLiveInputError` when ``source`` yields no rows
        (fail-closed), and propagates
        :class:`~modules.learninghub.domain.PointInTimeViolation` when a record
        would leak future information into a training/scoring feature.
        """
        rows = _iter_rows(source)
        if not rows:
            raise MissingLiveInputError(
                "model-ready materialization requires live source rows; "
                "refusing to persist an empty snapshot"
            )
        snapshot = build_dataset_snapshot(
            rows,
            dataset_snapshot_id=dataset_snapshot_id,
            require_training_eligible=require_training_eligible,
        )
        manifest = build_lineage_manifest(
            snapshot,
            run_id=run_id,
            storage_uri=storage_uri,
            schema_version=schema_version,
            materialized_at=materialized_at,
        )

        existing = self._lineage.get(snapshot.dataset_snapshot_id)
        if existing is not None and existing.source_snapshot_ids != manifest.source_snapshot_ids:
            raise LineageConflictError(
                f"dataset snapshot {snapshot.dataset_snapshot_id} already materialized "
                "with a different source lineage"
            )

        self._repository.save_dataset_snapshot(snapshot)
        self._lineage.record(manifest)
        return MaterializedSnapshot(snapshot=snapshot, lineage=manifest)

    def get(self, dataset_snapshot_id: str) -> MaterializedSnapshot | None:
        """Load a materialized snapshot and its lineage manifest, if present."""
        snapshot = self._repository.get_dataset_snapshot(dataset_snapshot_id)
        if snapshot is None:
            return None
        manifest = self._lineage.get(dataset_snapshot_id)
        if manifest is None:
            # Snapshot persisted without a manifest (e.g. legacy write): derive a
            # best-effort lineage header so callers still get a consistent shape.
            manifest = build_lineage_manifest(
                snapshot, run_id="unknown", materialized_at=snapshot.created_at
            )
        return MaterializedSnapshot(snapshot=snapshot, lineage=manifest)


__all__ = [
    "DEFAULT_SCHEMA_VERSION",
    "MODEL_READY_SNAPSHOT_TYPE",
    "DatasetSnapshotMaterializer",
    "DocumentStoreLineageRecorder",
    "InMemoryLineageRecorder",
    "LineageConflictError",
    "LineageManifest",
    "LineageRecorder",
    "MaterializationError",
    "MaterializedSnapshot",
    "MissingLiveInputError",
    "SnapshotSink",
    "build_lineage_manifest",
]
