"""Real SQLite/PostgreSQL PriceOps persistence, restart and exclusive-creation proof."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.priceops import create_priceops_router
from modules.priceops.application import PriceOpsService
from modules.priceops.domain.pricing import PlanAlreadyExistsError
from shared.api.errors import install_error_handlers
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.infrastructure.persistence.repositories import DurablePriceOpsRepository
from tests.integration.test_priceops_command_authority import proposal

HEADERS = {"x-subject-id": "durable-pricer", "x-roles": "pricing_manager", "x-tenant-id": "tenant-a", "x-correlation-id": "corr-durable-command"}


def http_context(engine, *, store_repository=None, brand_repository=None, feature_flag_registry=None):
    repository = DurablePriceOpsRepository(SqliteDocumentStore(engine))
    audit = DurableAuditLog(engine)
    app = FastAPI()
    if feature_flag_registry is not None:
        app.state.feature_flag_registry = feature_flag_registry
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-correlation")
        return await call_next(request)

    app.include_router(create_priceops_router(repository=repository, audit_log=audit,
                                             store_repository=store_repository,
                                             brand_repository=brand_repository,
                                             job_queue=DurableJobQueue(engine, flags=feature_flag_registry),
                                             require_durable_commands=True, runtime_mode="poc"))
    return TestClient(app, headers=HEADERS), repository, audit


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path):
    if request.param == "sqlite":
        path = tmp_path / "priceops-synthetic.sqlite3"
        def open_engine():
            return SqliteEngine(path)
        env = {"PRICEOPS_TEST_BACKEND": "sqlite", "PRICEOPS_TEST_PATH": str(path)}
    else:
        fixture = request.getfixturevalue("intake_blank_db")
        url = fixture.url()
        def open_engine():
            return PostgresEngine(url, validate_schema=False)
        env = {"PRICEOPS_TEST_BACKEND": "postgresql", "PRICEOPS_TEST_DATABASE_URL": url}
    yield open_engine, env


def seed(client, *, plan_proposal=None):
    created = client.post("/priceops/plans", headers={"Idempotency-Key": "durable-create"}, json=plan_proposal or proposal("durable-plan"))
    assert created.status_code == 201, created.text
    for suffix in ("simulate", "optimize"):
        response = client.post(f"/priceops/plans/durable-plan/{suffix}", json={"reason": "synthetic pricing preparation"})
        assert response.status_code == 200, response.text
    scenario = client.post("/priceops/plans/durable-plan/simulate-scenario", headers={"Idempotency-Key": "durable-scenario"}, json={"candidate_prices": {"new-item": 80}, "actor": "forged"})
    assert scenario.status_code == 200, scenario.text
    body = {"actor": "forged", "decision": "scenario_selected", "reason": "authored synthetic decision", "selected_scenario_id": scenario.json()["scenario_id"]}
    writeback = client.post("/priceops/plans/durable-plan/decision-writeback", headers={"Idempotency-Key": "durable-writeback"}, json=body)
    assert writeback.status_code == 200, writeback.text
    return scenario.json(), writeback.json(), body


def test_scenario_decision_audit_and_receipts_survive_reopen(database):
    open_engine, env = database
    engine = open_engine()
    client, repository, audit = http_context(engine)
    scenario, decision, body = seed(client)
    assert decision["actor"] == "durable-pricer"
    engine.close()

    reopened = open_engine()
    try:
        client, repository, audit = http_context(reopened)
        assert repository.get_scenario_simulation(scenario["scenario_id"]).to_dict()["items"] == scenario["items"]
        assert repository.list_decision_writebacks("durable-plan")[0].decision_id == decision["decision_id"]
        assert client.get("/priceops/plans/durable-plan").json()["decision_writebacks"][0]["decision_id"] == decision["decision_id"]
        replay = client.post("/priceops/plans/durable-plan/decision-writeback", headers={"Idempotency-Key": "durable-writeback"}, json=body)
        assert replay.status_code == 200 and replay.json()["decision_id"] == decision["decision_id"]
        assert replay.json()["audit_event_id"] == decision["audit_event_id"]
        assert len(repository.list_decision_writebacks("durable-plan")) == 1
        events = [event for event in audit.list_events() if event.event_type == "priceops.decision_written_back.v1"]
        assert len(events) == 1 and events[0].event_id == decision["audit_event_id"]
        assert events[0].metadata["selected_scenario_id"] == scenario["scenario_id"]
        assert audit.verify_chain().ok
    finally:
        reopened.close()

    code = """
