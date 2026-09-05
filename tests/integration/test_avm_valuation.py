from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.avm import (
    AVM_FEATURE_VERSION,
    LEGACY_QUALITY_DISPOSITION,
    LEGACY_UNKNOWN_QUALITY_STATUS,
    InMemoryAVMRepository,
    ValuationCaseStatus,
    build_valuation_view,
    run_avm_batch_valuation,
)
from modules.avm.application import AVMService
from shared.infrastructure.persistence import build_persistence
from tests.integration._authz import AVM_HEADERS


def _as_pre_status_payload(item):
    """Strip the quality status fields the way a pre-nullability pickle would.

    ``__init__`` always writes them, so removing the keys from the instance
    dict reproduces exactly what unpickling a record stored by the previous
    release yields, without pretending a freshly built object is legacy.
    """

    for field_name in ("quality_score_status", "quality_disposition"):
        if field_name in item.__dict__:
            object.__delattr__(item, field_name)
    return item


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


@pytest.mark.parametrize("quality_score", ["omitted", None])
def test_unmeasured_quality_is_retained_but_cannot_produce_a_valuation_card(
    quality_score: str | None,
) -> None:
    payload = _valuation_payload()
    if quality_score == "omitted":
        payload.pop("quality_score")
    else:
        payload["quality_score"] = quality_score

    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository)
    case = service.create_case(payload, created_by="ops-lead", correlation_id="corr-missing-quality")

    assert case.valuation_input.quality_score is None
    with pytest.raises(ValueError, match="quality_score is required.*unmeasured"):
        service.value(case.case_id, actor="avm-score-worker", correlation_id="corr-missing-quality")

    assert repository.get_case(case.case_id).status is ValuationCaseStatus.DATA_READY
    assert repository.latest_report(case.case_id) is None


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
    client = TestClient(
        create_app(),
        headers=AVM_HEADERS,
        backend_options={"use_uvloop": True},
    )
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
        client = TestClient(
            create_app(persistence=bundle),
            headers=AVM_HEADERS,
            backend_options={"use_uvloop": True},
        )
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
        client = TestClient(
            create_app(persistence=reopened),
            headers=AVM_HEADERS,
            backend_options={"use_uvloop": True},
        )
        replay = client.post(
            "/avm/cases",
            json=payload,
            headers={
                "x-correlation-id": correlation_id,
                "Idempotency-Key": "avm-durable-1",
            },
        )
        assert replay.status_code == 201
        assert replay.json()["created"] is False
        assert replay.json()["case_id"] == case_id

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


@pytest.mark.parametrize("quality_score", ["omitted", None])
def test_avm_api_does_not_create_a_card_without_quality_score(
    quality_score: str | None,
) -> None:
    client = TestClient(
        create_app(),
        headers=AVM_HEADERS,
        backend_options={"use_uvloop": True},
    )
    payload = {**_valuation_payload(), "created_by": "ops-lead"}
    if quality_score == "omitted":
        payload.pop("quality_score")
    else:
        payload["quality_score"] = quality_score

    created = client.post(
        "/avm/cases",
        json=payload,
        headers={
            "x-correlation-id": f"corr-avm-missing-quality-{quality_score}",
            "Idempotency-Key": f"avm-missing-quality-{quality_score}",
        },
    )
    assert created.status_code == 201, created.text
    case_body = created.json()
    case_id = case_body["case_id"]
    assert case_body["valuation_input"]["quality_score"] is None

    valued = client.post(
        f"/avm/cases/{case_id}/value",
        json={"actor": "avm-score-worker"},
        headers={"x-correlation-id": f"corr-avm-missing-quality-{quality_score}"},
    )
    assert valued.status_code == 422, valued.text
    assert "quality_score is required" in valued.json()["detail"]

    reports = client.get(f"/avm/cases/{case_id}/reports")
    assert reports.status_code == 200
    assert reports.json()["count"] == 0


