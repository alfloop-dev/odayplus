"""Immutable diagnostic inputs, not an automatic root-cause decision.

MOD04/ML03/UX04 distinguish evidence from candidate ranking. A saved Forecast
value is recorded model evidence; a source snapshot ID alone does not verify
the underlying observation, bytes or point-in-time availability.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

CAUSE_CATEGORIES = (
    "Revenue Residual", "Store-age Ramp", "Seasonality", "Equipment Availability",
    "Cost Unit", "Customer Experience", "Price", "Ad", "Promotion", "Competitor",
    "External Shock",
)
EVIDENCE_SCHEMA = "forecast-diagnostic-evidence-v1"


class DiagnosticEvidenceUnavailable(RuntimeError):
    """The actual owner/source cannot establish the diagnostic input."""


class DiagnosticEvidenceDenied(PermissionError):
    """Current authenticated object scope does not permit this input."""


class DiagnosticEvidenceConflict(RuntimeError):
    """An immutable identity cannot be rebound to different evidence."""


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class RecordedDiagnosticFact:
    name: str
    value: str | int | float | None
    source_kind: str
    source_id: str
    source_version: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not all(_text(value) for value in (
            self.name, self.source_kind, self.source_id, self.source_version,
        )) or not _aware(self.recorded_at):
            raise ValueError("Diagnostic fact requires an exact recorded source")
        if self.value is not None and type(self.value) not in (str, int, float):
            raise ValueError("Diagnostic fact value is not a recorded scalar")
        if type(self.value) in (int, float) and not math.isfinite(self.value):
            raise ValueError("Diagnostic fact must be finite")

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "recorded_at": self.recorded_at.isoformat(),
                "independent_observation_verified": False}


@dataclass(frozen=True)
class DiagnosticEvidenceEnvelope:
    tenant_id: str
    store_id: str
    issue_id: str
    workflow_version: int
    alert_id: str
    forecast_output_id: str
    prediction_run_id: str
    forecast_version: int
    prediction_origin_time: datetime
    feature_snapshot_time: datetime
    scored_at: datetime
    source_snapshot_ids: tuple[str, ...]
    versions: tuple[tuple[str, str], ...]
    facts: tuple[RecordedDiagnosticFact, ...]
    unknown_categories: tuple[str, ...] = CAUSE_CATEGORIES
    schema_version: str = EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        if not all(_text(value) for value in (
            self.tenant_id, self.store_id, self.issue_id, self.alert_id,
            self.forecast_output_id, self.prediction_run_id,
        )) or self.issue_id != "ISS-FC-" + self.alert_id:
            raise ValueError("Diagnostic evidence requires exact tenant/store/issue/source IDs")
        if any(type(value) is not int or value < 1 for value in (
            self.workflow_version, self.forecast_version,
        )):
            raise ValueError("Diagnostic evidence requires retained versions")
        if not all(_aware(value) for value in (
            self.prediction_origin_time, self.feature_snapshot_time, self.scored_at,
        )) or self.feature_snapshot_time > self.prediction_origin_time:
            raise ValueError("Diagnostic evidence has invalid point-in-time execution metadata")
        if (type(self.source_snapshot_ids) is not tuple or not self.source_snapshot_ids
                or not all(_text(value) for value in self.source_snapshot_ids)
                or len(set(self.source_snapshot_ids)) != len(self.source_snapshot_ids)):
            raise ValueError("Diagnostic evidence requires unique saved snapshot references")
        if (type(self.versions) is not tuple or not self.versions
                or any(type(row) is not tuple or len(row) != 2
                       or not all(_text(value) for value in row) for row in self.versions)
                or len({key for key, _ in self.versions}) != len(self.versions)):
            raise ValueError("Diagnostic evidence versions are not immutable unique bindings")
        if (type(self.facts) is not tuple or not self.facts
                or not all(type(row) is RecordedDiagnosticFact for row in self.facts)
                or len({row.name for row in self.facts}) != len(self.facts)):
            raise ValueError("Diagnostic evidence facts are not immutable unique records")
        for fact in self.facts:
            fact.__post_init__()
        if self.unknown_categories != CAUSE_CATEGORIES or self.schema_version != EVIDENCE_SCHEMA:
            raise ValueError("Evidence collection does not produce cause candidates")

    def source_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "tenant_id": self.tenant_id,
            "store_id": self.store_id, "issue_id": self.issue_id, "alert_id": self.alert_id,
            "forecast_output_id": self.forecast_output_id,
            "prediction_run_id": self.prediction_run_id, "forecast_version": self.forecast_version,
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "feature_snapshot_time": self.feature_snapshot_time.isoformat(),
            "scored_at": self.scored_at.isoformat(),
            "source_snapshot_references": [{"id": value, "verification": "unverified_reference"}
                                           for value in self.source_snapshot_ids],
            "versions": dict(self.versions), "facts": [row.to_dict() for row in self.facts],
            "unknown_categories": list(self.unknown_categories),
            "cause_candidates": None, "evidence_strength": None, "data_confidence": None,
        }

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.source_dict()).encode()).hexdigest()

    @property
    def evidence_id(self) -> str:
        return "diagnostic-evidence-" + str(uuid5(
            NAMESPACE_URL, _canonical([
                self.schema_version, self.tenant_id, self.store_id, self.issue_id,
                self.alert_id, self.forecast_output_id, self.prediction_run_id, self.forecast_version,
            ]),
        ))

    def to_dict(self) -> dict[str, object]:
        return {**self.source_dict(), "evidence_id": self.evidence_id,
                "workflow_version": self.workflow_version, "source_sha256": self.source_sha256}


@dataclass(frozen=True)
class DiagnosticEvidenceJob:
    """Future worker command contains source identity, never caller metric values."""

    tenant_id: str
    store_id: str
    issue_id: str
    expected_source_sha256: str

    def __post_init__(self) -> None:
        if not all(_text(value) for value in (self.tenant_id, self.store_id, self.issue_id)):
            raise ValueError("Diagnostic job requires explicit owned source IDs")
        if (not isinstance(self.expected_source_sha256, str)
                or len(self.expected_source_sha256) != 64
                or any(value not in "0123456789abcdef" for value in self.expected_source_sha256)):
            raise ValueError("Diagnostic job requires an exact expected source hash")

    @classmethod
    def from_payload(cls, payload: object) -> DiagnosticEvidenceJob:
        fields = {"tenant_id", "store_id", "issue_id", "expected_source_sha256"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError("Diagnostic job accepts source identity only")
        return cls(**payload)

    def require_source(self, evidence: DiagnosticEvidenceEnvelope) -> None:
        if (type(evidence) is not DiagnosticEvidenceEnvelope
                or (evidence.tenant_id, evidence.store_id, evidence.issue_id, evidence.source_sha256)
                != (self.tenant_id, self.store_id, self.issue_id, self.expected_source_sha256)):
            raise DiagnosticEvidenceConflict("Diagnostic worker source has changed")


@dataclass(frozen=True)
class RootCauseMethodConfiguration:
    """Explicit method lineage only; there is no built-in production policy."""

    method_id: str
    method_version: str
    parameter_sha256: str
    feature_schema_version: str
    evaluation_dataset_id: str

    def __post_init__(self) -> None:
        if not all(_text(value) for value in (
            self.method_id, self.method_version, self.feature_schema_version, self.evaluation_dataset_id,
        )) or (not isinstance(self.parameter_sha256, str) or len(self.parameter_sha256) != 64
               or any(value not in "0123456789abcdef" for value in self.parameter_sha256)):
            raise ValueError("Root-cause method requires explicit versioned validation lineage")


class VersionedRootCauseMethod(Protocol):
    """Stage C contract; no production method, default weights or scores here."""

    method_id: str
    method_version: str
    parameter_sha256: str

    def analyze(
        self, evidence: DiagnosticEvidenceEnvelope, configuration: RootCauseMethodConfiguration,
    ) -> object: ...
