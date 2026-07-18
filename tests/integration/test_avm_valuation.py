from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.avm import (
    AVM_FEATURE_VERSION,
    InMemoryAVMRepository,
    ValuationCaseStatus,
    build_valuation_view,
    run_avm_batch_valuation,
)
from modules.avm.application import AVMService
from shared.infrastructure.persistence import build_persistence
from tests.integration._authz import AVM_HEADERS


def _valuation_payload() -> dict:
    return {
        "store_id": "store-red-001",
        "gm_ttm": 1_200_000,
        "forecast_gm_next_12m": 980_000,
        "asset_book_value": 520_000,
        "equipment_fair_value": 180_000,
        "lease_liability": 90_000,
        "working_capital": 70_000,
        "comparable_multiples": [2.1, 2.4, 2.8],
        "liquidity_discount": 0.12,
        "quality_score": 0.93,
        "source_snapshot_ids": ["forecast-20260627", "asset-ledger-202606"],
        "prediction_origin_time": "2026-06-27T09:00:00+00:00",
    }


def test_valuation_view_and_worker_emit_lenses_and_price_separation() -> None:
    valuation_view = build_valuation_view(_valuation_payload())
    assert valuation_view.to_dict()["feature_version"] == AVM_FEATURE_VERSION

    result = run_avm_batch_valuation([_valuation_payload()], job_id="avm-job-1")

    assert result.job_id == "avm-job-1"
    assert result.status == "succeeded"
    report = result.reports[0].to_dict()
    assert {lens["lens"] for lens in report["lenses"]} == {
        "income",
        "asset",
        "market",
        "blended",
    }
    assert report["fair_price"]["p10"] <= report["fair_price"]["p50"] <= report["fair_price"]["p90"]
    assert report["lens_values"]["blended"]["p50"] == report["fair_price"]["p50"]
    assert report["lens_values"]["market"]["evidence"]["comparable_multiples"] == [2.1, 2.4, 2.8]
    assert report["reserve_price"] < report["fair_price"]["p50"]
    assert report["asking_price"] > report["fair_price"]["p50"]
    assert report["finance_approval"] is None
    assert result.datarooms == ()


def test_finance_approval_state_gates_versions_and_dataroom_export() -> None:
    service = AVMService(repository=InMemoryAVMRepository())
    case = service.create_case(
        _valuation_payload(), created_by="ops-lead", correlation_id="corr-avm-domain"
    )

    with pytest.raises(ValueError, match="expected one of: REVIEW_REQUIRED"):
        service.approve_finance(
            case.case_id,
            actor="finance-a",
            reason="cannot approve before valuation",
            correlation_id="corr-avm-domain",
        )

    first_report = service.value(
        case.case_id,
        actor="avm-score-worker",
        correlation_id="corr-avm-domain",
    )
    report = service.value(
        case.case_id,
        actor="avm-score-worker",
        correlation_id="corr-avm-domain-v2",
    )
    assert first_report.valuation_version == 1
    assert report.valuation_version == 2
    assert [item.valuation_version for item in service.report_history(case.case_id)] == [1, 2]

    with pytest.raises(ValueError, match="cannot build data room"):
        service.build_dataroom(
            case.case_id,
            actor="deal-room-a",
            correlation_id="corr-avm-domain",
        )

    with pytest.raises(ValueError, match="creator cannot approve"):
        service.approve_finance(
            case.case_id,
            actor="ops-lead",
            reason="self approval should be blocked",
            correlation_id="corr-avm-domain",
        )

    with pytest.raises(ValueError, match="requires a reason"):
        service.approve_finance(
            case.case_id,
            actor="finance-a",
            reason="",
            correlation_id="corr-avm-domain",
        )

    approved = service.approve_finance(
        case.case_id,
        actor="finance-a",
        reason="reserve price aligns with liquidation floor",
        reserve_price=report.reserve_price,
        correlation_id="corr-avm-domain",
    )
    assert approved.finance_approval is not None
    assert (
        approved.finance_approval.decision_reason == "reserve price aligns with liquidation floor"
    )
    assert approved.finance_approval.correlation_id == "corr-avm-domain"
    assert service.get_case(case.case_id).status is ValuationCaseStatus.APPROVED

    with pytest.raises(ValueError, match="cannot export data room"):
        service.export_dataroom(
            case.case_id,
            actor="deal-room-a",
            reason="premature export",
            correlation_id="corr-avm-domain",
        )

    dataroom = service.build_dataroom(
        case.case_id,
        actor="deal-room-a",
        correlation_id="corr-avm-domain",
    )
    assert dataroom.completeness == 1.0
    assert dataroom.is_complete is True
    assert dataroom.valuation_card["finance_approval"]["decision_id"].startswith("avm-decision-")

    exported = service.export_dataroom(
        case.case_id,
        actor="deal-room-a",
        reason="finance diligence package",
        correlation_id="corr-avm-domain",
    )
    assert exported.export_audit[0]["reason"] == "finance diligence package"