def test_legacy_unknown_quality_score_disposition_and_durable_migration(tmp_path) -> None:
    from modules.avm.domain import ValuationCase, ValuationInput, normalize_margin, value_store
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.repositories import DurableAVMRepository

    # 1. Direct legacy input with quality_score=1.0 and quality_score_status='legacy_unknown'
    legacy_input = ValuationInput(
        store_id="store-legacy-1",
        gm_ttm=1_000_000,
        forecast_gm_next_12m=1_000_000,
        asset_book_value=500_000,
        equipment_fair_value=100_000,
        quality_score=1.0,
        quality_score_status="legacy_unknown",
    )
    legacy_case = ValuationCase.create(
        legacy_input,
        created_by="legacy-system",
        correlation_id="corr-legacy-1",
    )
    margin = normalize_margin(legacy_case)
    assert margin.confidence == "low"
    assert "legacy_quality_unknown_discount" in margin.adjustment_reasons
    assert margin.normalized_gm == pytest.approx(1_000_000 * 0.92, abs=1)

    report = value_store(legacy_case, margin)
    assert report.confidence == "low"

    # In contrast, explicit measured score of 1.0 receives high confidence
    measured_input = ValuationInput(
        store_id="store-measured-1",
        gm_ttm=1_000_000,
        forecast_gm_next_12m=1_000_000,
        asset_book_value=500_000,
        equipment_fair_value=100_000,
        quality_score=1.0,
        quality_score_status="measured",
    )
    measured_case = ValuationCase.create(
        measured_input,
        created_by="modern-system",
        correlation_id="corr-measured-1",
    )
    measured_margin = normalize_margin(measured_case)
    assert measured_margin.confidence == "high"
    assert "legacy_quality_unknown_discount" not in measured_margin.adjustment_reasons

    # 2. Durable repository migrations for legacy cases without quality_score_status attribute
    db_path = tmp_path / "legacy-repo.sqlite3"
    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)
    repo = DurableAVMRepository(store)

    # Simulate an opaque legacy pickled case without quality_score_status
    raw_legacy_input = _as_pre_status_payload(
        ValuationInput(
            store_id="store-legacy-durable",
            gm_ttm=800_000,
            forecast_gm_next_12m=800_000,
            asset_book_value=400_000,
            equipment_fair_value=50_000,
            quality_score=1.0,
        )
    )
    raw_case = ValuationCase.create(
        raw_legacy_input,
        created_by="legacy-user",
        correlation_id="corr-legacy-durable",
        case_id="case-legacy-durable-1",
    )
    # Store directly into document store as pickled blob
    store.put(DurableAVMRepository._CASES, raw_case.case_id, raw_case)

    # Retrieve through DurableAVMRepository
    retrieved_case = repo.get_case("case-legacy-durable-1")
    assert retrieved_case is not None
    assert retrieved_case.valuation_input.quality_score == 1.0
    assert retrieved_case.valuation_input.quality_score_status == "legacy_unknown"

    all_cases = repo.list_cases()
    assert len(all_cases) == 1
    assert all_cases[0].valuation_input.quality_score_status == "legacy_unknown"

    # Value the retrieved legacy case via AVMService
    service = AVMService(repository=repo)
    valued_report = service.value(
        "case-legacy-durable-1", actor="worker-1", correlation_id="corr-val-1"
    )
    assert valued_report.confidence == "low"
    assert "legacy_quality_unknown_discount" in valued_report.normalized_margin.adjustment_reasons
    engine.close()


