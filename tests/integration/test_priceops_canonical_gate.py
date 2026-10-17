"""Authoritative workflow approvals and canonical exploration budget/transactions."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.priceops import create_priceops_router
from modules.priceops.application import PriceOpsService
from modules.priceops.domain.exploration import (
    ExplorationBudgetExceededError,
    ExplorationDecision,
    ExplorationNotAuthorizedError,
)
from shared.api.errors import install_error_handlers
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.domain.models import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.infrastructure.persistence.price_exploration import CanonicalPriceExplorationStore
from shared.infrastructure.persistence.repositories import (
    DurableAddressLocationRepository,
    DurableBrandRepository,
    DurablePriceOpsRepository,
    DurableStoreRepository,
    DurableTenantRepository,
)
from tests.integration.test_postgresql_persistence import _provision_canonical_schema
from tests.integration.test_priceops_command_authority import proposal

TENANT = "11111111-1111-4111-8111-111111111111"
BRAND = "22222222-2222-4222-8222-222222222222"
STORE = "33333333-3333-4333-8333-333333333333"
ADDRESS = "44444444-4444-4444-8444-444444444444"
APPROVAL_DECISION = "66666666-6666-4666-8666-666666666666"
APPROVAL = "77777777-7777-4777-8777-777777777777"
GATE = "88888888-8888-4888-8888-888888888888"
POLICY = "price-exploration-v1:" + TENANT
HEADERS = {"x-subject-id": "verified-operator", "x-roles": "pricing_manager", "x-tenant-id": TENANT,
           "x-correlation-id": "synthetic-canonical-exploration"}


def context(engine):
    canonical = CanonicalPriceExplorationStore(engine)
    repo = DurablePriceOpsRepository(SqliteDocumentStore(engine), canonical_exploration=canonical)
    audit = DurableAuditLog(engine)
    app = FastAPI()
    install_error_handlers(app)
    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = HEADERS["x-correlation-id"]
        return await call_next(request)
    app.include_router(create_priceops_router(
        repository=repo, audit_log=audit, job_queue=DurableJobQueue(engine),
        require_durable_commands=True, runtime_mode="poc",
        store_repository=DurableStoreRepository(engine), brand_repository=DurableBrandRepository(engine),
    ))
    return TestClient(app, headers=HEADERS), repo, canonical, audit


@pytest.fixture
def case(intake_blank_db, request, tmp_path):
    _provision_canonical_schema(intake_blank_db)
    url = intake_blank_db.url()
    engine = PostgresEngine(url, validate_schema=False)
    try:
        DurableTenantRepository(engine).save_tenant(Tenant(tenant_id=TENANT, tenant_name="Synthetic gate tenant"))
        DurableBrandRepository(engine).save_brand(Brand(brand_id=BRAND, tenant_id=TENANT, brand_code="synthetic", brand_name="Synthetic brand"))
        DurableAddressLocationRepository(engine).save_address(AddressLocation(
            address_id=ADDRESS, tenant_id=TENANT, raw_address="Synthetic", normalized_address="Synthetic"))
        DurableStoreRepository(engine).save_store(Store(
            store_id=STORE, tenant_id=TENANT, brand_id=BRAND, address_id=ADDRESS,
            store_name="Synthetic store", region_code="north"))
        now = datetime.now(UTC)
        options = getattr(request, "param", 10000)
        limit = options.get("limit", 10000) if isinstance(options, dict) else options
        group = options.get("group") if isinstance(options, dict) else None
        engine.execute(
            "INSERT INTO workflow.decision_policies(policy_version_id,policy_label,policy_id,policy_version,policy_kind,"
            "tenant_id,effective_from,effective_to,owner_role,approved_by,approved_at,input_contract,output_contract,"
            "change_reason,parameters,declared_inputs) VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,ARRAY['price'])",
            (POLICY, "price-exploration-v1", "price-exploration", "v1", "price_exploration", TENANT,
             (now-timedelta(days=2)).isoformat(), "pricing_manager", "synthetic-policy-reviewer",
             (now-timedelta(days=2)).isoformat(), "price-input-v1", "gate-v1", "Synthetic fixture", json.dumps({})),
        )
        engine.execute(
            "INSERT INTO workflow.decisions(decision_id,decision_type,entity_type,entity_id,decision_status,policy_version_id,created_by) "
            "VALUES (?, 'price', 'pricing_exploration_gate', ?, 'approved', ?, 'synthetic-requester')",
            (APPROVAL_DECISION, GATE, POLICY),
        )
        engine.execute(
            "INSERT INTO workflow.approvals(approval_id,decision_id,approver_id,approval_status,approved_at) VALUES (?,?,?,'approved',?)",
            (APPROVAL, APPROVAL_DECISION, "synthetic-independent-reviewer", (now-timedelta(minutes=1)).isoformat()),
        )
        engine.execute(
            "INSERT INTO pricing.exploration_gates(gate_id,tenant_id,scope_brand_id,budget_limit,effective_from,effective_to,"
            "approved_by,approval_id,approval_decision_id,rollback_condition,decision_policy_version_id,scope_store_group) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (GATE, TENANT, BRAND, limit, (now-timedelta(days=1)).isoformat(), (now+timedelta(days=1)).isoformat(),
             "forged-display-text", APPROVAL, APPROVAL_DECISION, "synthetic authored rollback", POLICY, group),
        )
        client, repo, canonical, audit = context(engine)
        audit._worm_sink = LocalAppendOnlyWormSink(tmp_path / "synthetic-canonical-worm")
        body = proposal("canonical-plan", store_id=STORE, brand_id=BRAND)
        body["tenant_id"] = TENANT
        yield SimpleNamespace(engine=engine, url=url, client=client, repo=repo, canonical=canonical,
                              audit=audit, proposal=body, now=now)
    finally:
        engine.close()


def prepare(case, *, items=1):
    body = {**case.proposal, "items": [dict(case.proposal["items"][0], item_id=f"item-{index}") for index in range(items)]}
    response = case.client.post("/priceops/plans", json=body)
    assert response.status_code == 201, response.text
    response = case.client.post("/priceops/plans/canonical-plan/simulate", json={"reason": "authored preparation"})
    assert response.status_code == 200, response.text


def preview(case, *, key="canonical-preview", store=STORE):
    item = dict(case.proposal["items"][0], store_id=store)
    return case.client.post("/priceops/exploration-candidates", headers={"Idempotency-Key": key},
                            json={"tenant_id": TENANT, "scope_brand_id": BRAND, "items": [item], "seed": 42})


def balance(case):
    return float(case.engine.query_one("SELECT budget_consumed FROM pricing.exploration_gates WHERE gate_id = ?", (GATE,))["budget_consumed"])


def ledger(case):
    return case.engine.query("SELECT e.*,d.created_by,d.policy_version_id FROM pricing.exploration_decisions e "
                             "JOIN workflow.decisions d ON d.decision_id=e.decision_id WHERE e.gate_id = ?", (GATE,))


def decision(cost=1):
    return ExplorationDecision(decision_id=str(uuid4()), gate_id=GATE, tenant_id=TENANT, sku_id="synthetic-sku",
                               store_id=STORE, baseline_price=80, explored_price=81, budget_consumed=cost,
                               algorithm="THOMPSON_SAMPLING", created_by="verified-operator")


def test_authority_comes_from_actual_approval_and_pickle_shadow_is_ignored(case):
    actual = case.canonical.get_gate(GATE, TENANT)
    assert actual.approved_by == "synthetic-independent-reviewer" and actual.budget_limit == 10000
    case.repo._store.put(case.repo._GATES, GATE, replace(actual, budget_limit=999999, approved_by="forged"), group_key=TENANT)
    assert case.repo.get_gate(GATE, TENANT).budget_limit == 10000
    assert case.repo.get_gate(GATE, "99999999-9999-4999-8999-999999999999") is None
    with pytest.raises(ExplorationNotAuthorizedError):
        case.repo.save_gate(actual)
    with pytest.raises(ExplorationNotAuthorizedError):
        case.repo.revoke_gate(GATE, TENANT)


def test_http_gate_creation_and_revoke_cannot_establish_or_reset_authority(case):
    before = balance(case)
    for path, body in [("/priceops/exploration-gates", {"tenant_id": TENANT, "approved_by": "forged"}),
                       (f"/priceops/exploration-gates/{GATE}/revoke", {"reason": "authored"})]:
        response = case.client.post(path, json=body)
        assert response.status_code == 405 and response.json()["detail"]["code"] == "WORKFLOW_APPROVAL_REQUIRED"
    assert balance(case) == before and case.canonical.get_gate(GATE, TENANT).revoked_at is None


def test_http_preview_is_scoped_audited_replayable_and_does_not_spend(case):
    response = preview(case)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "shadow" and body["grant"]["approved_by"] == "synthetic-independent-reviewer"
    assert body["audit_event_id"] and body["correlation_id"] == HEADERS["x-correlation-id"]
    assert preview(case).json()["candidates"] == body["candidates"]
    assert preview(case).json()["audit_event_id"] == body["audit_event_id"]
    assert balance(case) == 0 and ledger(case) == []
    events = [e for e in case.audit.list_events() if e.event_type == "priceops.exploration_previewed.v1"]
    assert len(events) == 1 and events[0].metadata["approval_id"] == APPROVAL
    assert preview(case, key="foreign", store="99999999-9999-4999-8999-999999999999").status_code == 403


def test_real_bandit_adoption_creates_canonical_workflow_decisions_and_trigger_budget(case):
    prepare(case, items=2)
    response = case.client.post("/priceops/plans/canonical-plan/optimize", headers={"Idempotency-Key": "canonical-optimize"},
                                json={"actor": "forged", "reason": "authored exploration", "exploration_gate_id": GATE, "exploration_seed": 42})
    assert response.status_code == 200, response.text
    rows = ledger(case)
    metadata = response.json()["solver_metadata"]
    assert len(rows) == 2 and {r["decision_id"] for r in rows} == set(metadata["exploration_decision_ids"])
    assert all(r["created_by"] == "verified-operator" and r["policy_version_id"] == POLICY for r in rows)
    assert balance(case) == sum(float(r["budget_consumed"]) for r in rows) == metadata["exploration_budget_consumed"]
    assert case.repo._store.list_all(case.repo._EXPLORATION_DECISIONS) == []
    before = balance(case)
    replay = case.client.post("/priceops/plans/canonical-plan/optimize", headers={"Idempotency-Key": "canonical-optimize"},
                              json={"actor": "forged", "reason": "authored exploration", "exploration_gate_id": GATE, "exploration_seed": 42})
    assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
    assert balance(case) == before and len(ledger(case)) == 2


def test_direct_service_second_decision_failure_rolls_back_all_canonical_work(case, monkeypatch):
    prepare(case, items=2)
    original = case.canonical.record_exploration_decision
    calls = 0
    def fail(record):
        nonlocal calls
        result = original(record)
        calls += 1
        if calls == 2:
            raise ValueError("synthetic failure after second canonical insert")
        return result
    monkeypatch.setattr(case.canonical, "record_exploration_decision", fail)
    with pytest.raises(ValueError, match="second canonical"):
        PriceOpsService(repository=case.repo, runtime_mode="poc").optimize(
            "canonical-plan", actor="verified-operator", exploration_gate_id=GATE, exploration_seed=42)
    assert balance(case) == 0 and ledger(case) == []
    assert case.repo.get_optimization("canonical-plan") is None
    assert case.repo.get_plan("canonical-plan").status.value == "simulated"


def test_business_audit_failure_rolls_back_canonical_decisions_budget_and_plan(case, monkeypatch):
    prepare(case)
    original = case.audit.record
    def fail(event):
        result = original(event)
        if event.event_type == "priceops.optimized.v1":
            raise RuntimeError("synthetic audit failure after canonical adoption")
        return result
    monkeypatch.setattr(case.audit, "record", fail)
    case.client._transport.raise_server_exceptions = False
    response = case.client.post("/priceops/plans/canonical-plan/optimize", json={"reason": "authored", "exploration_gate_id": GATE})
    assert response.status_code >= 500
    assert balance(case) == 0 and ledger(case) == [] and case.repo.get_optimization("canonical-plan") is None


def test_duplicate_decision_and_immutable_sql_records_cannot_reset_budget(case):
    record = decision()
    saved = case.canonical.record_exploration_decision(record)
    assert saved.created_by == "verified-operator" and balance(case) == 1
    with pytest.raises(ExplorationNotAuthorizedError):
        case.canonical.record_exploration_decision(record)
    assert balance(case) == 1 and len(ledger(case)) == 1
    for statement in ["UPDATE pricing.exploration_gates SET budget_consumed=0 WHERE gate_id=?",
                      "DELETE FROM pricing.exploration_gates WHERE gate_id=?"]:
        with pytest.raises(Exception) as failure:
            case.engine.execute(statement, (GATE,))
        assert getattr(failure.value, "sqlstate", None) == "23514"
    for statement in ["UPDATE pricing.exploration_decisions SET budget_consumed=0 WHERE decision_id=?",
                      "DELETE FROM pricing.exploration_decisions WHERE decision_id=?"]:
        with pytest.raises(Exception) as failure:
            case.engine.execute(statement, (record.decision_id,))
        assert getattr(failure.value, "sqlstate", None) == "23514"
    assert balance(case) == 1


@pytest.mark.parametrize("case", [2], indirect=True)
def test_eight_connections_can_only_consume_two_real_budget_units(case):
    def consume(index):
        engine = PostgresEngine(case.url, validate_schema=False)
        try:
            store = CanonicalPriceExplorationStore(engine)
            try:
                store.record_exploration_decision(decision())
                return "spent"
            except ExplorationBudgetExceededError:
                return "exhausted"
        finally:
            engine.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(consume, range(8)))
    assert outcomes.count("spent") == 2 and outcomes.count("exhausted") == 6
    assert balance(case) == 2 and len(ledger(case)) == 2


def test_revocation_prevents_backdated_preview_or_decision(case):
    case.engine.execute("UPDATE pricing.exploration_gates SET revoked_at=? WHERE gate_id=?", (datetime.now(UTC).isoformat(), GATE))
    response = case.client.post("/priceops/exploration-candidates", json={"tenant_id": TENANT, "scope_brand_id": BRAND,
                                "items": case.proposal["items"], "at": (case.now-timedelta(hours=1)).isoformat()})
    assert response.status_code == 422 and response.json()["detail"]["exploration_enabled"] is False
    with pytest.raises(ExplorationNotAuthorizedError):
        case.canonical.record_exploration_decision(replace(decision(), created_at=case.now-timedelta(hours=1)))
    assert balance(case) == 0 and ledger(case) == []


def test_plain_production_repository_cannot_authorize_a_pickle_gate(case, monkeypatch):
    legacy = DurablePriceOpsRepository(SqliteDocumentStore(case.engine))
    gate = case.canonical.get_gate(GATE, TENANT)
    legacy.save_gate(gate)
    prepare(case)
    service = PriceOpsService(repository=legacy, runtime_mode="poc")
    service.production_required = True
    with pytest.raises(ExplorationNotAuthorizedError, match="canonical workflow"):
        service.optimize("canonical-plan", actor="verified-operator", exploration_gate_id=GATE)
    assert balance(case) == 0 and ledger(case) == []


def test_native_production_algorithm_uses_actual_canonical_gate_and_ledger(case, monkeypatch):
    from modules.priceops.infrastructure.oss_optimizer import PriceOpsProductionOptimizer
    from tests.integration.test_priceops_bandit_gating import _make_item

    item = replace(_make_item(item_id="native-sku", store_id=STORE, brand_id=BRAND),
                   source_snapshot_ids=("synthetic-price-history",))
    service = PriceOpsService(repository=case.repo, production_optimizer=PriceOpsProductionOptimizer(), runtime_mode="production")
    plan = service.create_plan(tenant_id=TENANT, items=[item], correlation_id="synthetic-native-canonical",
                               created_by="verified-producer")
    service.simulate(plan.plan_id)
    result = service.optimize(plan.plan_id, actor="verified-operator", exploration_gate_id=GATE, exploration_seed=42)
    assert result.solver_metadata["exploration_enabled"] is True
    assert result.solver_version == "priceops-optuna-cvxpy-v1"
    assert result.solver_metadata["engines"]["search"]["library"] == "optuna"
    assert result.solver_metadata["engines"]["portfolio"]["library"] == "cvxpy"
    assert len(ledger(case)) == 1 and ledger(case)[0]["created_by"] == "verified-operator"


def test_expired_gate_cannot_be_reenabled_by_past_decision_time(case):
    case.engine.execute("UPDATE pricing.exploration_gates SET effective_to=? WHERE gate_id=?",
                        ((case.now-timedelta(hours=1)).isoformat(), GATE))
    with pytest.raises(ExplorationNotAuthorizedError):
        case.canonical.record_exploration_decision(replace(decision(), created_at=case.now-timedelta(hours=2)))
    assert preview(case).status_code == 422 and balance(case) == 0


def test_actual_store_and_verified_actor_are_required_before_a_direct_canonical_debit(case):
    for record in [replace(decision(), created_by="system"), replace(decision(), created_by=None),
                   replace(decision(), store_id=None), replace(decision(), store_id=str(uuid4()))]:
        with pytest.raises(ExplorationNotAuthorizedError):
            case.canonical.record_exploration_decision(record)
    assert balance(case) == 0 and ledger(case) == []


@pytest.mark.parametrize("case", [0.02], indirect=True)
def test_budget_preflight_uses_actual_canonical_cent_precision(case):
    for _ in range(2):
        saved = case.canonical.record_exploration_decision(decision(0.005))
        assert saved.budget_consumed == 0.01
    with pytest.raises(ExplorationBudgetExceededError):
        case.canonical.record_exploration_decision(decision(0.005))
    assert balance(case) == 0.02 and len(ledger(case)) == 2


def test_rejected_workflow_decision_is_not_an_active_gate(case):
    case.engine.execute("UPDATE workflow.decisions SET decision_status='rejected' WHERE decision_id=?", (APPROVAL_DECISION,))
    assert case.canonical.get_gate(GATE, TENANT) is None
    assert preview(case).status_code == 422 and ledger(case) == []


@pytest.mark.parametrize("case", [{"group": "unbound-group"}], indirect=True)
def test_request_group_text_cannot_establish_canonical_membership(case):
    item = {**case.proposal["items"][0], "store_group": "unbound-group"}
    response = case.client.post("/priceops/exploration-candidates", json={
        "tenant_id": TENANT, "scope_brand_id": BRAND, "scope_store_group": "unbound-group", "items": [item],
    })
    assert response.status_code == 422 and response.json()["detail"]["exploration_enabled"] is False
    with pytest.raises(ExplorationNotAuthorizedError, match="membership"):
        case.canonical.record_exploration_decision(decision())
    assert balance(case) == 0 and ledger(case) == []