def test_avm_api_runs_e2e_valuation_dataroom_export_and_audit() -> None:
    client = TestClient(create_app(), headers=AVM_HEADERS)
    payload = {**_valuation_payload(), "created_by": "ops-lead"}

    created = client.post(
        "/avm/cases",
        json=payload,
        headers={"x-correlation-id": "corr-avm-1", "Idempotency-Key": "avm-case-key-1"},
    )
    assert created.status_code == 201
    case_body = created.json()
    assert case_body["created"] is True
    assert case_body["correlation_id"] == "corr-avm-1"
    case_id = case_body["case_id"]

    replay = client.post(
        "/avm/cases",
        json=payload,
        headers={"x-correlation-id": "corr-avm-1", "Idempotency-Key": "avm-case-key-1"},
    )
    assert replay.json()["created"] is False
    assert replay.json()["case_id"] == case_id

    valued = client.post(
        f"/avm/cases/{case_id}/value",
        json={"actor": "avm-score-worker"},
        headers={"x-correlation-id": "corr-avm-1"},
    )
    assert valued.status_code == 200
    report = valued.json()
    assert {lens["lens"] for lens in report["lenses"]} == {
        "income",
        "asset",
        "market",
        "blended",
    }
    assert report["fair_price"]["p10"] <= report["fair_price"]["p50"] <= report["fair_price"]["p90"]
    assert report["reserve_price"] != report["asking_price"]
    assert report["lens_values"]["market"]["evidence"]["evidence_status"] == "ready"

    reports = client.get(f"/avm/cases/{case_id}/reports")
    assert reports.status_code == 200
    assert reports.json()["count"] == 1
    assert reports.json()["latest_version"] == 1

    premature_dataroom = client.post(
        f"/avm/cases/{case_id}/dataroom",
        json={"actor": "deal-room-a"},
        headers={"x-correlation-id": "corr-avm-1"},
    )
    assert premature_dataroom.status_code == 422

    rejected = client.post(
        f"/avm/cases/{case_id}/finance-approval",
        json={"actor": "finance-a", "reason": ""},
        headers={"x-correlation-id": "corr-avm-1"},
    )
    assert rejected.status_code == 422

    approved = client.post(
        f"/avm/cases/{case_id}/finance-approval",
        json={
            "actor": "finance-a",
            "reason": "reserve reviewed against asset floor and market comps",
        },
        headers={"x-correlation-id": "corr-avm-1"},
    )
    assert approved.status_code == 200
    approval_body = approved.json()
    assert approval_body["finance_approval"]["decision_reason"].startswith("reserve reviewed")
    assert approval_body["finance_approval"]["correlation_id"] == "corr-avm-1"

    premature_export = client.post(
        f"/avm/cases/{case_id}/dataroom/export",
        json={"actor": "deal-room-a", "reason": "export before build"},
        headers={"x-correlation-id": "corr-avm-1"},
    )
    assert premature_export.status_code == 422

    dataroom = client.post(
        f"/avm/cases/{case_id}/dataroom",
        json={"actor": "deal-room-a"},
        headers={"x-correlation-id": "corr-avm-1"},
    )
    assert dataroom.status_code == 200
    dataroom_body = dataroom.json()
    assert len(dataroom_body["checklist"]) == 5
    assert dataroom_body["completeness"] == 1.0
    assert dataroom_body["is_complete"] is True

    fetched_dataroom = client.get(f"/avm/cases/{case_id}/dataroom")
    assert fetched_dataroom.status_code == 200
    assert fetched_dataroom.json()["dataroom_id"] == dataroom_body["dataroom_id"]

    exported = client.post(
        f"/avm/cases/{case_id}/dataroom/export",
        json={"actor": "deal-room-a", "reason": "finance diligence package"},
        headers={"x-correlation-id": "corr-avm-1"},
    )
    assert exported.status_code == 200
    assert exported.json()["export_audit"][0]["reason"] == "finance diligence package"

    audit = client.get("/audit/events", params={"correlation_id": "corr-avm-1"})
    event_types = {event["event_type"] for event in audit.json()["events"]}
    assert {
        "avm.case_created.v1",
        "avm.valued.v1",
        "avm.finance_approved.v1",
        "avm.dataroom_ready.v1",
        "avm.dataroom_exported.v1",
    }.issubset(event_types)


