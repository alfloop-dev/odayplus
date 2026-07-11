from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.opsboard.audit import (
    AuditEvidenceExportService,
    DecisionCard,
    EvidenceExportRequest,
)
from shared.audit import AuditEvent, InMemoryAuditLog
from tests.integration._authz import AUDIT_HEADERS

NOW = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


def _ready_card(audit_event_id: str = "audit-1") -> DecisionCard:
    return DecisionCard(
        decision_id="decision-intervention-001",
        decision_type="INTERVENTION_EFFECT",
        module="InterventionOps",
        title="Price intervention gross margin effect",
        subject_ref="intervention/intv-001",
        outcome="COMPLETED",
        owner="ops-manager",
        decided_at=NOW,
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
    audit_log = InMemoryAuditLog()
    event = audit_log.record(
        AuditEvent(
            event_type="intervention.effect_evaluated.v1",
            actor="analyst-a",
            action="evaluate",
            resource="intervention/intv-001",
            outcome="completed",
            correlation_id="corr-audit-export-1",
            occurred_at=NOW,
            metadata={"evidence_level": "L3", "label_id": "label/intv-001"},
        )
    )
    service = AuditEvidenceExportService(audit_log=audit_log)

    bundle = service.export(
        EvidenceExportRequest(
            program_id="subsidy-program-2026-q2",
            purpose="quarterly subsidy review",
            requested_by="reviewer-a",
            from_time=NOW - timedelta(days=1),
            to_time=NOW + timedelta(days=1),
            correlation_ids=("corr-audit-export-1",),
            export_scope="tenant=t1;region=north;program=subsidy-program-2026-q2",
            environment="ci",
            build_version="test-build",
            data_classification="restricted",
            sensitive=True,
        ),
        decision_cards=(_ready_card(event.event_id),),
        generated_at=NOW,
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
    export_events = [
        item
        for item in audit_log.list_events(correlation_id="corr-audit-export-1")
        if item.event_type == "audit.evidence_export.v1"
    ]
    assert export_events[0].metadata["bundle_checksum"] == bundle.bundle_checksum


def test_audit_evidence_export_api_uses_platform_audit_log() -> None:
    audit_log = InMemoryAuditLog()
    event = audit_log.record(
        AuditEvent(
            event_type="learninghub.model_release.v1",
            actor="ml-owner",
            action="release",
            resource="model/forecast_revenue_interval:1.1.0",
            outcome="approved",
            correlation_id="corr-api-export-1",
            occurred_at=NOW,
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
            "from_time": (NOW - timedelta(hours=1)).isoformat(),
            "to_time": (NOW + timedelta(hours=1)).isoformat(),
            "correlation_ids": ["corr-api-export-1"],
            "export_scope": "tenant=t1;model=forecast_revenue_interval",
            "environment": "ci",
            "build_version": "test-build",
            "data_classification": "restricted",
            "sensitive": True,
            "decision_cards": [
                {
                    "decision_id": "decision-model-release-001",
                    "decision_type": "MODEL_RELEASE",
                    "module": "Learning Hub",
                    "title": "ForecastOps model release",
                    "subject_ref": "model/forecast_revenue_interval:1.1.0",
                    "outcome": "APPROVED",
                    "owner": "model-review-board",
                    "decided_at": NOW.isoformat(),
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