def test_value_downgrades_persisted_high_confidence_legacy_margin() -> None:
    from modules.avm.domain import NormalizedMargin, ValuationInput

    service = AVMService()
    case = service.create_case(
        ValuationInput(
            store_id="store-legacy-persisted-margin",
            gm_ttm=1_000_000,
            forecast_gm_next_12m=1_000_000,
            asset_book_value=500_000,
            equipment_fair_value=100_000,
            quality_score=1.0,
            quality_score_status="legacy_unknown",
        ),
        created_by="legacy-system",
        correlation_id="corr-legacy-persisted-margin",
    )
    service.repository.save_margin(
        NormalizedMargin(
            case_id=case.case_id,
            store_id=case.store_id,
            gm_ttm=1_000_000,
            gm_fwd=1_000_000,
            normalized_gm=1_000_000,
            adjustment_reasons=("weighted_ttm_and_forecast_gm",),
            confidence="high",
        )
    )

    report = service.value(
        case.case_id,
        actor="valuation-worker",
        correlation_id="corr-legacy-persisted-value",
    )

    assert report.confidence == "low"
    assert report.quality_score_status == "legacy_unknown"
    assert report.quality_disposition == "legacy_unknown_downgraded"
    assert report.normalized_margin.confidence == "low"
    assert report.normalized_margin.normalized_gm == pytest.approx(920_000, abs=1)
    assert "legacy_quality_unknown_discount" in report.normalized_margin.adjustment_reasons
    persisted_margin = service.repository.get_margin(case.case_id)
    assert persisted_margin is not None
    assert persisted_margin.confidence == "low"
    assert persisted_margin.normalized_gm == pytest.approx(920_000, abs=1)


def test_legacy_report_and_dataroom_are_downgraded_on_every_read_path(tmp_path) -> None:
    from modules.avm.domain import (
        ApprovalDecision,
        NormalizedMargin,
        ValuationCase,
        ValuationCaseStatus,
        ValuationInput,
        generate_data_room,
        value_store,
    )
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.repositories import DurableAVMRepository

    engine = SqliteEngine(tmp_path / "legacy-report-paths.sqlite3")
    store = SqliteDocumentStore(engine)
    repository = DurableAVMRepository(store)

    def old_case(case_id: str, status: ValuationCaseStatus) -> ValuationCase:
        case = ValuationCase.create(
            _as_pre_status_payload(
                ValuationInput(
                    store_id=f"store-{case_id}",
                    gm_ttm=1_000_000,
                    forecast_gm_next_12m=1_000_000,
                    asset_book_value=500_000,
                    equipment_fair_value=100_000,
                    quality_score=1.0,
                )
            ),
            created_by="legacy-system",
            correlation_id=f"corr-{case_id}",
            case_id=case_id,
        )
        if status is not ValuationCaseStatus.DATA_READY:
            case = case.transition(
                status,
                actor="legacy-system",
                reason="legacy persisted status",
                correlation_id=f"corr-{case_id}",
            )
        store.put(repository._CASES, case.case_id, case)
        return case

    def old_high_confidence_report(case: ValuationCase):
        high_margin = NormalizedMargin(
            case_id=case.case_id,
            store_id=case.store_id,
            gm_ttm=1_000_000,
            gm_fwd=1_000_000,
            normalized_gm=1_000_000,
            adjustment_reasons=("weighted_ttm_and_forecast_gm",),
            confidence="high",
        )
        report = value_store(case, high_margin)
        report = replace(
            report,
            confidence="high",
            normalized_margin=replace(report.normalized_margin, confidence="high"),
            finance_approval=ApprovalDecision(
                decision_id=f"decision-{case.case_id}",
                actor_id="legacy-finance",
                approved_at=case.created_at,
                decision_reason="historical approval",
                reserve_price=report.reserve_price,
                correlation_id=f"corr-{case.case_id}",
            ),
        )
        # Simulate a pickle written before the status/disposition fields existed.
        return _as_pre_status_payload(report)

    review_case = old_case("legacy-review-case", ValuationCaseStatus.REVIEW_REQUIRED)
    review_report = old_high_confidence_report(review_case)
    store.put(
        repository._REPORTS,
        review_report.report_id,
        review_report,
        group_key=review_case.case_id,
        seq=1,
    )

    latest = repository.latest_report(review_case.case_id)
    assert latest is not None
    assert latest.confidence == "low"
    assert latest.normalized_margin.confidence == "low"
    assert latest.quality_score_status == "legacy_unknown"
    assert latest.quality_disposition == "legacy_unknown_downgraded"
    assert latest.finance_approval is None
    assert repository.report_history(review_case.case_id)[0].quality_disposition == (
        "legacy_unknown_downgraded"
    )
    persisted_report = store.get(repository._REPORTS, review_report.report_id)
    assert persisted_report.quality_disposition == "legacy_unknown_downgraded"

    service = AVMService(repository=repository)
    with pytest.raises(ValueError, match="legacy valuation report is downgraded"):
        service.approve_finance(
            review_case.case_id,
            actor="finance-new",
            reason="review old report",
            correlation_id="corr-new-approval",
        )

    dataroom_case = old_case("legacy-dataroom-case", ValuationCaseStatus.DATAROOM_READY)
    dataroom_report = old_high_confidence_report(dataroom_case)
    store.put(
        repository._REPORTS,
        dataroom_report.report_id,
        dataroom_report,
        group_key=dataroom_case.case_id,
        seq=1,
    )
    old_dataroom = _as_pre_status_payload(generate_data_room(dataroom_report))
    store.put(repository._DATAROOMS, dataroom_case.case_id, old_dataroom)

    read_dataroom = service.dataroom(dataroom_case.case_id)
    assert read_dataroom is not None
    assert read_dataroom.quality_disposition == "legacy_unknown_downgraded"
    assert read_dataroom.valuation_card["confidence"] == "low"
    assert read_dataroom.valuation_card["quality_disposition"] == (
        "legacy_unknown_downgraded"
    )
    assert read_dataroom.valuation_card["finance_approval"] is None
    persisted_dataroom = store.get(repository._DATAROOMS, dataroom_case.case_id)
    assert persisted_dataroom.quality_disposition == "legacy_unknown_downgraded"

    with pytest.raises(ValueError, match="legacy valuation report is downgraded"):
        service.build_dataroom(
            dataroom_case.case_id,
            actor="deal-room",
            correlation_id="corr-dataroom-rebuild",
        )
    with pytest.raises(ValueError, match="legacy data room is downgraded"):
        service.export_dataroom(
            dataroom_case.case_id,
            actor="deal-room",
            reason="export old room",
            correlation_id="corr-dataroom-export",
        )
    engine.close()