def test_avm_durable_loop_survives_restart(tmp_path) -> None:
    db_path = tmp_path / "avm-durable.sqlite3"
    correlation_id = "corr-avm-durable"
    bundle = build_persistence(mode="durable", db_path=db_path)
    try:
        client = TestClient(create_app(persistence=bundle), headers=AVM_HEADERS)
        payload = {**_valuation_payload(), "created_by": "ops-lead"}
        created = client.post(
            "/avm/cases",
            json=payload,
            headers={"x-correlation-id": correlation_id, "Idempotency-Key": "avm-durable-1"},
        )
        assert created.status_code == 201
        case_id = created.json()["case_id"]

        first = client.post(
            f"/avm/cases/{case_id}/value",
            json={"actor": "avm-score-worker"},
            headers={"x-correlation-id": correlation_id},
        )
        assert first.status_code == 200
        second = client.post(
            f"/avm/cases/{case_id}/value",
            json={"actor": "avm-score-worker"},
            headers={"x-correlation-id": correlation_id},
        )
        assert second.status_code == 200
        assert second.json()["valuation_version"] == 2

        approved = client.post(
            f"/avm/cases/{case_id}/finance-approval",
            json={
                "actor": "finance-a",
                "reason": "approve versioned valuation for durable data room",
            },
            headers={"x-correlation-id": correlation_id},
        )
        assert approved.status_code == 200
        dataroom = client.post(
            f"/avm/cases/{case_id}/dataroom",
            json={"actor": "deal-room-a"},
            headers={"x-correlation-id": correlation_id},
        )
        assert dataroom.status_code == 200
        exported = client.post(
            f"/avm/cases/{case_id}/dataroom/export",
            json={"actor": "deal-room-a", "reason": "durable export audit"},
            headers={"x-correlation-id": correlation_id},
        )
        assert exported.status_code == 200
    finally:
        bundle.engine.close()

    reopened = build_persistence(mode="durable", db_path=db_path)
    try:
        client = TestClient(create_app(persistence=reopened), headers=AVM_HEADERS)
        case = client.get(f"/avm/cases/{case_id}")
        assert case.status_code == 200
        assert case.json()["status"] == "DATAROOM_READY"

        reports = client.get(f"/avm/cases/{case_id}/reports")
        assert reports.status_code == 200
        reports_body = reports.json()
        assert reports_body["count"] == 2
        assert reports_body["latest_version"] == 2
        assert reports_body["items"][-1]["finance_approval"]["decision_reason"].startswith(
            "approve versioned valuation"
        )

        dataroom = client.get(f"/avm/cases/{case_id}/dataroom")
        assert dataroom.status_code == 200
        assert dataroom.json()["completeness"] == 1.0
        assert dataroom.json()["export_audit"][0]["reason"] == "durable export audit"

        audit = client.get("/audit/events", params={"correlation_id": correlation_id})
        event_types = {event["event_type"] for event in audit.json()["events"]}
        assert {
            "avm.valued.v1",
            "avm.finance_approved.v1",
            "avm.dataroom_ready.v1",
            "avm.dataroom_exported.v1",
        }.issubset(event_types)
    finally:
        reopened.engine.close()
