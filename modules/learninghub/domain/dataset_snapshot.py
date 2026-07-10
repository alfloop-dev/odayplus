from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any


class DatasetSnapshotError(ValueError):
    pass


class PointInTimeViolation(DatasetSnapshotError):
    pass


@dataclass(frozen=True)
class PointInTimeIssue:
    check_name: str
    message: str
    row_index: int | None = None
    field_name: str | None = None


@dataclass(frozen=True)
class ModelReadyRecord:
    view_name: str
    view_version: str
    entity_id: str
    feature_snapshot_time: datetime
    prediction_origin_time: datetime
    source_snapshot_ids: tuple[str, ...] = ()
    data_quality_score: float = 1.0
    confidence: float = 1.0
    is_training_eligible: bool = True
    is_scoring_eligible: bool = True
    exclusion_reason: str = ""
    features: Mapping[str, Any] = field(default_factory=dict)
    labels: Mapping[str, Any] = field(default_factory=dict)
    label_maturity_time: datetime | None = None


@dataclass(frozen=True)
class DatasetSnapshot:
    dataset_snapshot_id: str
    view_versions: Mapping[str, str]
    entity_count: int
    feature_snapshot_time: datetime
    prediction_origin_time: datetime
    time_range: tuple[datetime, datetime]
    source_snapshot_ids: tuple[str, ...]
    records: tuple[ModelReadyRecord, ...]
    feature_set_id: str | None = None
    label_set_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def training_record_count(self) -> int:
        return sum(record.is_training_eligible for record in self.records)

    @property
    def scoring_record_count(self) -> int:
        return sum(record.is_scoring_eligible for record in self.records)


def _parse_datetime(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        raise DatasetSnapshotError(f"{field_name} is required")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value if item not in (None, ""))


def model_ready_record_from_mapping(row: Mapping[str, Any]) -> ModelReadyRecord:
    source_snapshot_ids = _tuple_of_strings(row.get("source_snapshot_ids"))
    known_fields = {
        "view_name",
        "view_version",
        "entity_id",
        "feature_snapshot_time",
        "prediction_origin_time",
        "source_snapshot_ids",
        "data_quality_score",
        "confidence",
        "is_training_eligible",
        "is_scoring_eligible",
        "exclusion_reason",
        "features",
        "labels",
        "label_maturity_time",
    }
    features = dict(row.get("features") or {})
    for key, value in row.items():
        if key not in known_fields:
            features[key] = value
    label_maturity_time = row.get("label_maturity_time")
    return ModelReadyRecord(
        view_name=str(row["view_name"]),
        view_version=str(row["view_version"]),
        entity_id=str(row["entity_id"]),
        feature_snapshot_time=_parse_datetime(
            row["feature_snapshot_time"], field_name="feature_snapshot_time"
        ),
        prediction_origin_time=_parse_datetime(
            row["prediction_origin_time"], field_name="prediction_origin_time"
        ),
        source_snapshot_ids=source_snapshot_ids,
        data_quality_score=float(row.get("data_quality_score", 1.0)),
        confidence=float(row.get("confidence", 1.0)),
        is_training_eligible=bool(row.get("is_training_eligible", True)),
        is_scoring_eligible=bool(row.get("is_scoring_eligible", True)),
        exclusion_reason=str(row.get("exclusion_reason") or ""),
        features=features,
        labels=dict(row.get("labels") or {}),
        label_maturity_time=(
            _parse_datetime(label_maturity_time, field_name="label_maturity_time")
            if label_maturity_time not in (None, "")
            else None
        ),
    )


