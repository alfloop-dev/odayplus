from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ModelRiskLevel(StrEnum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


@dataclass(frozen=True)
class ModelCardApproval:
    approver: str
    role: str
    decision: str = "approved"
    approved_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "approver": self.approver,
            "role": self.role,
            "decision": self.decision,
            "approved_at": self.approved_at.isoformat(),
        }


@dataclass(frozen=True)
class ModelCard:
    model_name: str
    model_version: str
    owner: str
    risk_level: ModelRiskLevel
    intended_use: str
    not_intended_use: str
    dataset_snapshot_id: str
    validation_run_id: str
    feature_set_id: str
    label_set_id: str
    training_period: str
    validation_period: str
    algorithm: str
    baseline: str
    metrics_summary: Mapping[str, float]
    segment_metrics: Sequence[Mapping[str, Any]] = ()
    calibration_summary: Mapping[str, Any] = field(default_factory=dict)
    explainability_method: str = "not_applicable"
    limitations: Sequence[str] = ()
    known_biases: Sequence[str] = ()
    privacy_review: str = "PASSED"
    security_review: str = "PASSED"
    release_status: str = "DEV"
    rollback_conditions: Sequence[str] = ()
    approvals: Sequence[ModelCardApproval] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_complete(self) -> bool:
        required_text = (
            self.model_name,
            self.model_version,
            self.owner,
            self.intended_use,
            self.not_intended_use,
            self.dataset_snapshot_id,
            self.validation_run_id,
            self.feature_set_id,
            self.label_set_id,
            self.training_period,
            self.validation_period,
            self.algorithm,
            self.baseline,
        )
        return (
            all(bool(value) for value in required_text)
            and bool(self.metrics_summary)
            and bool(self.rollback_conditions)
            and self.privacy_review in {"PASSED", "WARNING"}
            and self.security_review in {"PASSED", "WARNING"}
        )

    @property
    def is_approved(self) -> bool:
        return any(approval.decision == "approved" for approval in self.approvals)

    def with_release_status(self, status: str) -> ModelCard:
        return ModelCard(
            model_name=self.model_name,
            model_version=self.model_version,
            owner=self.owner,
            risk_level=self.risk_level,
            intended_use=self.intended_use,
            not_intended_use=self.not_intended_use,
            dataset_snapshot_id=self.dataset_snapshot_id,
            validation_run_id=self.validation_run_id,
            feature_set_id=self.feature_set_id,
            label_set_id=self.label_set_id,
            training_period=self.training_period,
            validation_period=self.validation_period,
            algorithm=self.algorithm,
            baseline=self.baseline,
            metrics_summary=self.metrics_summary,
            segment_metrics=self.segment_metrics,
            calibration_summary=self.calibration_summary,
            explainability_method=self.explainability_method,
            limitations=self.limitations,
            known_biases=self.known_biases,
            privacy_review=self.privacy_review,
            security_review=self.security_review,
            release_status=status,
            rollback_conditions=self.rollback_conditions,
            approvals=self.approvals,
            created_at=self.created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "owner": self.owner,
            "risk_level": self.risk_level.value,
            "intended_use": self.intended_use,
            "not_intended_use": self.not_intended_use,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "validation_run_id": self.validation_run_id,
            "feature_set_id": self.feature_set_id,
            "label_set_id": self.label_set_id,
            "training_period": self.training_period,
            "validation_period": self.validation_period,
            "algorithm": self.algorithm,
            "baseline": self.baseline,
            "metrics_summary": dict(self.metrics_summary),
            "segment_metrics": [dict(metric) for metric in self.segment_metrics],
            "calibration_summary": dict(self.calibration_summary),
            "explainability_method": self.explainability_method,
            "limitations": list(self.limitations),
            "known_biases": list(self.known_biases),
            "privacy_review": self.privacy_review,
            "security_review": self.security_review,
            "release_status": self.release_status,
            "rollback_conditions": list(self.rollback_conditions),
            "approvals": [approval.to_dict() for approval in self.approvals],
            "created_at": self.created_at.isoformat(),
        }


__all__ = ["ModelCard", "ModelCardApproval", "ModelRiskLevel"]
