"""Real bundle/factory/main-mount engineering controls, never true PG acceptance.

Own SQLite remains blocked by the unchanged live platform persistence guard.
The main-mount closure check and direct real-factory HTTP route are separate;
we do not label a lambda router fixture as actual app composition coverage.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from inspect import getclosurevars

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.audit import create_audit_router
from modules.opsboard.audit.application.evidence_general_admission import (
    GeneralDecisionEvidenceSelection,
)
from modules.opsboard.audit.application.evidence_general_factory import (
    GeneralSiteScoreExportFactory,
)
from modules.opsboard.audit.application.evidence_general_read import GeneralSiteScoreRetainedReader
from modules.opsboard.audit.application.evidence_projection import EvidencePolicyScope
from modules.opsboard.audit.application.sitescore_evidence import SiteScoreEvidenceUnavailable
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from shared.audit import InMemoryAuditLog
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.observability.routes import iter_route_templates
from tests.integration.test_audit_sitescore_evidence_resolver import decide, principal, selected
from tests.unit.test_network_canonical_review_projection import report
from tests.unit.test_network_review_canonical_action_metadata import (
    TENANT,
    install_real_related_producers,
)


@pytest.fixture
def actual_factory(tmp_path):
    sink = LocalAppendOnlyWormSink(tmp_path / "actual-factory-local-worm-only")
    bundle = _durable_bundle(tmp_path / "actual-factory-owned-sqlite.sqlite", worm_sink=sink)
    try:
        factory = GeneralSiteScoreExportFactory(bundle, audit_log=bundle.audit_log,
            evidence_store=bundle.evidence_store, queue=bundle.job_queue)
        service = factory(TENANT)
        source = service.admission.source_owner
        workflow = source.review_owner._decision_workflow
        install_real_related_producers(bundle.engine, source.decisions, workflow, source.review_owner)
        frozen = source.reports.save_report(report())
        parts = (bundle.engine, source.reports, source.decisions, bundle.audit_log, workflow, source.review_owner)
        outcome = decide(parts, frozen)
        yield bundle, factory, outcome, sink
    finally:
        bundle.engine.close()


def test_actual_bundle_factory_reads_same_tenant_canonical_owners_after_reconstruction_and_delivers(actual_factory):
    bundle, factory, outcome, sink = actual_factory
    service = factory(TENANT)
    actor = principal()
    source = service.admission.source_owner.resolve_source(actor, selected(outcome))
    assert service.engine is bundle.engine
    assert service.delivery.queue is service.receipts.queue is bundle.job_queue
    assert service.delivery.audit_log is bundle.audit_log
    assert service.delivery.evidence_store is bundle.evidence_store
    assert service.delivery.sink is sink and source.decision_id == outcome.decision.decision_id
    request = GeneralDecisionEvidenceSelection(selected(outcome), "Actual captured factory control",
        EvidencePolicyScope("json", "authenticated-response", source.events[0].occurred_at,
            source.events[-1].occurred_at))
    result = service.export(actor, request, idempotency_key="actual-factory-key", correlation_id="actual-factory-cid")
    record = service.delivery.evidence_store.get(result["export_id"])
    service.delivery.deliver(service.delivery.queue.get_by_idempotency_key(service.delivery._key(record.export_id)))
    fresh = factory(TENANT)
    assert GeneralSiteScoreRetainedReader(fresh).content(actor, record.export_id,
        correlation_id="actual-fresh-factory-read-cid")


@pytest.mark.parametrize("changed", ["audit", "queue", "memory"])
def test_factory_refuses_wrong_actual_app_owner_instead_of_fallback(actual_factory, changed):
    bundle, _factory, _outcome, _sink = actual_factory
    selected_bundle = replace(bundle, mode="memory", engine=None) if changed == "memory" else bundle
    audit = InMemoryAuditLog() if changed == "audit" else bundle.audit_log
    queue = DurableJobQueue(bundle.engine) if changed == "queue" else bundle.job_queue
    factory = GeneralSiteScoreExportFactory(selected_bundle, audit_log=audit,
        evidence_store=bundle.evidence_store, queue=queue)
    before = bundle.evidence_store.list_all()
    with pytest.raises(SiteScoreEvidenceUnavailable):
        factory(TENANT)
    assert bundle.evidence_store.list_all() == before


def verified_boundary():
    actor = principal()
    key = SigningKey(kid="actual-factory", algorithm="HS256", secret=b"actual-factory-engineering-key")
    boundary = AuthenticationBoundary(AuthBoundaryConfig(issuer="https://factory.invalid",
        audiences=frozenset({"factory-api"}), signing_keys={key.kid: key}, principal_mappings={
            actor.subject_id: {"roles": [role.value for role in actor.roles], "scope": {"tenant_id": TENANT}}}))
    now = datetime.now(UTC)
    token = encode_compact_jwt({"sub": actor.subject_id, "iss": "https://factory.invalid", "aud": "factory-api",
        "iat": now.timestamp(), "exp": (now + timedelta(minutes=5)).timestamp()}, key)
    return boundary, {"Authorization": "Bearer " + token, "Idempotency-Key": "actual-factory-http-key"}


def test_actual_factory_HTTP_uses_real_bundle_without_lambda_service(actual_factory, monkeypatch):
    bundle, factory, outcome, _sink = actual_factory
    service = factory(TENANT)
    actual = service.admission.source_owner.resolve_source(principal(), selected(outcome))
    boundary, headers = verified_boundary()
    app = FastAPI()
    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = "actual-factory-http-cid"
        return await call_next(request)
    app.include_router(create_audit_router(audit_log=bundle.audit_log, evidence_store=bundle.evidence_store,
        boundary=boundary, canonical_service_for_tenant=factory))
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    client = TestClient(app, headers=headers)
    response = client.post("/audit/evidence/export", json={"decision_id": actual.decision_id,
        "expected_decision_version": actual.decision_version, "sections": ["decision_log", "audit"],
        "reason": "Actual factory HTTP control", "format": "json",
        "from_time": actual.events[0].occurred_at.isoformat(), "to_time": actual.events[-1].occurred_at.isoformat()})
    assert response.status_code == 201 and response.json()["external_acknowledged"] is False
    record = bundle.evidence_store.get(response.json()["export_id"])
    delivery = factory(TENANT).delivery
    delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    downloaded = client.get(f"/audit/evidence/exports/{record.export_id}/download", params={"format": "json"})
    assert downloaded.status_code == 200 and downloaded.json() == record.bundle["document"]


def test_actual_create_app_mount_captures_real_factory_and_sqlite_live_gate_still_blocks(actual_factory, monkeypatch):
    # No database dialect disguise, middleware bypass or healthy model/provider
    # substitute: this app correctly cannot pass the production persistence gate.
    from apps.api.oday_api import main
    from apps.api.oday_api.security import dependencies

    bundle, _factory, _outcome, _sink = actual_factory
    boundary, headers = verified_boundary()
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "sqlite")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.setattr(dependencies, "_default_boundary", boundary)
    app = main.create_app(persistence=bundle)
    # Force the original framework route-table publication; inspect the actual
    # mounted endpoint, not a test replacement of create_audit_router/factory.
    app.openapi()
    assert "/audit/evidence/export" in set(iter_route_templates(app.routes))
    def leaves(routes):
        # Same original_router/include tree as the existing bounded route
        # traversal. No flattening or replacement of the mounted endpoint.
        for route in routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                yield from leaves(getattr(nested, "routes", []))
            elif getattr(route, "routes", None):
                yield from leaves(route.routes)
            else:
                yield route
    endpoint = next(route.endpoint for route in leaves(app.routes)
        if getattr(route, "path", None) == "/audit/evidence/export")
    canonical_service = getclosurevars(endpoint).nonlocals["canonical_service"]
    factory = getclosurevars(canonical_service).nonlocals["canonical_service_for_tenant"]
    assert isinstance(factory, GeneralSiteScoreExportFactory)
    assert factory.bundle is bundle and factory.audit_log is bundle.audit_log
    assert factory.evidence_store is bundle.evidence_store and factory.queue is bundle.job_queue
    selected_service = factory(TENANT)
    assert selected_service.engine is bundle.engine
    before = bundle.evidence_store.list_all()
    response = TestClient(app, headers=headers).post("/audit/evidence/export", json={})
    assert response.status_code == 503
    assert "SQLITE_NOT_PRODUCTION_PERSISTENCE" in response.text
    assert bundle.evidence_store.list_all() == before