def validate_point_in_time(
    records: Iterable[ModelReadyRecord | Mapping[str, Any]],
) -> tuple[PointInTimeIssue, ...]:
    issues: list[PointInTimeIssue] = []
    checked_records = tuple(
        row if isinstance(row, ModelReadyRecord) else model_ready_record_from_mapping(row)
        for row in records
    )
    for row_index, record in enumerate(checked_records):
        if record.feature_snapshot_time > record.prediction_origin_time:
            issues.append(
                PointInTimeIssue(
                    "feature_snapshot_after_prediction_origin",
                    "feature_snapshot_time must not be after prediction_origin_time",
                    row_index,
                    "feature_snapshot_time",
                )
            )
        if record.label_maturity_time and record.label_maturity_time > record.feature_snapshot_time:
            issues.append(
                PointInTimeIssue(
                    "label_not_mature",
                    "label_maturity_time must not be after feature_snapshot_time",
                    row_index,
                    "label_maturity_time",
                )
            )
        for field_name in ("event_time", "observation_time", "available_from"):
            value = record.features.get(field_name)
            if value in (None, ""):
                continue
            timestamp = _parse_datetime(value, field_name=field_name)
            upper_bound = (
                record.prediction_origin_time
                if field_name == "event_time"
                else record.feature_snapshot_time
            )
            if timestamp > upper_bound:
                issues.append(
                    PointInTimeIssue(
                        f"{field_name}_after_allowed_time",
                        f"{field_name} violates point-in-time boundary",
                        row_index,
                        field_name,
                    )
                )
        available_to = record.features.get("available_to")
        if available_to not in (None, ""):
            timestamp = _parse_datetime(available_to, field_name="available_to")
            if timestamp <= record.feature_snapshot_time:
                issues.append(
                    PointInTimeIssue(
                        "available_to_before_snapshot",
                        "available_to must be after feature_snapshot_time",
                        row_index,
                        "available_to",
                    )
                )
    return tuple(issues)


def build_dataset_snapshot(
    rows: Iterable[ModelReadyRecord | Mapping[str, Any]],
    *,
    dataset_snapshot_id: str | None = None,
    require_training_eligible: bool = False,
    feature_set_id: str | None = None,
    label_set_id: str | None = None,
) -> DatasetSnapshot:
    records = tuple(
        row if isinstance(row, ModelReadyRecord) else model_ready_record_from_mapping(row)
        for row in rows
    )
    if not records:
        raise DatasetSnapshotError("dataset snapshot requires at least one record")

    issues = validate_point_in_time(records)
    if issues:
        detail = "; ".join(issue.message for issue in issues)
        raise PointInTimeViolation(detail)

    if require_training_eligible and not any(record.is_training_eligible for record in records):
        raise DatasetSnapshotError("dataset snapshot has no training-eligible records")

    view_versions = {record.view_name: record.view_version for record in records}
    source_snapshot_ids = tuple(
        sorted({snapshot_id for record in records for snapshot_id in record.source_snapshot_ids})
    )
    feature_times = tuple(record.feature_snapshot_time for record in records)
    origin_times = tuple(record.prediction_origin_time for record in records)
    snapshot_id = dataset_snapshot_id or _stable_snapshot_id(records)

    return DatasetSnapshot(
        dataset_snapshot_id=snapshot_id,
        view_versions=view_versions,
        entity_count=len({record.entity_id for record in records}),
        feature_snapshot_time=max(feature_times),
        prediction_origin_time=max(origin_times),
        time_range=(min(feature_times), max(origin_times)),
        source_snapshot_ids=source_snapshot_ids,
        records=records,
        feature_set_id=feature_set_id,
        label_set_id=label_set_id,
    )


def _stable_snapshot_id(records: Sequence[ModelReadyRecord]) -> str:
    digest = sha256()
    for record in sorted(records, key=lambda item: (item.view_name, item.entity_id)):
        digest.update(record.view_name.encode())
        digest.update(record.view_version.encode())
        digest.update(record.entity_id.encode())
        digest.update(record.feature_snapshot_time.isoformat().encode())
        digest.update(record.prediction_origin_time.isoformat().encode())
        for snapshot_id in sorted(record.source_snapshot_ids):
            digest.update(snapshot_id.encode())
    return f"ds_{digest.hexdigest()[:16]}"


__all__ = [
    "DatasetSnapshot",
    "DatasetSnapshotError",
    "ModelReadyRecord",
    "PointInTimeIssue",
    "PointInTimeViolation",
    "build_dataset_snapshot",
    "model_ready_record_from_mapping",
    "validate_point_in_time",
]
