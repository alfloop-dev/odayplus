from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

AUDIT_EVIDENCE_POLICY_VERSION = "audit-evidence-export-policy-v1"


@dataclass(frozen=True)
class EvidenceArtifact:
    artifact_id: str
    artifact_type: str
    uri: str
    checksum: str
    produced_by: str
    produced_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "uri": self.uri,
            "checksum": self.checksum,
            "produced_by": self.produced_by,
            "produced_at": self.produced_at.isoformat(),
        }


@dataclass(frozen=True)
class EvidenceExportRequest:
    program_id: str
    purpose: str
    requested_by: str
    from_time: datetime
    to_time: datetime
    correlation_ids: tuple[str, ...]
    export_scope: str
    environment: str = "test"
    build_version: str = "local"
    data_classification: str = "internal"
    sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "purpose": self.purpose,
            "requested_by": self.requested_by,
            "from_time": self.from_time.isoformat(),
            "to_time": self.to_time.isoformat(),
            "correlation_ids": list(self.correlation_ids),
            "export_scope": self.export_scope,
            "environment": self.environment,
            "build_version": self.build_version,
            "data_classification": self.data_classification,
            "sensitive": self.sensitive,
        }


@dataclass(frozen=True)
class DecisionCard:
    decision_id: str
    decision_type: str
    module: str
    title: str
    subject_ref: str
    outcome: str
    owner: str
    decided_at: datetime
    rationale: str
    input_snapshot_id: str
    evidence_refs: tuple[str, ...] = ()
    model_refs: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    audit_event_ids: tuple[str, ...] = ()
    subsidy_requirements: tuple[str, ...] = ()
    controls: tuple[str, ...] = ()
    prediction_ref: str | None = None
    recommendation_ref: str | None = None
    approval_ref: str | None = None
    execution_ref: str | None = None
    outcome_ref: str | None = None
    feature_version: str | None = None
    data_snapshot_id: str | None = None
    artifact_hash: str | None = None
    risk_flags: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    readiness: str = "PENDING"

    def __post_init__(self) -> None:
        object.__setattr__(self, "readiness", self.resolve_readiness())

    def resolve_readiness(self) -> str:
        if self.risk_flags:
            return "NEEDS_REVIEW"
        has_lineage = self.input_snapshot_id and self.policy_refs and self.audit_event_ids
        has_decision = self.owner and self.rationale and self.decided_at
        if self.evidence_refs and has_lineage and has_decision:
            return "READY"
        return "PENDING"

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "decision_id": self.decision_id,
            "decision_type": self.decision_type,
            "module": self.module,
            "title": self.title,
            "subject_ref": self.subject_ref,
            "outcome": self.outcome,
            "owner": self.owner,
            "decided_at": self.decided_at.isoformat(),
            "rationale": self.rationale,
            "input_snapshot_id": self.input_snapshot_id,
            "data_snapshot_id": self.data_snapshot_id,
            "feature_version": self.feature_version,
            "evidence_refs": list(self.evidence_refs),
            "model_refs": list(self.model_refs),
            "policy_refs": list(self.policy_refs),
            "audit_event_ids": list(self.audit_event_ids),
            "subsidy_requirements": list(self.subsidy_requirements),
            "controls": list(self.controls),
            "lifecycle_refs": {
                "prediction": self.prediction_ref,
                "recommendation": self.recommendation_ref,
                "approval": self.approval_ref,
                "execution": self.execution_ref,
                "outcome": self.outcome_ref,
            },
            "artifact_hash": self.artifact_hash,
            "risk_flags": list(self.risk_flags),
            "metrics": dict(self.metrics),
            "readiness": self.readiness,
        }
        payload["card_hash"] = self.content_hash(payload)
        return payload

    def content_hash(self, payload: dict[str, Any] | None = None) -> str:
        canonical = dict(payload or self.to_dict())
        canonical.pop("card_hash", None)
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SubsidyEvidenceRow:
    requirement_id: str
    requirement: str
    decision_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: str
    gap: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "requirement": self.requirement,
            "decision_ids": list(self.decision_ids),
            "evidence_refs": list(self.evidence_refs),
            "status": self.status,
            "gap": self.gap,
        }


@dataclass(frozen=True)
class AuditEvidenceBundle:
    export_id: str
    program_id: str
    purpose: str
    requested_by: str
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    policy_version: str
    decision_cards: tuple[DecisionCard, ...]
    audit_events: tuple[dict[str, Any], ...]
    subsidy_matrix: tuple[SubsidyEvidenceRow, ...]
    missing_requirements: tuple[str, ...]
    bundle_checksum: str
    audit_event_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "program_id": self.program_id,
            "purpose": self.purpose,
            "requested_by": self.requested_by,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "policy_version": self.policy_version,
            "decision_cards": [card.to_dict() for card in self.decision_cards],
            "audit_events": list(self.audit_events),
            "subsidy_matrix": [row.to_dict() for row in self.subsidy_matrix],
            "missing_requirements": list(self.missing_requirements),
            "bundle_checksum": self.bundle_checksum,
            "audit_event_id": self.audit_event_id,
        }


SUBSIDY_REQUIREMENTS: dict[str, str] = {
    "ELIGIBILITY": "Applicant, store, model, or intervention eligibility is documented.",
    "DECISION": "Human or policy decision rationale is captured with actor and timestamp.",
    "EFFECT": "Outcome, effect, or model validation evidence is attached.",
    "CONTROL": "Approval, separation of duties, conflict, rollback, or audit control is present.",
    "TRACE": "Source audit events and artifact references can trace the decision end to end.",
}


def build_subsidy_matrix(cards: tuple[DecisionCard, ...] | list[DecisionCard]) -> tuple[SubsidyEvidenceRow, ...]:
    rows: list[SubsidyEvidenceRow] = []
    for requirement_id, requirement in SUBSIDY_REQUIREMENTS.items():
        matched = tuple(
            card for card in cards if requirement_id in set(card.subsidy_requirements)
        )
        evidence_refs = tuple(
            sorted({ref for card in matched for ref in card.evidence_refs})
        )
        decision_ids = tuple(card.decision_id for card in matched)
        ready_cards = [card for card in matched if card.resolve_readiness() == "READY"]
        status = "READY" if matched and len(ready_cards) == len(matched) else "MISSING"
        gap = "" if status == "READY" else f"Missing ready evidence for {requirement_id}"
        rows.append(
            SubsidyEvidenceRow(
                requirement_id=requirement_id,
                requirement=requirement,
                decision_ids=decision_ids,
                evidence_refs=evidence_refs,
                status=status,
                gap=gap,
            )
        )
    return tuple(rows)


def build_bundle_checksum(
    *,
    export_id: str,
    request: EvidenceExportRequest,
    decision_cards: tuple[DecisionCard, ...] | list[DecisionCard],
    audit_events: tuple[dict[str, Any], ...],
    subsidy_matrix: tuple[SubsidyEvidenceRow, ...],
    generated_at: datetime,
) -> str:
    canonical = {
        "export_id": export_id,
        "request": request.to_dict(),
        "decision_cards": [card.to_dict() for card in decision_cards],
        "audit_events": audit_events,
        "subsidy_matrix": [row.to_dict() for row in subsidy_matrix],
        "generated_at": generated_at.isoformat(),
        "policy_version": AUDIT_EVIDENCE_POLICY_VERSION,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
