from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class DataQualityIssue:
    check_name: str
    severity: str
    message: str
    row_index: int | None = None
    field_name: str | None = None


@dataclass(frozen=True)
class DataQualityReport:
    source_id: str
    entity_type: str
    row_count: int
    issues: tuple[DataQualityIssue, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def quality_score(self) -> float:
        if self.row_count == 0:
            return 0.0
        penalty = sum(0.08 if issue.severity == "warning" else 0.2 for issue in self.issues)
        return max(0.0, round(1.0 - penalty, 4))


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


class SourceBatchQualityGate:
    def __init__(
        self,
        *,
        entity_type: str,
        source_id: str,
        required_fields: Iterable[str],
        unique_fields: Iterable[str] = (),
        freshness_field: str = "observation_time",
        max_age: timedelta | None = None,
        event_time_field: str = "event_time",
        observation_time_field: str = "observation_time",
        ingested_at_field: str = "ingested_at",
    ) -> None:
        self.entity_type = entity_type
        self.source_id = source_id
        self.required_fields = tuple(required_fields)
        self.unique_fields = tuple(unique_fields)
        self.freshness_field = freshness_field
        self.max_age = max_age
        self.event_time_field = event_time_field
        self.observation_time_field = observation_time_field
        self.ingested_at_field = ingested_at_field

    def evaluate(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        as_of: datetime | None = None,
    ) -> DataQualityReport:
        checked_rows = list(rows)
        issues: list[DataQualityIssue] = []
        evaluation_time = as_of or datetime.now(UTC)
        if evaluation_time.tzinfo is None:
            evaluation_time = evaluation_time.replace(tzinfo=UTC)

        if not checked_rows:
            issues.append(DataQualityIssue("non_empty", "error", "source batch has no rows"))

        seen: dict[tuple[Any, ...], int] = {}
        for row_index, row in enumerate(checked_rows):
            for field_name in self.required_fields:
                if row.get(field_name) in (None, ""):
                    issues.append(
                        DataQualityIssue(
                            "required_field",
                            "error",
                            f"{field_name} is required",
                            row_index,
                            field_name,
                        )
                    )

            if self.unique_fields:
                key = tuple(row.get(field_name) for field_name in self.unique_fields)
                if key in seen:
                    issues.append(
                        DataQualityIssue(
                            "unique_key",
                            "error",
                            f"duplicate key for {', '.join(self.unique_fields)}",
                            row_index,
                            ",".join(self.unique_fields),
                        )
                    )
                else:
                    seen[key] = row_index

            self._check_freshness(row, row_index, evaluation_time, issues)
            self._check_point_in_time(row, row_index, evaluation_time, issues)

        return DataQualityReport(
            source_id=self.source_id,
            entity_type=self.entity_type,
            row_count=len(checked_rows),
            issues=tuple(issues),
        )

    def _check_freshness(
        self,
        row: Mapping[str, Any],
        row_index: int,
        evaluation_time: datetime,
        issues: list[DataQualityIssue],
    ) -> None:
        if self.max_age is None:
            return
        timestamp = _parse_datetime(row.get(self.freshness_field))
        if timestamp is None:
            issues.append(
                DataQualityIssue(
                    "freshness",
                    "error",
                    f"{self.freshness_field} is required for freshness",
                    row_index,
                    self.freshness_field,
                )
            )
            return
        if evaluation_time - timestamp > self.max_age:
            issues.append(
                DataQualityIssue(
                    "freshness",
                    "warning",
                    f"{self.freshness_field} exceeds max age {self.max_age}",
                    row_index,
                    self.freshness_field,
                )
            )

    def _check_point_in_time(
        self,
        row: Mapping[str, Any],
        row_index: int,
        evaluation_time: datetime,
        issues: list[DataQualityIssue],
    ) -> None:
        event_time = _parse_datetime(row.get(self.event_time_field))
        observation_time = _parse_datetime(row.get(self.observation_time_field))
        ingested_at = _parse_datetime(row.get(self.ingested_at_field))

        if event_time and event_time > evaluation_time:
            issues.append(
                DataQualityIssue(
                    "point_in_time",
                    "error",
                    f"{self.event_time_field} is after evaluation time",
                    row_index,
                    self.event_time_field,
                )
            )
        if event_time and observation_time and observation_time < event_time:
            issues.append(
                DataQualityIssue(
                    "point_in_time",
                    "error",
                    f"{self.observation_time_field} is before {self.event_time_field}",
                    row_index,
                    self.observation_time_field,
                )
            )
        if observation_time and ingested_at and ingested_at < observation_time:
            issues.append(
                DataQualityIssue(
                    "point_in_time",
                    "error",
                    f"{self.ingested_at_field} is before {self.observation_time_field}",
                    row_index,
                    self.ingested_at_field,
                )
            )


__all__ = ["DataQualityIssue", "DataQualityReport", "SourceBatchQualityGate"]
