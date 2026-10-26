"""Explicit single-operation flag premises for isolated engineering fixtures.

No autouse fixture or global registry mutation lives here. Each successful
business callback must explicitly choose its one operation key. Durable callers
supply their actual raw engine/queue; flags remain platform-global, not tenant
partitions. HMAC subjects below are synthetic test administrators, not human
business approval, production IAM, PostgreSQL proof, or external WORM ack.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.oday_api.routes.feature_flags import create_feature_flags_router
from apps.api.oday_api.security.dependencies import build_engine
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.audit import InMemoryAuditLog
from shared.auth.feature_flags import DEFAULT_FLAGS, FeatureFlagRegistry
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.feature_flags import DurableFeatureFlagRegistry
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY, _headers

SITE_GO_FLAG = "high_risk.sitescore.approve"
NETPLAN_MOVE_EXIT_FLAG = "high_risk.netplan.approve"
MODEL_PUBLISH_FLAG = "high_risk.model.publish"
PROOF = {"reason": "Explicit isolated operation fixture enablement", "riskAcknowledged": True}


def new_operation_flags(engine=None):
    """Create only disabled defaults on this fixture's explicit owner."""
    if engine is None:
        return FeatureFlagRegistry(dict(DEFAULT_FLAGS)), None
    owner = DurableFeatureFlagRegistry(SqliteDocumentStore(engine))
    owner.initialize_defaults()
    return owner, DurableJobQueue(engine, flags=owner)


def enable_operation_flag(owner, *, key, tenant_id, queue=None, audit_log=None):
    """Use two verified test subjects and the real admin HTTP command path.

    This helper never creates an owner implicitly or selects a key itself.
    Existing enabled state is a persisted premise, not a new approval claim.
    """
    assert key in {SITE_GO_FLAG, NETPLAN_MOVE_EXIT_FLAG, MODEL_PUBLISH_FLAG}
    assert owner is not None and owner.get(key) is not None
    if isinstance(owner, DurableFeatureFlagRegistry):
        assert queue is not None and queue._engine is owner.engine and queue._flags is owner
        audit_log = audit_log or DurableAuditLog(owner.engine)
        assert audit_log._engine is owner.engine
    else:
        assert isinstance(owner, FeatureFlagRegistry)
        audit_log = audit_log or InMemoryAuditLog()
    if owner.is_enabled(key):
        return
    boundary = AuthenticationBoundary(AuthBoundaryConfig(
        issuer=ISSUER,
        audiences=frozenset({"unit-feature-api"}),
        signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
        principal_mappings={
            actor: {"roles": ["platform_admin"], "scope": {"tenant_id": tenant_id}}
            for actor in ("admin-a", "admin-b")
        },
    ), audit_log=audit_log)
    app = FastAPI()
    app.state.feature_flag_registry = owner
    app.state.job_queue = queue

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "fixture-flag-confirmation")
        return await call_next(request)

    app.include_router(create_feature_flags_router(
        registry=owner, audit_log=audit_log, queue=queue, boundary=boundary,
        engine=build_engine(audit_log=audit_log, flags=owner),
    ), prefix="/api/v1")
    with TestClient(app) as client:
        path = "/api/v1/admin/feature-flags/" + key
        for actor in ("admin-a", "admin-b"):
            response = client.post(path + "/approve", json=PROOF, headers=_headers(actor))
            assert response.status_code == 200, response.text
        response = client.post(path + "/enable", json=PROOF, headers=_headers("admin-a"))
        assert response.status_code == 200, response.text
        assert response.json()["flag"]["enabled"] is True
        assert set(response.json()["flag"]["approved_by"]) == {"admin-a", "admin-b"}
    assert owner.is_enabled(key)
