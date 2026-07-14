from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle


def _headers(key: str, correlation_id: str = "corr-store-ops") -> dict[str, str]:
    return {
        "Idempotency-Key": key,
        "X-Correlation-ID": correlation_id,
        "X-Subject-Id": "operator-opsLead",
        "X-Roles": "operations_manager",
        "X-Tenant-Id": "tenant-a",
    }


def _client(db_path: str | None = None) -> tuple[TestClient, object | None]:
    if db_path is None:
        return TestClient(create_app(external_provider_validation=lambda: None)), None
    bundle = _durable_bundle(db_path)
    return (
        TestClient(create_app(persistence=bundle, external_provider_validation=lambda: None)),
        bundle,
    )


def test_store_ops_four_light_filter_returns_deterministic_queue() -> None:
    client, _ = _client()

    response = client.get(
        "/api/v1/operator/store-ops/issues",
        params={"light": "operations", "lightStatus": "red"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filters"]["light"] == "operations"
    assert body["filters"]["lightStatus"] == "red"
    assert [issue["id"] for issue in body["issues"]] == ["ISS-1024"]
    operations = next(item for item in body["fourLightSummary"] if item["dimension"] == "operations")
    assert operations["counts"]["red"] == 1
    assert operations["issueCounts"]["red"] == 1


def test_store_ops_invalid_transition_409_and_idempotency_dedupes_audit() -> None:
    client, _ = _client()

    invalid = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/outcome",
        json={"outcome": "effective", "closeIssue": True},
        headers=_headers("idem-invalid"),
    )
    assert invalid.status_code == 409

    payload = {"severity": "critical", "decision": "accept", "notes": "Accepted for ownership."}
    first = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/triage",
        json=payload,
        headers=_headers("idem-triage"),
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["issue"]["status"] == "triaged"
    audit_count = len(first_body["auditEvents"])

    replay = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/triage",
        json=payload,
        headers=_headers("idem-triage"),
    )
    assert replay.status_code == 200
    replay_body = replay.json()
    assert replay_body["idempotentReplay"] is True
    assert len(replay_body["auditEvents"]) == audit_count


def test_store_ops_issue_1024_lifecycle_and_camera_purpose_survive_restart(tmp_path) -> None:
    db_path = str(tmp_path / "store_ops.sqlite3")
    client, bundle = _client(db_path)
    assert bundle is not None
    try:
        locked = client.get("/api/v1/operator/store-ops/issues/ISS-1024/evidence")
        assert locked.status_code == 200
        camera = next(item for item in locked.json()["evidence"] if item["kind"] == "camera")
        assert camera["lockedReason"]

        denied = client.post(
            "/api/v1/operator/store-ops/issues/ISS-1024/camera-purpose",
            json={
                "purpose": "marketing curiosity",
                "privacyAcknowledged": True,
                "retentionHours": 24,
            },
            headers=_headers("idem-camera-denied"),
        )
        assert denied.status_code == 422

        allowed = client.post(
            "/api/v1/operator/store-ops/issues/ISS-1024/camera-purpose",
            json={
                "purpose": "payment incident quality audit",
                "cameraLocation": "台北信義 A11 counter camera",
                "timeWindow": "2026-07-04T20:00Z/2026-07-04T21:00Z",
                "retentionHours": 24,
                "privacyAcknowledged": True,
                "auditNote": "Purpose-limited review for ISS-1024.",
            },
            headers=_headers("idem-camera-allowed"),
        )
        assert allowed.status_code == 200
        assert "lockedReason" not in allowed.json()["evidenceItem"]

        steps = [
            ("triage", {"severity": "critical", "decision": "accept", "notes": "Accepted."}, "triaged"),
            (
                "assign",
                {
                    "ownerRoleId": "facilitiesLead",
                    "ownerName": "工務主任",
                    "slaDueAt": "2026-07-05T13:00:00.000Z",
                },
                "assigned",
            ),
            (
                "actions",
                {
                    "actionType": "cleaningCheck",
                    "title": "Clean counter lane",
                    "instructions": "Close the hygiene loop.",
                    "checklistItems": ["clean lane", "attach photo"],
                    "requiresApproval": False,
                },
                "inprogress",
            ),
            (
                "field-report",
                {
                    "reportedBy": "工務主任",
                    "observedAt": "2026-07-05T09:30:00.000Z",
                    "summary": "Counter lane cleaned and payment queue cleared.",
                    "checklistStatus": "complete",
                    "attachmentNames": ["field-photo.jpg"],
                },
                "observing",
            ),
            (
                "outcome",
                {
                    "outcome": "effective",
                    "impactSummary": "Negative review cluster stopped after field action.",
                    "evidenceSummary": "Payment queue and CS case trend returned to baseline.",
                    "closeIssue": True,
                },
                "closed",
            ),
        ]
        for index, (action, payload, expected_status) in enumerate(steps, start=1):
            response = client.post(
                f"/api/v1/operator/store-ops/issues/ISS-1024/{action}",
                json=payload,
                headers=_headers(f"idem-lifecycle-{index}", f"corr-lifecycle-{index}"),
            )
            assert response.status_code == 200
            assert response.json()["issue"]["status"] == expected_status
    finally:
        bundle.engine.close()

    reopened_client, reopened_bundle = _client(db_path)
    assert reopened_bundle is not None
    try:
        issue = reopened_client.get("/api/v1/operator/store-ops/issues/ISS-1024").json()["issue"]
        assert issue["status"] == "closed"

        evidence = reopened_client.get("/api/v1/operator/store-ops/issues/ISS-1024/evidence").json()["evidence"]
        camera = next(item for item in evidence if item["kind"] == "camera")
        assert camera["purpose"] == "payment incident quality audit"
        assert "lockedReason" not in camera

        audit = reopened_client.get("/api/v1/operator/store-ops/issues").json()["auditEvents"]
        assert any(event["action"] == "evidence.camera_purpose.recorded" for event in audit)
        assert any(event["action"] == "issue.outcome" for event in audit)
    finally:
        reopened_bundle.engine.close()
