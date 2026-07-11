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


def test_valuation_view_and_worker_emit_three_lenses_and_price_separation() -> None:
    valuation_view = build_valuation_view(_valuation_payload())
    assert valuation_view.to_dict()["feature_version"] == AVM_FEATURE_VERSION

    result = run_avm_batch_valuation([_valuation_payload()], job_id="avm-job-1")

    assert result.job_id == "avm-job-1"
    assert result.status == "succeeded"
    report = result.reports[0].to_dict()
    assert {lens["lens"] for lens in report["lenses"]} == {"income", "asset", "market"}
    assert report["fair_price"]["p10"] <= report["fair_price"]["p50"] <= report["fair_price"]["p90"]
    assert report["reserve_price"] < report["fair_price"]["p50"]
    assert report["asking_price"] > report["fair_price"]["p50"]
    assert report["finance_approval"] is None

    dataroom = result.datarooms[0].to_dict()
    assert {item["document_id"] for item in dataroom["checklist"]} >= {
        "financials",
        "assets",
        "lease",
        "comparables",
        "valuation_card",
    }


def test_finance_approval_requires_reason_and_updates_report() -> None:
    service = AVMService(repository=InMemoryAVMRepository())
    case = service.create_case(
        _valuation_payload(), created_by="ops-lead", correlation_id="corr-avm-domain"
    )
    report = service.value(case.case_id, actor="avm-score-worker", correlation_id="corr-avm-domain")

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
    assert approved.finance_approval.decision_reason == "reserve price aligns with liquidation floor"
    assert service.get_case(case.case_id).status is ValuationCaseStatus.APPROVED


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
    assert {lens["lens"] for lens in report["lenses"]} == {"income", "asset", "market"}
    assert report["fair_price"]["p10"] <= report["fair_price"]["p50"] <= report["fair_price"]["p90"]
    assert report["reserve_price"] != report["asking_price"]

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
    assert approved.json()["finance_approval"]["decision_reason"].startswith("reserve reviewed")

    dataroom = client.post(
        f"/avm/cases/{case_id}/dataroom",
        json={"actor": "deal-room-a"},
        headers={"x-correlation-id": "corr-avm-1"},
    )
    assert dataroom.status_code == 200
    assert len(dataroom.json()["checklist"]) == 5

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
