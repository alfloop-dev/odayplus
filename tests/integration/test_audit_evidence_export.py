from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.opsboard.audit import (
    AuditEvidenceExportService,
    AuditEvidenceExportError,
    DecisionCard,
    EvidenceExportRequest,
)
from shared.audit import AuditEvent, InMemoryAuditLog
from tests.integration._authz import AUDIT_HEADERS


def _now() -> datetime:
    """Stable reference time anchored one hour before wall-clock.

    Every ``expires_at`` in the tests is built as ``_now() + delta`` so that
    sensitive-export authorization can never race against real time the way
    the old fixed ``datetime(2026, 6, 27, …)`` constant did.
    """
    return datetime.now(UTC) - timedelta(hours=1)


def _ready_card(now: datetime, audit_event_id: str = "audit-1") -> DecisionCard:
    return DecisionCard(
        decision_id="decision-intervention-001",
        decision_type="INTERVENTION_EFFECT",
        module="InterventionOps",
        title="Price intervention gross margin effect",
        subject_ref="intervention/intv-001",
        outcome="COMPLETED",
        owner="ops-manager",
        decided_at=now,
        rationale="Observation window matured with DID evidence and positive margin.",
        input_snapshot_id="intervention-input-snapshot-20260627",
        evidence_refs=("label/intv-001", "artifact/effect-report-001"),
        model_refs=("forecast_revenue_interval:1.1.0",),
        policy_refs=("intervention-lifecycle-policy-v1",),
        audit_event_ids=(audit_event_id,),
        subsidy_requirements=("ELIGIBILITY", "DECISION", "EFFECT", "CONTROL", "TRACE"),
        controls=("approval_recorded", "observation_window_matured"),
        prediction_ref="prediction/forecast-20260627",
        recommendation_ref="recommendation/price-change-001",
        approval_ref="approval/intv-001",
        execution_ref="execution/intv-001",
        outcome_ref="outcome/intv-001",
        feature_version="intervention-feature-v1",
        data_snapshot_id="canonical-store-snapshot-20260627",
        artifact_hash="sha256:effect-report-001",
        metrics={"incremental_gross_margin": 48_000.0, "evidence_level": "L3"},
    )


def test_audit_evidence_export_builds_decision_cards_and_subsidy_matrix() -> None:
    now = _now()
    audit_log = InMemoryAuditLog()
    event = audit_log.record(
        AuditEvent(
            event_type="intervention.effect_evaluated.v1",
            actor="analyst-a",
            action="evaluate",
            resource="intervention/intv-001",
            outcome="completed",
            correlation_id="corr-audit-export-1",
            occurred_at=now,
            metadata={"evidence_level": "L3", "label_id": "label/intv-001"},
        )
    )
    service = AuditEvidenceExportService(audit_log=audit_log)

    bundle = service.export(
        EvidenceExportRequest(
            program_id="subsidy-program-2026-q2",
            purpose="quarterly subsidy review",
            requested_by="reviewer-a",
            from_time=now - timedelta(days=1),
            to_time=now + timedelta(days=1),
            correlation_ids=("corr-audit-export-1",),
            export_scope="tenant=t1;region=north;program=subsidy-program-2026-q2",
            environment="ci",
            build_version="test-build",
            data_classification="restricted",
            sensitive=True,
            purpose_scope="subsidy-review:q2",
            expires_at=now + timedelta(hours=4),
            authorized_by="legal-approver",
            authorization_id="authz-sub-2026-q2",
            masking_profile="masked",
        ),
        decision_cards=(_ready_card(now, event.event_id),),
        generated_at=now,
    )

    assert bundle.policy_version == "audit-evidence-export-policy-v1"
    assert bundle.missing_requirements == ()
    assert len(bundle.bundle_checksum) == 64
    assert bundle.decision_cards[0].resolve_readiness() == "READY"
    assert bundle.decision_cards[0].to_dict()["lifecycle_refs"] == {
        "prediction": "prediction/forecast-20260627",
        "recommendation": "recommendation/price-change-001",
        "approval": "approval/intv-001",
        "execution": "execution/intv-001",
        "outcome": "outcome/intv-001",
    }
    assert len(bundle.decision_cards[0].to_dict()["card_hash"]) == 64
    assert {row.requirement_id for row in bundle.subsidy_matrix} == {
        "ELIGIBILITY",
        "DECISION",
        "EFFECT",
        "CONTROL",
        "TRACE",
    }
    assert all(row.status == "READY" for row in bundle.subsidy_matrix)
    assert bundle.audit_events[0]["event_id"] == event.event_id
    assert bundle.audit_events[0]["integrity"]["event_hash"]
    assert bundle.to_dict()["export_governance"]["purpose_scope"] == "subsidy-review:q2"
    assert bundle.to_dict()["audit_chain"]["end"]
    export_events = [
        item
        for item in audit_log.list_events(correlation_id="corr-audit-export-1")
        if item.event_type == "audit.evidence_export.v1"
    ]
    assert export_events[0].metadata["bundle_checksum"] == bundle.bundle_checksum