def test_persisted_legacy_margin_is_downgraded_before_valuation(tmp_path) -> None:
    """A saved pre-nullability margin must not bypass the value entry gate."""

    from modules.avm.domain import NormalizedMargin, ValuationCase, ValuationInput
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.repositories import DurableAVMRepository

    engine = SqliteEngine(tmp_path / "legacy-margin-entry.sqlite3")
    store = SqliteDocumentStore(engine)
    repository = DurableAVMRepository(store)
    case = ValuationCase.create(
        ValuationInput(
            store_id="store-legacy-margin",
            gm_ttm=1_000_000,
            forecast_gm_next_12m=1_000_000,
            asset_book_value=500_000,
            equipment_fair_value=100_000,
            quality_score=1.0,
            quality_score_status=LEGACY_UNKNOWN_QUALITY_STATUS,
        ),
        created_by="legacy-system",
        correlation_id="corr-legacy-margin",
        case_id="case-legacy-margin-1",
    )
    store.put(repository._CASES, case.case_id, case)
    repository.save_margin(
        NormalizedMargin(
            case_id=case.case_id,
            store_id=case.store_id,
            gm_ttm=1_000_000,
            gm_fwd=1_000_000,
            normalized_gm=1_000_000,
            adjustment_reasons=("weighted_ttm_and_forecast_gm",),
            confidence="high",
        )
    )

    service = AVMService(repository=repository)
    report = service.value(
        case.case_id,
        actor="avm-score-worker",
        correlation_id="corr-legacy-margin-value",
    )

    assert report.confidence == "low"
    assert report.normalized_margin.confidence == "low"
    assert report.normalized_margin.normalized_gm == 920_000
    assert "legacy_quality_unknown_discount" in report.normalized_margin.adjustment_reasons
    assert report.quality_disposition == LEGACY_QUALITY_DISPOSITION

    persisted_margin = repository.get_margin(case.case_id)
    assert persisted_margin is not None
    assert persisted_margin.confidence == "low"
    assert "legacy_quality_unknown_discount" in persisted_margin.adjustment_reasons
    persisted_report = store.get(repository._REPORTS, report.report_id)
    assert persisted_report is not None
    assert persisted_report.confidence == "low"
    assert persisted_report.quality_disposition == LEGACY_QUALITY_DISPOSITION
    engine.close()


