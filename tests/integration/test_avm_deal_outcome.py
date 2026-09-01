"""Integration tests for AVM deal outcome recovery, schema migration, and API routes."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from apps.api.app.routes.avm import create_avm_router
from modules.avm.application.valuation import AVMService
from modules.avm.domain.valuation import ValuationInput
from modules.avm.infrastructure.repositories import InMemoryAVMRepository
from shared.audit import InMemoryAuditLog
from shared.auth.identity import Principal, Role


def test_avm_deal_outcomes_migration_contract() -> None:
    migration_path = Path("infra/db/migrations/000014_avm_deal_outcomes.sql").resolve()
    assert migration_path.exists(), "000014_avm_deal_outcomes.sql must exist"
    sql = migration_path.read_text(encoding="utf-8")

    assert "CREATE SCHEMA IF NOT EXISTS avm;" in sql
    assert "CREATE TABLE IF NOT EXISTS avm.deal_outcomes" in sql
    assert "valuation_id VARCHAR(100) NOT NULL" in sql
    assert "store_id VARCHAR(100) NOT NULL" in sql
    assert "settlement_price NUMERIC(16, 2)" in sql
    assert "settlement_date DATE" in sql
    assert "no_deal_reason_code VARCHAR(50)" in sql
    assert "deal_terms JSONB NOT NULL" in sql
    assert "source_authority VARCHAR(100) NOT NULL" in sql
    for reason in ("PRICE_GAP", "CONDITION", "FINANCING", "WITHDRAWN_BY_OWNER", "OTHER"):
        assert reason in sql

    # Verify linear Alembic migration chain
    config = Config("infra/db/migrations/alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, f"migration chain must stay linear, found heads: {heads}"
    rev6 = script.get_revision("0006")
    assert rev6 is not None
    assert rev6.down_revision == "0005"


def test_avm_router_deal_outcomes_and_calibration_endpoints() -> None:
    from fastapi import FastAPI, Request

    repo = InMemoryAVMRepository()
    audit_log = InMemoryAuditLog()
    service = AVMService(repository=repo)

    # Seed a valuation case and valuation report
    val_input = ValuationInput(
        store_id="store-101",
        gm_ttm=2_000_000.0,
        forecast_gm_next_12m=2_200_000.0,
        asset_book_value=3_000_000.0,
        equipment_fair_value=1_000_000.0,
    )
    case = service.create_case(val_input, created_by="operator-1", correlation_id="corr-1")
    report = service.value(case.case_id, actor="operator-1", correlation_id="corr-2")

    app = FastAPI()

    # Middleware to inject operator_principal and correlation_id for tests
    current_principal: Principal = Principal(
        subject_id="usr-fin-001",
        roles=frozenset({Role.FINANCE_LEGAL}),
        authenticated=True,
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        request.state.operator_principal = current_principal
        request.state.correlation_id = "test-corr-123"
        return await call_next(request)

    router = create_avm_router(
        repository=repo,
        audit_log=audit_log,
        require_durable_commands=False,
    )
    app.include_router(router)
    client = TestClient(app)

    fin_headers = {"x-subject-id": "usr-fin-001", "x-roles": "finance_legal"}
    aud_headers = {"x-subject-id": "usr-aud-001", "x-roles": "auditor"}

    # 1. POST /avm/deal-outcomes as FINANCE_LEGAL
    deal_payload = {
        "valuation_id": report.report_id,
        "store_id": "store-101",
        "sold": True,
        "settlement_price": report.fair_price.p50,
        "settlement_date": "2026-08-25",
        "duration_days": 28.0,
        "deal_terms": {"escrow": True},
        "source_authority": "official_dealroom",
    }
    resp = client.post("/avm/deal-outcomes", json=deal_payload, headers=fin_headers)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["sold"] is True
    assert created["settlement_price"] == report.fair_price.p50
    assert "outcome_id" in created

    # 2. GET /avm/deal-outcomes as FINANCE_LEGAL (unmasked settlement_price)
    resp_get = client.get("/avm/deal-outcomes", headers=fin_headers)
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["count"] == 1
    assert data["finance_authorized"] is True
    assert data["items"][0]["settlement_price"] == report.fair_price.p50

    # 3. GET /avm/deal-outcomes as AUDITOR (masked settlement_price)
    current_principal = Principal(
        subject_id="usr-aud-001",
        roles=frozenset({Role.AUDITOR}),
        authenticated=True,
    )
    resp_masked = client.get("/avm/deal-outcomes", headers=aud_headers)
    assert resp_masked.status_code == 200
    data_masked = resp_masked.json()
    assert data_masked["finance_authorized"] is False
    assert data_masked["items"][0]["settlement_price"] == "[REDACTED_CONFIDENTIAL_VALUE]"

    # 4. POST /avm/deal-outcomes/export as FINANCE_LEGAL
    current_principal = Principal(
        subject_id="usr-fin-001",
        roles=frozenset({Role.FINANCE_LEGAL}),
        authenticated=True,
    )
    export_payload = {
        "actor": "usr-fin-001",
        "role": "finance_legal",
        "reason": "Quarterly portfolio audit",
    }
    resp_export = client.post("/avm/deal-outcomes/export", json=export_payload, headers=fin_headers)
    assert resp_export.status_code == 200
    export_data = resp_export.json()
    assert export_data["export_metadata"]["decision"] == "PERMIT"
    assert export_data["count"] == 1

    # 5. POST /avm/calibration as FINANCE_LEGAL
    resp_calib = client.post("/avm/calibration", headers=fin_headers)
    assert resp_calib.status_code == 200
    calib_data = resp_calib.json()
    assert calib_data["total_outcomes"] == 1
    assert calib_data["sold_count"] == 1
    assert calib_data["p10_p90_coverage_rate"] == 1.0
    assert calib_data["is_coverage_target_met"] is True