def test_audit_evidence_export_api_uses_platform_audit_log() -> None:
    now = _now()
    audit_log = InMemoryAuditLog()
    event = audit_log.record(
        AuditEvent(
            event_type="learninghub.model_release.v1",
            actor="ml-owner",
            action="release",
            resource="model/forecast_revenue_interval:1.1.0",
            outcome="approved",
            correlation_id="corr-api-export-1",
            occurred_at=now,
            metadata={"release_type": "FULL", "approval_id": "approval-full-002"},
        )
    )
    app = create_app(audit_log=audit_log)
    client = TestClient(app, headers=AUDIT_HEADERS)

    response = client.post(
        "/audit/evidence/export",
        headers={"X-Correlation-ID": "corr-api-export-1"},
        json={
            "program_id": "subsidy-program-2026-q2",
            "purpose": "model release subsidy audit",
            "requested_by": "auditor-a",
            "from_time": (now - timedelta(hours=1)).isoformat(),
            "to_time": (now + timedelta(hours=1)).isoformat(),
            "correlation_ids": ["corr-api-export-1"],
            "export_scope": "tenant=t1;model=forecast_revenue_interval",
            "environment": "ci",
            "build_version": "test-build",
            "data_classification": "restricted",
            "sensitive": True,
            "purpose_scope": "model-release-subsidy-review",
            "expires_at": (now + timedelta(hours=6)).isoformat(),
            "authorized_by": "legal-approver",
            "authorization_id": "authz-model-release-q2",
            "masking_profile": "masked",
            "decision_cards": [
                {
                    "decision_id": "decision-model-release-001",
                    "decision_type": "MODEL_RELEASE",
                    "module": "Learning Hub",
                    "title": "ForecastOps model release",
                    "subject_ref": "model/forecast_revenue_interval:1.1.0",
                    "outcome": "APPROVED",
                    "owner": "model-review-board",
                    "decided_at": now.isoformat(),
                    "rationale": "Validation passed and rollback target is recorded.",
                    "input_snapshot_id": "forecast-training-1.1.0",
                    "evidence_refs": ["validation/forecast-1.1.0", "model-card/1.1.0"],
                    "model_refs": ["forecast_revenue_interval:1.1.0"],
                    "policy_refs": ["learninghub-release-policy-v1"],
                    "audit_event_ids": [event.event_id],
                    "subsidy_requirements": [
                        "ELIGIBILITY",
                        "DECISION",
                        "EFFECT",
                        "CONTROL",
                        "TRACE",
                    ],
                    "controls": ["approval_id_present", "rollback_target_present"],
                    "prediction_ref": "prediction/shadow-run-1.1.0",
                    "recommendation_ref": "release-request/full-1.1.0",
                    "approval_ref": "approval-full-002",
                    "execution_ref": "model-alias/production",
                    "outcome_ref": "validation/forecast-1.1.0",
                    "feature_version": "store-machine-timeseries-view-v1",
                    "data_snapshot_id": "forecast-training-1.1.0",
                    "artifact_hash": "sha256:model-card-1.1.0",
                    "metrics": {"w4_smape": 0.11, "p80_coverage": 0.82},
                }
            ],
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["program_id"] == "subsidy-program-2026-q2"
    assert payload["correlation_id"] == "corr-api-export-1"
    assert payload["audit_events"][0]["event_id"] == event.event_id
    assert payload["decision_cards"][0]["readiness"] == "READY"
    assert payload["decision_cards"][0]["input_snapshot_id"] == "forecast-training-1.1.0"
    assert payload["decision_cards"][0]["model_refs"] == ["forecast_revenue_interval:1.1.0"]
    assert payload["decision_cards"][0]["policy_refs"] == ["learninghub-release-policy-v1"]
    assert len(payload["decision_cards"][0]["card_hash"]) == 64
    assert payload["missing_requirements"] == []
    assert len(payload["bundle_checksum"]) == 64
    assert payload["export_governance"]["download_evidence_id"]
    assert payload["audit_chain"]["end"]


def test_expired_authorization_is_rejected_by_service() -> None:
    """Regression: sensitive export with past expires_at must fail-closed.

    Production validation (``_validate_request``) rejects any sensitive export
    whose ``expires_at`` is at or before ``generated_at``.  This test proves
    the guard is intact after switching the happy-path tests to dynamic
    timestamps.
    """
    now = _now()
    audit_log = InMemoryAuditLog()
    audit_log.record(
        AuditEvent(
            event_type="intervention.effect_evaluated.v1",
            actor="analyst-a",
            action="evaluate",
            resource="intervention/intv-001",
            outcome="completed",
            correlation_id="corr-expired-1",
            occurred_at=now,
            metadata={},
        )
    )
    service = AuditEvidenceExportService(audit_log=audit_log)

    with pytest.raises(
        AuditEvidenceExportError, match="sensitive export authorization expired"
    ):
        service.export(
            EvidenceExportRequest(
                program_id="subsidy-program-2026-q2",
                purpose="quarterly subsidy review",
                requested_by="reviewer-a",
                from_time=now - timedelta(days=1),
                to_time=now + timedelta(days=1),
                correlation_ids=("corr-expired-1",),
                export_scope="tenant=t1;region=north",
                environment="ci",
                build_version="test-build",
                data_classification="restricted",
                sensitive=True,
                purpose_scope="subsidy-review:q2",
                expires_at=now - timedelta(hours=2),
                authorized_by="legal-approver",
                authorization_id="authz-expired-test",
                masking_profile="masked",
            ),
            decision_cards=(_ready_card(now),),
            generated_at=now,
        )

    # Denial must be audited (durable trail for rejected sensitive exports).
    denial_events = [
        item
        for item in audit_log.list_events(correlation_id="corr-expired-1")
        if item.event_type == "audit.evidence_export.v1"
        and item.outcome == "denied"
    ]
    assert len(denial_events) == 1


def test_expired_authorization_returns_422_via_api() -> None:
    """Regression: the API route surfaces the expired-authorization rejection."""
    now = _now()
    audit_log = InMemoryAuditLog()
    audit_log.record(
        AuditEvent(
            event_type="learninghub.model_release.v1",
            actor="ml-owner",
            action="release",
            resource="model/forecast_revenue_interval:1.1.0",
            outcome="approved",
            correlation_id="corr-api-expired-1",
            occurred_at=now,
            metadata={},
        )
    )
    app = create_app(audit_log=audit_log)
    client = TestClient(app, headers=AUDIT_HEADERS)

    response = client.post(
        "/audit/evidence/export",
        headers={"X-Correlation-ID": "corr-api-expired-1"},
        json={
            "program_id": "subsidy-program-2026-q2",
            "purpose": "model release subsidy audit",
            "requested_by": "auditor-a",
            "from_time": (now - timedelta(hours=1)).isoformat(),
            "to_time": (now + timedelta(hours=1)).isoformat(),
            "correlation_ids": ["corr-api-expired-1"],
            "export_scope": "tenant=t1;model=forecast_revenue_interval",
            "environment": "ci",
            "build_version": "test-build",
            "data_classification": "restricted",
            "sensitive": True,
            "purpose_scope": "model-release-subsidy-review",
            "expires_at": (now - timedelta(hours=2)).isoformat(),
            "authorized_by": "legal-approver",
            "authorization_id": "authz-api-expired-test",
            "masking_profile": "masked",
            "decision_cards": [
                {
                    "decision_id": "decision-model-release-001",
                    "decision_type": "MODEL_RELEASE",
                    "module": "Learning Hub",
                    "title": "ForecastOps model release",
                    "subject_ref": "model/forecast_revenue_interval:1.1.0",
                    "outcome": "APPROVED",
                    "owner": "model-review-board",
                    "decided_at": now.isoformat(),
                    "rationale": "Validation passed.",
                    "input_snapshot_id": "forecast-training-1.1.0",
                    "evidence_refs": [],
                    "model_refs": ["forecast_revenue_interval:1.1.0"],
                    "policy_refs": ["learninghub-release-policy-v1"],
                    "audit_event_ids": [],
                    "subsidy_requirements": ["ELIGIBILITY"],
                    "controls": ["approval_id_present"],
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "expired" in response.json()["detail"].lower()