def test_fresh_input_with_omitted_status_is_measured_not_legacy(tmp_path) -> None:
    """An omitted status on a fresh input must not be read as legacy_unknown.

    Only a record stored before ``quality_score_status`` existed is opaque.  A
    caller that supplies a measured ``quality_score`` and leaves the status out
    is still supplying a measurement and must keep its confidence and price.
    """

    from modules.avm.domain import ValuationCase, ValuationInput, normalize_margin
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.repositories import DurableAVMRepository

    def fresh_input(store_id: str) -> ValuationInput:
        return ValuationInput(
            store_id=store_id,
            gm_ttm=1_000_000,
            forecast_gm_next_12m=1_000_000,
            asset_book_value=500_000,
            equipment_fair_value=100_000,
            quality_score=0.95,
        )

    domain_input = fresh_input("store-fresh-measured")
    assert domain_input.quality_score_status is None
    assert domain_input.is_pre_status_payload is False
    assert domain_input.effective_quality_score_status == "measured"

    domain_case = ValuationCase.create(
        domain_input,
        created_by="operator-1",
        correlation_id="corr-fresh-domain",
    )
    domain_margin = normalize_margin(domain_case)
    assert domain_margin.confidence == "high"
    assert "legacy_quality_unknown_discount" not in domain_margin.adjustment_reasons
    assert domain_margin.normalized_gm == pytest.approx(1_000_000, abs=1)

    # In-memory entry path.
    memory_service = AVMService()
    memory_case = memory_service.create_case(
        fresh_input("store-fresh-memory"),
        created_by="operator-1",
        correlation_id="corr-fresh-memory",
    )
    memory_report = memory_service.value(
        memory_case.case_id,
        actor="operator-1",
        correlation_id="corr-fresh-memory-value",
    )
    assert memory_report.confidence == "high"
    assert memory_report.quality_score_status == "measured"
    assert memory_report.quality_disposition is None
    assert (
        "legacy_quality_unknown_discount"
        not in memory_report.normalized_margin.adjustment_reasons
    )
    memory_latest = memory_service.repository.latest_report(memory_case.case_id)
    assert memory_latest is not None
    assert memory_latest.confidence == "high"
    assert memory_latest.quality_disposition is None

    # Durable entry path: the read-time legacy migration must leave it alone.
    engine = SqliteEngine(tmp_path / "fresh-measured.sqlite3")
    store = SqliteDocumentStore(engine)
    repository = DurableAVMRepository(store)
    durable_service = AVMService(repository=repository)
    durable_case = durable_service.create_case(
        fresh_input("store-fresh-durable"),
        created_by="operator-1",
        correlation_id="corr-fresh-durable",
    )
    reloaded = repository.get_case(durable_case.case_id)
    assert reloaded is not None
    assert reloaded.valuation_input.effective_quality_score_status == "measured"
    assert repository.list_cases()[0].valuation_input.effective_quality_score_status == (
        "measured"
    )

    durable_report = durable_service.value(
        durable_case.case_id,
        actor="operator-1",
        correlation_id="corr-fresh-durable-value",
    )
    assert durable_report.confidence == "high"
    assert durable_report.quality_score_status == "measured"
    assert durable_report.quality_disposition is None
    assert (
        "legacy_quality_unknown_discount"
        not in durable_report.normalized_margin.adjustment_reasons
    )
    durable_latest = repository.latest_report(durable_case.case_id)
    assert durable_latest is not None
    assert durable_latest.confidence == "high"
    assert repository.report_history(durable_case.case_id)[0].confidence == "high"
    engine.close()
