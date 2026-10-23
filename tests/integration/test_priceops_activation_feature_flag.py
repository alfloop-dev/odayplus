"""Explicit SQLite engineering owner; only price activation is flag-gated."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.priceops import create_priceops_router
from apps.api.oday_api.routes.feature_flags import create_feature_flags_router
from apps.api.oday_api.security import dependencies
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from shared.api.errors import install_error_handlers
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.feature_flags import DurableFeatureFlagRegistry
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.repositories import DurablePriceOpsRepository
from tests.integration.test_priceops_atomic_commands import effects
from tests.integration.test_priceops_command_authority import proposal
from tests.integration.test_priceops_durable_commands import seed

KEY = "high_risk.priceops.execute"
PLAN = "/priceops/plans/durable-plan"
ADMIN = "/admin/feature-flags/" + KEY
SIGNING_KEY = SigningKey(kid="activation-engineering", algorithm="HS256",
                         secret=b"synthetic-activation-http-key")
ISSUER = "https://synthetic-activation.invalid"


COMMAND_PROOF = {"reason": "Synthetic operational command reason", "riskAcknowledged": True}


@dataclass
class Context:
    engine: SqliteEngine
    client: TestClient
    repository: DurablePriceOpsRepository
    audit: DurableAuditLog
    flags: DurableFeatureFlagRegistry


def headers(actor):
    now = datetime.now(UTC)
    token = encode_compact_jwt({
        "sub": actor, "iss": ISSUER, "aud": "activation-api",
        "iat": now.timestamp(), "exp": (now + timedelta(minutes=5)).timestamp(),
    }, SIGNING_KEY)
    return {"authorization": "Bearer " + token, "x-correlation-id": "synthetic-activation-http"}


@pytest.fixture
def context(tmp_path, monkeypatch):
    # This one fixture explicitly owns its ordinary SQLite and disabled flags.
    # It neither enables shared defaults nor asserts a production data gate.
    engine = SqliteEngine(tmp_path / "activation.sqlite")
    try:
        store = SqliteDocumentStore(engine)
        flags = DurableFeatureFlagRegistry(store)
        flags.initialize_defaults()
        audit = DurableAuditLog(engine)
        repository = DurablePriceOpsRepository(store)
        queue = DurableJobQueue(engine, flags=flags)
        boundary = AuthenticationBoundary(AuthBoundaryConfig(
            issuer=ISSUER, audiences=frozenset({"activation-api"}),
            signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
            principal_mappings={
                actor: {"roles": [role], "scope": {"tenant_id": tenant}}
                for actor, role, tenant in (
                    ("pricer", "pricing_manager", "tenant-a"),
                    ("reviewer", "pricing_manager", "tenant-a"),
                    ("foreign-pricer", "pricing_manager", "tenant-b"),
                    ("admin-a", "platform_admin", "tenant-a"),
                    ("admin-b", "platform_admin", "tenant-a"),
                )
            },
        ))
        monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
        app = FastAPI()
        app.state.feature_flag_registry = flags
        app.state.job_queue = queue
        install_error_handlers(app)

        @app.middleware("http")
        async def correlation(request, call_next):
            request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-activation-http")
            return await call_next(request)

        app.include_router(create_feature_flags_router(
            registry=flags, audit_log=audit, boundary=boundary, queue=queue,
        ))
        app.include_router(create_priceops_router(
            repository=repository, audit_log=audit, job_queue=queue,
            require_durable_commands=True, runtime_mode="poc",
        ))
        client = TestClient(app, headers=headers("pricer"))
        seed(client)
        submitted = client.post(PLAN + "/submit", json={"reason": "synthetic independent review"})
        assert submitted.status_code == 200, submitted.text
        approval = client.post(PLAN + "/approve", headers=headers("reviewer"), json={
            "actor_id": "forged", "decision": "approved", "reason": "synthetic independent review",
        })
        assert approval.status_code == 200, approval.text
        yield Context(engine, client, repository, audit, flags)
    finally:
        engine.close()


def enable_with_two_verified_admins(case):
    for actor in ("admin-a", "admin-b"):
        response = case.client.post(ADMIN + "/approve", headers=headers(actor), json={**COMMAND_PROOF, **({})})
        assert response.status_code == 200, response.text
    response = case.client.post(ADMIN + "/enable", headers=headers("admin-a"), json={**COMMAND_PROOF, **({})})
    assert response.status_code == 200, response.text
    assert case.flags.get(KEY).approved_by == frozenset({"admin-a", "admin-b"})


def test_default_disabled_activation_has_no_domain_audit_receipt_or_job_effect(context):
    case = context
    before = effects(case.engine)
    denied = case.client.post(PLAN + "/activate", headers={"Idempotency-Key": "activation-one"},
                              json={"executor": "forged"})
    assert denied.status_code == 403, denied.text
    assert case.repository.get_execution("durable-plan") is None
    assert effects(case.engine) == before
    enable_with_two_verified_admins(case)
    activated = case.client.post(PLAN + "/activate", headers={"Idempotency-Key": "activation-one"},
                                 json={"executor": "forged"})
    assert activated.status_code == 200, activated.text
    assert activated.json()["execution"]["executor"] == "pricer"
    assert case.repository.get_execution("durable-plan").executor == "pricer"
    assert len([event for event in case.audit.list_events()
                if event.event_type == "priceops.activated.v1"]) == 1


def test_disable_blocks_new_activation_but_keeps_committed_replay_and_rollback(context):
    case = context
    enable_with_two_verified_admins(case)
    activation_headers = {"Idempotency-Key": "committed-activation"}
    first = case.client.post(PLAN + "/activate", headers=activation_headers,
                             json={"executor": "forged"})
    assert first.status_code == 200, first.text
    assert case.client.post(ADMIN + "/disable", headers=headers("admin-b"), json=COMMAND_PROOF).status_code == 200
    before = effects(case.engine)
    refused = case.client.post(PLAN + "/activate", headers={"Idempotency-Key": "different-activation"},
                               json={"executor": "forged"})
    assert refused.status_code == 403, refused.text
    assert effects(case.engine) == before
    replay = case.client.post(PLAN + "/activate", headers=activation_headers, json={"executor": "forged"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["audit_event_id"] == first.json()["audit_event_id"]
    assert effects(case.engine) == before
    conflict = case.client.post(PLAN + "/activate", headers=activation_headers,
                                json={"executor": "forged", "measurement_method": "changed"})
    assert conflict.status_code == 409, conflict.text
    assert effects(case.engine) == before
    rolled_back = case.client.post(PLAN + "/rollback", json={"reason": "synthetic emergency rollback"})
    assert rolled_back.status_code == 200, rolled_back.text
    assert case.repository.get_plan("durable-plan").status.value == "rollback"


def test_disabled_flag_keeps_analysis_routes_and_original_auth_scope_precedence(context):
    case = context
    assert not case.flags.is_enabled(KEY)
    analysis_plan = "/priceops/plans/analysis-plan"
    created = case.client.post("/priceops/plans", json=proposal("analysis-plan"))
    assert created.status_code == 201, created.text
    for suffix, body in (
        ("simulate", {"reason": "synthetic analysis"}),
        ("simulate-scenario", {"candidate_prices": {"new-item": 80}}),
        ("optimize", {"reason": "synthetic optimization"}),
    ):
        response = case.client.post(analysis_plan + "/" + suffix, json=body)
        assert response.status_code == 200, response.text
    assert case.client.get(analysis_plan + "/comparison").status_code == 200
    for actor, status in (("admin-a", 403), ("foreign-pricer", 404)):
        before = effects(case.engine)
        response = case.client.post(PLAN + "/activate", headers=headers(actor), json={"executor": "forged"})
        assert response.status_code == status, response.text
        assert effects(case.engine) == before
    unauthenticated = case.client.post(PLAN + "/activate", headers={"authorization": "Bearer invalid"},
                                       json={"executor": "forged"})
    assert unauthenticated.status_code == 401


def test_local_receipt_lane_also_refuses_before_creating_failed_job(context):
    from modules.priceops.infrastructure import InMemoryPriceOpsRepository
    from shared.audit import InMemoryAuditLog
    from shared.jobs.queue import InMemoryJobQueue

    case = context
    repository = InMemoryPriceOpsRepository()
    audit = InMemoryAuditLog()
    queue = InMemoryJobQueue(flags=case.flags)
    app = FastAPI()
    app.state.feature_flag_registry = case.flags
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = "synthetic-local-activation"
        return await call_next(request)

    app.include_router(create_priceops_router(
        repository=repository, audit_log=audit, job_queue=queue, runtime_mode="poc",
    ))
    client = TestClient(app, headers=headers("pricer"))
    seed(client)
    assert client.post(PLAN + "/submit", json={"reason": "synthetic local review"}).status_code == 200
    approved = client.post(PLAN + "/approve", headers=headers("reviewer"), json={
        "actor_id": "forged", "decision": "approved", "reason": "synthetic local review",
    })
    assert approved.status_code == 200, approved.text
    before = dict(queue._jobs)
    denied = client.post(PLAN + "/activate", json={"executor": "forged"})
    assert denied.status_code == 403, denied.text
    assert queue._jobs == before
    assert repository.get_execution("durable-plan") is None
    assert not [event for event in audit.list_events() if event.event_type == "priceops.activated.v1"]


@pytest.mark.parametrize("owner_kind", ["missing", "RAM", "other-engine", "queue-owner"])
def test_durable_activation_rejects_missing_RAM_wrongengine_or_wrongqueue_owner(context, tmp_path, monkeypatch, owner_kind):
    from dataclasses import replace

    from shared.auth.feature_flags import DEFAULT_FLAGS, FeatureFlagRegistry

    case = context
    enable_with_two_verified_admins(case)
    enabled_ram = FeatureFlagRegistry({**DEFAULT_FLAGS, KEY: replace(
        DEFAULT_FLAGS[KEY], enabled=True, approved_by=frozenset({"synthetic-a", "synthetic-b"}),
    )})
    # A default-RAM getter can never authorize this durable command. This
    # monkeypatch is local to this one engineering control, no global enable.
    monkeypatch.setattr(dependencies, "default_registry", lambda: enabled_ram)
    other_engine = None
    try:
        if owner_kind == "missing":
            del case.client.app.state.feature_flag_registry
        elif owner_kind == "RAM":
            case.client.app.state.feature_flag_registry = enabled_ram
        elif owner_kind == "other-engine":
            other_engine = SqliteEngine(tmp_path / "unrelated-owner.sqlite")
            other = DurableFeatureFlagRegistry(SqliteDocumentStore(other_engine))
            other.initialize_defaults()
            other_audit = DurableAuditLog(other_engine)
            for operation, actor in (("approve", "synthetic-a"), ("approve", "synthetic-b"), ("enable", "synthetic-a")):
                other.execute(operation, KEY, actor=actor, correlation_id="synthetic-wrong-owner", audit_log=other_audit)
            assert other.is_enabled(KEY)
            case.client.app.state.feature_flag_registry = other
        else:
            # A different registry object, even on the same database engine,
            # must not replace the owner bound to the actual command queue.
            case.client.app.state.feature_flag_registry = DurableFeatureFlagRegistry(SqliteDocumentStore(case.engine))
        before = effects(case.engine)
        response = case.client.post(PLAN + "/activate", headers={"Idempotency-Key": "wrong-owner-activation"},
                                    json={"executor": "forged"})
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "FEATURE_FLAG_STORE_UNAVAILABLE"
        assert effects(case.engine) == before
        assert case.repository.get_execution("durable-plan") is None
    finally:
        if other_engine is not None:
            other_engine.close()
