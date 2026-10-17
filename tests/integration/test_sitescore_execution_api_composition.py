"""Fresh execution ownership requires the actual durable PostgreSQL factory.

Historical report fixtures are saved directly; these tests never infer, admit
a model, grant a live role, or execute a business job. The live-marked cases
use a fresh migrated PostgreSQL database and the unmodified persistence factory.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes import operator
from apps.api.oday_api.main import create_app
from modules.listing.application.intake_score_delivery import ScoreDeliveryUnavailable
from modules.sitescore.application.score_execution import SiteScoreExecutionService
from modules.sitescore.domain.scoring import Interval, SiteScoreRecommendation, SiteScoreReport
from shared.infrastructure.persistence.factory import _durable_bundle, build_persistence
from shared.infrastructure.persistence.postgresql import PostgresEngine
from tests.integration._authz import SITESCORE_HEADERS
from tests.integration.test_operator_canonical_wiring import _seed_candidate


def _capture_owner(monkeypatch):
    captured = []
    original = operator.create_operator_router

    def create_router(**kwargs):
        captured.append(kwargs["score_execution_service"])
        return original(**kwargs)

    monkeypatch.setattr(operator, "create_operator_router", create_router)
    return captured


def _unavailable_model_environment(monkeypatch):
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("ODP_E2E_MODE", raising=False)


def _historical_report():
    at = datetime(2026, 10, 13, tzinfo=UTC)
    return SiteScoreReport(
        sitescore_run_id="saved-synthetic-run", candidate_site_id="saved-synthetic-candidate",
        target_format_code="saved-format", recommendation=SiteScoreRecommendation.WAIT,
        m1=Interval(100, 200, 300), m3=Interval(200, 300, 400),
        m6=Interval(300, 400, 500), m12=Interval(400, 500, 600),
        payback_period=Interval(20, 25, 30), payback_p50_months=25,
        rent_reasonableness=0.8, cannibalization_risk=0.1, comparable_stores=4,
        key_positive_factors=(), key_negative_factors=(), confidence=0.9,
        model_version="saved-unavailable-model-v1", feature_version="saved-feature-v1",
        feature_snapshot_time=at, prediction_origin_time=at, scored_at=at,
    )


def _sqlite_business_rows(engine):
    return {
        table: engine.query(f"SELECT * FROM {table} ORDER BY 1")
        for table in ("durable_documents", "durable_jobs", "durable_outbox_events")
    }


def test_flagged_sqlite_keeps_stored_history_and_health_without_fresh_execution_authority(monkeypatch, tmp_path):
    _unavailable_model_environment(monkeypatch)
    bundle = _durable_bundle(tmp_path / "composition.sqlite3")
    candidate = _seed_candidate(SimpleNamespace(listing=bundle.listing_repository_for_tenant), "tenant-a")
    reports = bundle._scoped_repository("sitescore_repository", "tenant-a")
    retained = reports.save_report(replace(_historical_report(), candidate_site_id=candidate))
    # Match the existing API composition probes. The flag is deliberately not
    # a canonical transaction authority; its actual engine remains SQLite.
    bundle.engine.is_production = True
    bundle = replace(bundle, mode="postgresql", assisted_intake_store=SimpleNamespace())
    captured = _capture_owner(monkeypatch)
    try:
        app = create_app(
            persistence=bundle,
            external_provider_validation=SimpleNamespace(ok=True, errors=(), mode="live"),
        )
        assert captured == [None]
        assert app.state.model_runtime is None
        assert app.state.production_model_capabilities["sitescore"]["available"] is False
        before = _sqlite_business_rows(bundle.engine)
        with TestClient(app, headers=SITESCORE_HEADERS) as client:
            assert client.get("/healthz").status_code == 200
            historical = client.get(f"/api/v1/sitescore/reports/{retained.candidate_site_id}")
            # A SQLite probe's flags cannot admit a production native reader.
            # The genuine PostgreSQL history tests retain exact HTTP 200
            # assertions, while this unsupported producer stays unavailable.
            assert historical.status_code == 503
            assert historical.json()["detail"] == "CANONICAL_REPORT_READ_BINDING_REQUIRED"
            context = client.get(
                f"/api/v1/operator/network-scoring/candidates/{retained.candidate_site_id}/execution-context",
                headers={"x-operator-role": "expansion-manager"},
            )
            assert context.status_code == 503
            assert "SITESCORE_EXECUTION_OWNER_UNAVAILABLE" in context.json()["detail"]["code"]
            command = client.post(
                f"/api/v1/operator/network-scoring/candidates/{retained.candidate_site_id}/score",
                headers={"x-operator-role": "expansion-manager", "Idempotency-Key": "no-canonical-owner"},
                json={"expectedReportId": retained.report_id, "expectedReportVersion": 1, "reason": "synthetic refusal"},
            )
            assert command.status_code == 503
        assert _sqlite_business_rows(bundle.engine) == before
        assert reports.get_report(retained.report_id).to_dict() == retained.to_dict()
        assert [report.to_dict() for report in reports.history(retained.candidate_site_id)] == [retained.to_dict()]
    finally:
        bundle.engine.close()


@pytest.fixture
def canonical_bundle(intake_blank_db, monkeypatch):
    from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
    from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade

    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    _unavailable_model_environment(monkeypatch)
    bundle = _bundle(intake_blank_db, monkeypatch)
    try:
        yield bundle
    finally:
        bundle.engine.close()


@pytest.mark.requires_live_env
def test_actual_postgresql_owner_is_wired_without_claiming_model_readiness(canonical_bundle, monkeypatch):
    captured = _capture_owner(monkeypatch)
    app = create_app(
        persistence=canonical_bundle,
        external_provider_validation=SimpleNamespace(ok=True, errors=(), mode="live"),
    )
    assert isinstance(canonical_bundle.engine, PostgresEngine)
    assert len(captured) == 1
    owner = captured[0]
    assert isinstance(owner, SiteScoreExecutionService)
    assert owner.repository.persistence is canonical_bundle
    assert owner.repository.engine is canonical_bundle.engine
    assert owner.repository.store._engine is canonical_bundle.engine
    assert owner.repository.queue._engine is canonical_bundle.engine
    assert owner.repository.outbox._engine is canonical_bundle.engine
    assert app.state.model_runtime is None
    assert app.state.production_model_capabilities["sitescore"]["available"] is False
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


@pytest.mark.requires_live_env
@pytest.mark.parametrize("foreign", ("assisted_intake_store", "job_queue", "outbox_repository"))
def test_actual_postgresql_foreign_engine_binding_still_refuses_app_composition(canonical_bundle, foreign):
    # Both bundles are genuine SQL repositories in this same fresh database.
    # A different pooled engine still cannot participate in the owner's atomic
    # transaction. Do not manufacture an _engine attribute or relabel a dialect.
    other = build_persistence(mode="postgresql")
    try:
        assert isinstance(other.engine, PostgresEngine)
        assert other.engine is not canonical_bundle.engine
        broken = replace(canonical_bundle, **{foreign: getattr(other, foreign)})
        with pytest.raises(ScoreDeliveryUnavailable, match="SITESCORE_TRANSACTION_BINDING_UNAVAILABLE"):
            create_app(
                persistence=broken,
                external_provider_validation=SimpleNamespace(ok=True, errors=(), mode="live"),
            )
    finally:
        other.engine.close()


READ_TENANTS = ("00000000-0000-0000-0000-000000000801", "00000000-0000-0000-0000-000000000802")
READ_ACTORS = ("00000000-0000-0000-0000-000000000811", "00000000-0000-0000-0000-000000000812")
READ_INTERVALS = {
    READ_TENANTS[0]: {"m1": Interval(100, 200, 300), "m3": Interval(200, 300, 400),
                      "m6": Interval(300, 400, 500), "m12": Interval(400, 500, 600)},
    READ_TENANTS[1]: {"m1": Interval(1100, 2200, 3300), "m3": Interval(4400, 5500, 6600),
                      "m6": Interval(7700, 8800, 9900), "m12": Interval(11000, 22000, 33000)},
}


def _assert_tenant_intervals(report_dto, tenant):
    # These are recorded synthetic fixture numbers, never model accuracy proof.
    for horizon, band in READ_INTERVALS[tenant].items():
        assert report_dto[horizon] == band.to_dict()
        other = READ_TENANTS[1] if tenant == READ_TENANTS[0] else READ_TENANTS[0]
        assert report_dto[horizon] != READ_INTERVALS[other][horizon].to_dict()


def _saved_sql_read_domain(bundle):
    records = []
    for tenant, actor in zip(READ_TENANTS, READ_ACTORS, strict=True):
        bundle.engine.execute("INSERT INTO core.tenants(tenant_id,tenant_name) VALUES(?,?)", (tenant, "isolated-read-composition"))
        bundle.engine.execute(
            "INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) VALUES(?,?,?,?,'active','isolated-read-composition')",
            (actor, tenant, "read-" + actor, actor + "@example.invalid"),
        )
        bundle.engine.execute(
            "INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES(?,'site_reviewer','isolated-read-composition')", (actor,),
        )
        bundle.engine.execute("INSERT INTO identity.account_scopes(account_id,modules) VALUES(?,?)", (actor, '["sitescore"]'))
        candidate = _seed_candidate(SimpleNamespace(listing=bundle.listing_repository_for_tenant), tenant)
        repository = bundle._scoped_repository("sitescore_repository", tenant)
        first = repository.save_report(replace(_historical_report(), candidate_site_id=candidate,
                                              model_version="saved-" + tenant, **READ_INTERVALS[tenant]))
        second = repository.save_report(replace(first, confidence=0.6))
        records.append((tenant, actor, candidate, first, second))
    return records


def _signed_sql_headers(bundle, monkeypatch):
    from apps.api.oday_api.security import dependencies
    from modules.opsboard.auth import (
        AuthBoundaryConfig,
        AuthenticationBoundary,
        SigningKey,
        encode_compact_jwt,
    )

    key = SigningKey(kid="isolated-report-read", algorithm="HS256", secret=b"isolated-synthetic-native-read-signing-key")
    boundary = AuthenticationBoundary(AuthBoundaryConfig(
        local_issuer="urn:odp:identity:local", local_audiences=frozenset({"isolated-read-composition"}),
        local_signing_keys={key.kid: key}, identity_store=bundle.identity_store, session_service=bundle.session_service,
    ), audit_log=bundle.audit_log)
    monkeypatch.setattr(dependencies, "_default_boundary", boundary)

    def headers(tenant, actor):
        session = bundle.session_service.create_session(account_id=UUID(actor), provider="local_password")
        now = datetime.now(UTC)
        token = encode_compact_jwt({"sub": actor, "sid": str(session.session_id), "tenant_id": tenant,
            "iss": "urn:odp:identity:local", "aud": "isolated-read-composition", "iat": now.timestamp(),
            "exp": (now + timedelta(minutes=20)).timestamp()}, key)
        return {"Authorization": "Bearer " + token}
    return headers


def _pg_business_rows(bundle):
    return {table: bundle.engine.query(f"SELECT * FROM {table} ORDER BY 1")
            for table in ("durable_documents", "durable_jobs", "workflow.jobs", "workflow.outbox_events")}


@pytest.mark.requires_live_env
def test_saved_report_history_without_model_uses_actual_sql_tenant_and_native_only_scope(canonical_bundle, monkeypatch):
    records = _saved_sql_read_domain(canonical_bundle)
    app = create_app(persistence=canonical_bundle, external_provider_validation=SimpleNamespace(ok=True, errors=(), mode="live"))
    headers = _signed_sql_headers(canonical_bundle, monkeypatch)
    signed = [headers(tenant, actor) for tenant, actor, *_ in records]
    before = _pg_business_rows(canonical_bundle)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        for (tenant, _actor, candidate, first, latest), authorization in zip(records, signed, strict=True):
            listed = client.get("/api/v1/sitescore/reports", headers=authorization)
            assert listed.status_code == 200, listed.text
            assert listed.json() == {"items": [latest.to_summary_dict()], "count": 1}
            _assert_tenant_intervals(listed.json()["items"][0], tenant)
            history = client.get(f"/api/v1/sitescore/reports/{candidate}", headers=authorization)
            assert history.status_code == 200, history.text
            assert history.json() == {"latest": latest.to_dict(), "versions": [first.to_dict(), latest.to_dict()], "version_count": 2}
            _assert_tenant_intervals(history.json()["latest"], tenant)
            for recorded in history.json()["versions"]:
                _assert_tenant_intervals(recorded, tenant)
            # Native-only scope does not grant the Operator module.
            assert client.get("/api/v1/operator/network-reviews", headers=authorization).status_code == 403
            assert client.post("/api/v1/sitescore/score-jobs", headers=authorization, json={"features": []}).status_code == 503
        assert client.get("/api/v1/sitescore/reports").status_code == 401
        tenant, actor, candidate, *_ = records[0]
        for field, value in (("modules", '["operator_console"]'), ("assigned_area_ids", '["foreign-area"]'), ("clearance", "INTERNAL")):
            canonical_bundle.engine.execute(f"UPDATE identity.account_scopes SET {field}=? WHERE account_id=?", (value, actor))
            denied = client.get(f"/api/v1/sitescore/reports/{candidate}", headers=signed[0])
            assert denied.status_code == 403, denied.text
            assert "versions" not in denied.json()
            canonical_bundle.engine.execute(f"UPDATE identity.account_scopes SET {field}=? WHERE account_id=?", ('["sitescore"]' if field == "modules" else '[]' if field == "assigned_area_ids" else "CONFIDENTIAL", actor))
        foreign = client.get(f"/api/v1/sitescore/reports/{candidate}", headers={**signed[0], "x-tenant-id": READ_TENANTS[1]})
        assert foreign.status_code == 403
        missing = client.get("/api/v1/sitescore/reports/no-such-candidate", headers=signed[0])
        assert missing.status_code == 404
    assert app.state.model_runtime is None
    assert app.state.production_model_capabilities["sitescore"]["available"] is False
    assert _pg_business_rows(canonical_bundle) == before


@pytest.mark.requires_live_env
def test_actual_postgresql_poc_mode_keeps_scoped_read_and_refuses_native_inline_fresh(canonical_bundle, monkeypatch):
    from apps.api.app.routes.sitescore import create_sitescore_router

    records = _saved_sql_read_domain(canonical_bundle)
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODAY_ENV", "test")
    # Bind the actual router directly: whole-app POC setup additionally requires
    # a configured Learning Hub registry. This control reads saved SQL reports
    # and must not seed or override any current model to satisfy that setup.
    app = FastAPI()
    app.include_router(create_sitescore_router(
        repository=canonical_bundle.sitescore_repository,
        sitescore_repository_for_tenant=lambda tenant: canonical_bundle._scoped_repository("sitescore_repository", tenant),
        review_service_resolver=canonical_bundle.network_review_resolver(),
        audit_log=canonical_bundle.audit_log, runtime_mode="poc",
    ), prefix="/api/v1")
    headers = _signed_sql_headers(canonical_bundle, monkeypatch)
    signed = [headers(tenant, actor) for tenant, actor, *_ in records]
    before = _pg_business_rows(canonical_bundle)
    with TestClient(app) as client:
        for (tenant, _, candidate, first, latest), authorization in zip(records, signed, strict=True):
            listed = client.get("/api/v1/sitescore/reports", headers=authorization)
            assert listed.status_code == 200, listed.text
            assert listed.json() == {"items": [latest.to_summary_dict()], "count": 1}
            _assert_tenant_intervals(listed.json()["items"][0], tenant)
            history = client.get(f"/api/v1/sitescore/reports/{candidate}", headers=authorization)
            assert history.status_code == 200, history.text
            assert history.json() == {"latest": latest.to_dict(), "versions": [first.to_dict(), latest.to_dict()], "version_count": 2}
            _assert_tenant_intervals(history.json()["latest"], tenant)
            for recorded in history.json()["versions"]:
                _assert_tenant_intervals(recorded, tenant)
            for path in ("/api/v1/sitescore/reports", "/api/v1/sitescore/score-jobs"):
                fresh = client.post(path, headers=authorization, json={"features": []})
                assert fresh.status_code == 503, fresh.text
                assert fresh.json()["detail"]["code"] == "SITESCORE_EXECUTION_CONTEXT_REQUIRED"
        assert client.get("/api/v1/sitescore/reports", headers={**signed[0], "x-tenant-id": READ_TENANTS[1]}).status_code == 403
    assert _pg_business_rows(canonical_bundle) == before


@pytest.mark.requires_live_env
def test_foreign_sql_report_reader_with_coincident_candidate_id_is_unavailable(canonical_bundle, monkeypatch):
    records = _saved_sql_read_domain(canonical_bundle)
    other = build_persistence(mode="postgresql")
    try:
        tenant, actor, candidate, *_ = records[0]
        injected = other._scoped_repository("sitescore_repository", tenant)
        app = create_app(persistence=canonical_bundle, sitescore_repository=injected,
                         external_provider_validation=SimpleNamespace(ok=True, errors=(), mode="live"))
        headers = _signed_sql_headers(canonical_bundle, monkeypatch)(tenant, actor)
        before = _pg_business_rows(canonical_bundle)
        with TestClient(app) as client:
            for path in ("/api/v1/sitescore/reports", f"/api/v1/sitescore/reports/{candidate}"):
                response = client.get(path, headers=headers)
                assert response.status_code == 503, response.text
                assert "items" not in response.json() and "versions" not in response.json()
        assert _pg_business_rows(canonical_bundle) == before
    finally:
        other.engine.close()


@pytest.mark.requires_live_env
@pytest.mark.parametrize("binding", ("missing", "disabled"))
def test_missing_or_disabled_native_read_binding_is_not_an_empty_ready_module(canonical_bundle, monkeypatch, binding):
    from apps.api.app.routes.sitescore import create_sitescore_router

    records = _saved_sql_read_domain(canonical_bundle)
    app = create_app(persistence=canonical_bundle, external_provider_validation=SimpleNamespace(ok=True, errors=(), mode="live"))
    app.include_router(create_sitescore_router(
        repository=canonical_bundle.sitescore_repository if binding == "missing" else None,
        audit_log=canonical_bundle.audit_log, runtime_mode="production",
        sitescore_repository_for_tenant=(lambda tenant: canonical_bundle._scoped_repository("sitescore_repository", tenant)) if binding == "disabled" else None,
        review_service_resolver=canonical_bundle.network_review_resolver(),
    ), prefix="/isolated")
    tenant, actor, candidate, *_ = records[0]
    headers = _signed_sql_headers(canonical_bundle, monkeypatch)(tenant, actor)
    before = _pg_business_rows(canonical_bundle)
    with TestClient(app) as client:
        for path in ("/isolated/sitescore/reports", f"/isolated/sitescore/reports/{candidate}"):
            response = client.get(path, headers=headers)
            assert response.status_code == 503, response.text
            assert "items" not in response.json() and "versions" not in response.json()
    assert _pg_business_rows(canonical_bundle) == before


@pytest.mark.requires_live_env
@pytest.mark.parametrize("fault", ("model", "candidate_key"))
def test_retained_report_unknown_identity_does_not_release_numeric_rows(canonical_bundle, monkeypatch, fault):
    tenant, actor, candidate, _, latest = _saved_sql_read_domain(canonical_bundle)[0]
    repository = canonical_bundle._scoped_repository("sitescore_repository", tenant)
    if fault == "model":
        malformed = repository.save_report(replace(latest, model_version=""))
        assert malformed.report_version == 3
        expected = "CANONICAL_RETAINED_REPORT_IDENTITY_UNAVAILABLE"
    else:
        listing = canonical_bundle.listing_repository_for_tenant(tenant)
        actual = listing._store.get(listing._CANDIDATES, candidate)
        candidate = "misbound-synthetic-candidate-key"
        listing._store.put(listing._CANDIDATES, candidate, actual)
        repository.save_report(replace(latest, candidate_site_id=candidate))
        expected = "CANONICAL_REPORT_READ_BINDING_REQUIRED"
    app = create_app(persistence=canonical_bundle, external_provider_validation=SimpleNamespace(ok=True, errors=(), mode="live"))
    headers = _signed_sql_headers(canonical_bundle, monkeypatch)(tenant, actor)
    before = _pg_business_rows(canonical_bundle)
    with TestClient(app) as client:
        for path in ("/api/v1/sitescore/reports", f"/api/v1/sitescore/reports/{candidate}"):
            response = client.get(path, headers=headers)
            assert response.status_code == 503, response.text
            assert response.json()["detail"] == expected
            assert "items" not in response.json() and "versions" not in response.json()
    assert _pg_business_rows(canonical_bundle) == before
