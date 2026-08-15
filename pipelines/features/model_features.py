from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from models.shared_ml import ArtifactKind, ArtifactRecord, ArtifactStore
from modules.learninghub.infrastructure import LearningHubRepository


class FeaturePipelineError(ValueError):
    pass


@dataclass(frozen=True)
class FeaturePipelineArtifact:
    model_name: str
    dataset_snapshot_id: str
    feature_schema_version: str
    feature_set_id: str | None
    version: str
    feature_names: tuple[str, ...]
    row_count: int
    artifact_id: str
    artifact_uri: str
    content_digest: str
    run_id: str
    created_by: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "feature_schema_version": self.feature_schema_version,
            "feature_set_id": self.feature_set_id,
            "version": self.version,
            "feature_names": list(self.feature_names),
            "row_count": self.row_count,
            "artifact_id": self.artifact_id,
            "artifact_uri": self.artifact_uri,
            "content_digest": self.content_digest,
            "run_id": self.run_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


class FeaturePipelineRunner:
    def __init__(self, *, repository: LearningHubRepository, artifact_store: ArtifactStore) -> None:
        self.repository = repository
        self.artifact_store = artifact_store

    def run(
        self,
        *,
        model_name: str,
        dataset_snapshot_id: str,
        feature_schema_version: str,
        feature_set_id: str | None = None,
        actor: str = "system",
        run_id: str | None = None,
    ) -> FeaturePipelineArtifact:
        snapshot = self.repository.get_dataset_snapshot(dataset_snapshot_id)
        if snapshot is None:
            raise FeaturePipelineError(f"unknown dataset snapshot {dataset_snapshot_id}")
        names = _feature_names(snapshot.records)
        payload = {
            "artifact_type": "feature_matrix",
            "model_name": model_name,
            "dataset_snapshot_id": snapshot.dataset_snapshot_id,
            "feature_schema_version": feature_schema_version,
            "feature_set_id": feature_set_id or snapshot.feature_set_id,
            "view_versions": dict(snapshot.view_versions),
            "source_snapshot_ids": list(snapshot.source_snapshot_ids),
            "feature_names": list(names),
            "records": [
                {
                    "entity_id": record.entity_id,
                    "features": {name: _jsonable(record.features.get(name)) for name in names},
                }
                for record in sorted(snapshot.records, key=lambda item: item.entity_id)
            ],
        }
        data = _canonical_json_bytes(payload)
        digest_suffix = _digest_suffix(data)
        version = f"{feature_schema_version}-{digest_suffix}"
        record = self.artifact_store.put_artifact(
            model_name=model_name,
            version=version,
            kind=ArtifactKind.FEATURE_SPEC,
            data=data,
            content_type="application/json",
            metadata={
                "dataset_snapshot_id": snapshot.dataset_snapshot_id,
                "feature_schema_version": feature_schema_version,
                "feature_set_id": feature_set_id or snapshot.feature_set_id,
                "created_by": actor,
                "run_id": run_id or f"feature-run-{digest_suffix}",
            },
        )
        return _artifact_from_record(
            record,
            model_name=model_name,
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            feature_schema_version=feature_schema_version,
            feature_set_id=feature_set_id or snapshot.feature_set_id,
            feature_names=names,
            row_count=len(snapshot.records),
            run_id=run_id or f"feature-run-{digest_suffix}",
            actor=actor,
        )


def _feature_names(records: tuple[Any, ...]) -> tuple[str, ...]:
    names = sorted({name for record in records for name in record.features})
    if not names:
        raise FeaturePipelineError("dataset snapshot contains no features")
    return tuple(names)


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _digest_suffix(data: bytes) -> str:
    return sha256(data).hexdigest()[:12]


def _artifact_from_record(
    record: ArtifactRecord,
    *,
    model_name: str,
    dataset_snapshot_id: str,
    feature_schema_version: str,
    feature_set_id: str | None,
    feature_names: tuple[str, ...],
    row_count: int,
    run_id: str,
    actor: str,
) -> FeaturePipelineArtifact:
    return FeaturePipelineArtifact(
        model_name=model_name,
        dataset_snapshot_id=dataset_snapshot_id,
        feature_schema_version=feature_schema_version,
        feature_set_id=feature_set_id,
        version=record.version,
        feature_names=feature_names,
        row_count=row_count,
        artifact_id=record.artifact_id,
        artifact_uri=record.uri,
        content_digest=record.content_digest,
        run_id=run_id,
        created_by=actor,
        created_at=record.created_at,
    )


__all__ = ["FeaturePipelineArtifact", "FeaturePipelineError", "FeaturePipelineRunner"]