import json,os
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.repositories import DurablePriceOpsRepository
from shared.infrastructure.persistence.audit_log import DurableAuditLog
engine=SqliteEngine(os.environ['PRICEOPS_TEST_PATH']) if os.environ['PRICEOPS_TEST_BACKEND']=='sqlite' else PostgresEngine(os.environ['PRICEOPS_TEST_DATABASE_URL'],validate_schema=False)
repo=DurablePriceOpsRepository(SqliteDocumentStore(engine));decisions=repo.list_decision_writebacks('durable-plan')
scenario=repo.get_scenario_simulation(decisions[0].selected_scenario_id)
events=[e for e in DurableAuditLog(engine).list_events() if e.event_type=='priceops.decision_written_back.v1']
print(json.dumps({'decision_id':decisions[0].decision_id,'scenario_id':scenario.scenario_id,'decision_count':len(decisions),'audit_event_id':events[0].event_id}))
engine.close()
"""
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, **env}, check=True, capture_output=True, text=True)
    actual = json.loads(result.stdout)
    assert actual == {"decision_id": decision["decision_id"], "scenario_id": scenario["scenario_id"], "decision_count": 1, "audit_event_id": decision["audit_event_id"]}


def test_exclusive_creation_is_atomic_across_independent_connections(database):
    open_engine, env = database
    initial = open_engine()
    try:
        client, repository, audit = http_context(initial)
        seed(client)
        original = repository.get_plan("durable-plan")
    finally:
        initial.close()

    def attempt(index):
        engine = open_engine()
        try:
            repository = DurablePriceOpsRepository(SqliteDocumentStore(engine))
            service = PriceOpsService(repository=repository, runtime_mode="poc")
            try:
                return service.create_plan(tenant_id="tenant-a", items=original.items,
                                           correlation_id=f"race-{index}", plan_id="concurrent-plan").plan_id
            except PlanAlreadyExistsError:
                return "conflict"
        finally:
            engine.close()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert results.count("concurrent-plan") == 1 and results.count("conflict") == 7
    reopened = open_engine()
    try:
        repository = DurablePriceOpsRepository(SqliteDocumentStore(reopened))
        assert len([plan for plan in repository.list_plans() if plan.plan_id == "concurrent-plan"]) == 1
        with pytest.raises(PlanAlreadyExistsError):
            PriceOpsService(repository=repository, runtime_mode="poc").create_plan(
                tenant_id="foreign-tenant", items=original.items, correlation_id="overwrite", plan_id="durable-plan")
        assert repository.get_plan("durable-plan").to_dict() == original.to_dict()
    finally:
        reopened.close()


def test_sqlite_bootstrap_rolls_back_and_restores_connection_settings(tmp_path, monkeypatch):
    import sqlite3

    from shared.infrastructure.persistence import engine as engine_module

    path = tmp_path / "bootstrap-rollback.sqlite3"
    engine = SqliteEngine(path)
    assert engine.query_one("PRAGMA foreign_keys")["foreign_keys"] == 1
    assert engine.query_one("PRAGMA busy_timeout")["timeout"] == 5000
    engine.execute("CREATE TABLE bootstrap_probe (value TEXT NOT NULL)")
    engine.execute("INSERT INTO bootstrap_probe(value) VALUES (?)", ("original",))
    engine.close()
    (tmp_path / "broken.sql").write_text("UPDATE bootstrap_probe SET value = 'corrupt';\nTHIS IS INVALID SQL;\n")
    with monkeypatch.context() as patch:
        patch.setattr(engine_module, "_MIGRATIONS_DIR", tmp_path)
        patch.setattr(engine_module, "_SCHEMA_FILES", ("broken.sql",))
        with pytest.raises(sqlite3.OperationalError):
            SqliteEngine(path)
    reopened = SqliteEngine(path)
    try:
        assert reopened.query_one("SELECT value FROM bootstrap_probe")["value"] == "original"
        assert reopened.query_one("PRAGMA foreign_keys")["foreign_keys"] == 1
        assert reopened.query_one("PRAGMA busy_timeout")["timeout"] == 5000
    finally:
        reopened.close()



def test_canonical_ownership_and_creator_survive_reopen(database, request):
    from dataclasses import replace

    from shared.domain.models import AddressLocation, Brand, Store, Tenant
    from shared.infrastructure.persistence.repositories import (
        DurableAddressLocationRepository,
        DurableBrandRepository,
        DurableStoreRepository,
        DurableTenantRepository,
    )

    open_engine, env = database
    if env["PRICEOPS_TEST_BACKEND"] == "postgresql":
        from tests.integration.test_postgresql_persistence import _provision_canonical_schema
        _provision_canonical_schema(request.getfixturevalue("intake_blank_db"))
    tenant_id = "11111111-1111-4111-8111-111111111111"
    brand_id = "22222222-2222-4222-8222-222222222222"
    store_id = "33333333-3333-4333-8333-333333333333"
    address_id = "44444444-4444-4444-8444-444444444444"
    new_brand_id = "55555555-5555-4555-8555-555555555555"
    engine = open_engine()
    DurableTenantRepository(engine).save_tenant(Tenant(tenant_id=tenant_id, tenant_name="Synthetic pricing tenant"))
    stores = DurableStoreRepository(engine)
    brands = DurableBrandRepository(engine)
    brands.save_brand(Brand(brand_id=brand_id, tenant_id=tenant_id, brand_code="synthetic-pricing", brand_name="Synthetic brand"))
    DurableAddressLocationRepository(engine).save_address(AddressLocation(address_id=address_id, tenant_id=tenant_id, raw_address="Synthetic address", normalized_address="Synthetic address"))
    stores.save_store(Store(store_id=store_id, tenant_id=tenant_id, brand_id=brand_id, address_id=address_id, store_name="Synthetic store", region_code="north"))
    client, repository, audit = http_context(engine, store_repository=stores, brand_repository=brands)
    client.headers.update({"x-tenant-id": tenant_id})
    plan_proposal = proposal("durable-plan", store_id=store_id, brand_id=brand_id)
    plan_proposal["tenant_id"] = tenant_id
    seed(client, plan_proposal=plan_proposal)
    assert repository.get_plan("durable-plan").created_by == "durable-pricer"
    assert client.post("/priceops/plans/durable-plan/submit", json={"reason": "independent review"}).status_code == 200
    engine.close()
    reopened = open_engine()
    try:
        stores = DurableStoreRepository(reopened)
        brands = DurableBrandRepository(reopened)
        client, repository, audit = http_context(reopened, store_repository=stores, brand_repository=brands)
        client.headers.update({"x-tenant-id": tenant_id})
        own = client.get("/priceops/plans/durable-plan/comparison").json()
        assert own["created_by"] == "durable-pricer" and own["is_approvable"] is False
        body = {"actor_id": "forged-independent-reviewer", "decision": "approved", "reason": "authored independent approval"}
        assert client.post("/priceops/plans/durable-plan/approve", json=body).status_code == 403
        assert repository.list_approvals("durable-plan") == []
        reviewer = {"x-subject-id": "independent-durable-pricer", "Idempotency-Key": "independent-sql-approval"}
        allowed = client.post("/priceops/plans/durable-plan/approve", headers=reviewer, json=body)
        assert allowed.status_code == 200, allowed.text
        assert repository.list_approvals("durable-plan")[0].actor_id == "independent-durable-pricer"
        brands.save_brand(Brand(brand_id=new_brand_id, tenant_id=tenant_id, brand_code="new-synthetic-brand", brand_name="New synthetic brand"))
        stores.save_store(replace(stores.get_store(store_id), brand_id=new_brand_id))
        assert client.get("/priceops/plans/durable-plan").status_code == 404
        assert client.post("/priceops/plans/durable-plan/approve", headers=reviewer, json=body).status_code == 404
        assert len(repository.list_approvals("durable-plan")) == 1
    finally:
        reopened.close()
