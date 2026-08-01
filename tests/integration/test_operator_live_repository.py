from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from apps.api.app.routes.operator import _live_operator_request_context
from apps.api.oday_api.main import create_app
from apps.api.oday_api.security.dependencies import (
    OPERATOR_CONSOLE_RESOURCE,
    require_operator_permission,
)
from modules.forecastops.domain.forecasting import Alert, AlertLevel
from modules.opsboard.application.operator_live_repository import (
    OperatorLiveRepository,
    OperatorTenantScopeRequiredError,
)
from modules.opsboard.application.operator_state import OperatorStateService
from shared.audit import AuditEvent
from shared.auth import Action, Role
from shared.domain import AddressLocation, Brand, Listing, Store, Tenant, Transaction
from shared.infrastructure.persistence.factory import _durable_bundle, _memory_bundle


def _alert(alert_id: str = "alert-live-1") -> Alert:
    return Alert(
        alert_id=alert_id,
        tenant_id="tenant-live-1",
        store_id="store-live-1",
        alert_level=AlertLevel.RED,
        alert_reason_code="REVENUE_DROP",
        evidence_json={"snapshot_id": "snapshot-live-1"},
        opened_at=datetime(2026, 7, 24, 8, 0, tzinfo=UTC),
    )


def _route_for(app: Any, target_path: str) -> Any:
    def walk(router: Any, prefix: str = "") -> Any:
        for route in getattr(router, "routes", []):
            path = getattr(route, "path", "")
            if path and f"{prefix}{path}" == target_path:
                return route
            nested = getattr(route, "original_router", None)
            if nested is None:
                continue
            context = getattr(route, "include_context", None)
            nested_prefix = getattr(context, "prefix", "") or ""
            matched = walk(nested, f"{prefix}{nested_prefix}")
            if matched is not None:
                return matched
        return None

    route = walk(app)
    assert route is not None, f"route not found: {target_path}"
    return route


def test_empty_live_repository_is_ready_without_seed_rows() -> None:
    bundle = replace(_memory_bundle(), mode="postgresql")
    repository = OperatorLiveRepository(bundle)
    service = OperatorStateService(
        require_live_data=True,
        persistence_mode="postgresql",
        provider_mode="live",
        live_repository=repository,
    )

    envelope = service.get_today(
        role_id="ops-lead",
        tenant_id="tenant-live-1",
    )

    assert envelope["meta"]["dataMode"] == "degraded"
    assert envelope["meta"]["source"] == "operator-shell-production"
    assert envelope["meta"]["dataOrigin"]["kind"] == "degraded"
    assert envelope["meta"]["dataOrigin"]["complete"] is False
    assert envelope["meta"]["liveReadiness"]["ready"] is True
    assert envelope["meta"]["liveReadiness"]["complete"] is False
    assert (
        envelope["meta"]["liveReadiness"]["reasonCode"]
        == "OPERATOR_LIVE_REPOSITORY_DEGRADED"
    )
    assert envelope["meta"]["sections"]["stores"]["state"] == "available"
    assert envelope["meta"]["sections"]["stores"]["recordCount"] == 0
    assert envelope["meta"]["sections"]["listings"]["state"] == "unavailable"
    assert envelope["meta"]["sections"]["listings"]["recordCount"] is None
    assert envelope["meta"]["sections"]["riskRows"]["state"] == "available"
    assert envelope["meta"]["sections"]["riskRows"]["recordCount"] == 0
    assert envelope["workQueue"] == []
    assert envelope["approvals"] == []
    kpis = {item["label"]: item["value"] for item in envelope["kpis"]}
    assert kpis["營運任務"] == "0"
    assert kpis["待核准"] == "0"
    assert kpis["交易淨額"] == "0.00"


def test_live_repository_projects_persisted_rows_and_real_kpis() -> None:
    bundle = replace(_memory_bundle(), mode="postgresql")
    bundle.store_repository.save_store(
        Store(
            store_id="store-live-1",
            tenant_id="tenant-live-1",
            brand_id="brand-live-1",
            store_name="Live Store",
            store_status="open",
        )
    )
    bundle.transaction_repository.save_transaction(
        Transaction(
            transaction_id="txn-live-1",
            store_id="store-live-1",
            net_amount=180.5,
            transaction_status="succeeded",
            source_system="pos-live",
        )
    )
    bundle.forecastops_repository.save_alert(_alert())
    bundle.audit_log.record(
        AuditEvent(
            event_type="forecast.alert.opened",
            actor="forecast-worker",
            action="open",
            resource="forecast-alert/alert-live-1",
            outcome="accepted",
            correlation_id="corr-live-1",
            metadata={"tenant_id": "tenant-live-1"},
        )
    )

    service = OperatorStateService(
        require_live_data=True,
        persistence_mode="postgresql",
        provider_mode="live",
        live_repository=OperatorLiveRepository(bundle),
    )
    envelope = service.get_today(
        role_id="ops-lead",
        tenant_id="tenant-live-1",
    )

    assert [item["id"] for item in envelope["workQueue"]] == ["alert-live-1"]
    assert envelope["notifications"][0]["id"] == "notification-alert-live-1"
    assert envelope["auditFeed"][0]["correlationId"] == "corr-live-1"
    kpis = {item["label"]: item["value"] for item in envelope["kpis"]}
    assert kpis["有效門市"] == "1"
    assert kpis["交易淨額"] == "180.50"
    assert envelope["meta"]["recordCounts"]["transactions"] == 1
    assert envelope["meta"]["source"] == "operator-shell-production"


def test_live_repository_reads_rows_after_process_restart(tmp_path: Any) -> None:
    db_path = tmp_path / "operator-live-restart.sqlite3"
    first = _durable_bundle(db_path)
    first.tenant_repository.save_tenant(
        Tenant(tenant_id="tenant-live-1", tenant_name="Live Tenant")
    )
    first.brand_repository.save_brand(
        Brand(
            brand_id="brand-live-1",
            tenant_id="tenant-live-1",
            brand_code="brand-live",
            brand_name="Live Brand",
        )
    )
    first.address_location_repository.save_address(
        AddressLocation(
            address_id="address-live-1",
            raw_address="Live test address",
        )
    )
    first.store_repository.save_store(
        Store(
            store_id="store-live-1",
            tenant_id="tenant-live-1",
            brand_id="brand-live-1",
            store_name="Live Store",
            store_status="open",
            address_id="address-live-1",
        )
    )
    first.forecastops_repository.save_alert(_alert("alert-restart-1"))
    first.engine.close()

    reopened = _durable_bundle(db_path)
    try:
        repository = OperatorLiveRepository(reopened)
        service = OperatorStateService(
            require_live_data=True,
            persistence_mode="durable",
            provider_mode="live",
            live_repository=repository,
        )

        envelope = service.get_today(
            role_id="ops-lead",
            tenant_id="tenant-live-1",
        )

        assert repository.probe().ready is True
        assert [item["id"] for item in envelope["workQueue"]] == [
            "alert-restart-1"
        ]
        assert envelope["meta"]["dataOrigin"]["sourceId"] == (
            "operator-live-repository"
        )
    finally:
        reopened.engine.close()


def test_create_app_rejects_sqlite_bundle_relabelled_as_postgresql(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    bundle = replace(
        _durable_bundle(tmp_path / "postgres-composition.sqlite3"),
        mode="postgresql",
    )
    provider_validation = SimpleNamespace(ok=True, errors=(), mode="live")

    try:
        app = create_app(
            persistence=bundle,
            external_provider_validation=provider_validation,
        )
        response = Response()
        readiness = _route_for(app, "/readiness").endpoint(response)

        assert app.state.operator_live_repository is None
        assert response.status_code == 503
        assert readiness["details"]["persistence"][
            "production_persistence_supported"
        ] is False
        assert readiness["details"]["data"]["operatorRepositoryReady"] is False
        assert readiness["details"]["data"]["operatorRepositoryProbe"] is None
        assert "SQLITE_NOT_PRODUCTION_PERSISTENCE" in readiness[
            "details"
        ]["data"]["blockingReasons"]
        assert "OPERATOR_LIVE_REPOSITORY_UNAVAILABLE" in readiness[
            "details"
        ]["data"]["blockingReasons"]
        assert "/api/v1/operator/seed/reset" not in app.openapi()["paths"]
        assert "/api/v1/operator/shell/tasks" in app.openapi()["paths"]
        assert "/api/v1/operator/shell/notifications" in app.openapi()["paths"]
    finally:
        bundle.engine.close()


def test_create_app_injects_live_repository_for_production_postgresql(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("ODP_E2E_MODE", raising=False)

    class _ProductionStubEngine:
        is_production = True
        dialect = "postgresql"

        def query(self, *_args: Any, **_kwargs: Any) -> list[Any]:
            return []

        def query_one(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ready": 1}

    from shared.infrastructure.persistence.assisted_listing_intake import (
        DurableAssistedIntakeStore,
    )

    engine = _ProductionStubEngine()
    bundle = replace(
        _memory_bundle(),
        mode="postgresql",
        engine=engine,
        assisted_intake_store=DurableAssistedIntakeStore(SimpleNamespace(engine=engine)),
    )
    app = create_app(
        persistence=bundle,
        external_provider_validation=SimpleNamespace(ok=True, errors=(), mode="live"),
    )

    repository = app.state.operator_live_repository
    assert isinstance(repository, OperatorLiveRepository)
    probe = repository.probe()
    assert probe.ready is True
    assert probe.repository == "OperatorLiveRepository"
    assert probe.persistence_mode == "postgresql"
    assert probe.errors == ()
    assert repository.data_origin["kind"] == "authoritative"
    assert repository.data_origin["persistenceMode"] == "postgresql"


def test_repository_probe_reports_real_dependency_failure() -> None:
    class BrokenStoreRepository:
        def list_stores(self, **_: Any) -> list[Any]:
            raise ConnectionError("database unavailable")

    bundle = replace(
        _memory_bundle(),
        mode="postgresql",
        store_repository=BrokenStoreRepository(),
    )

    probe = OperatorLiveRepository(bundle).probe()

    assert probe.ready is False
    assert probe.errors == (
        "OperatorLiveRepositoryError: stores: ConnectionError: database unavailable",
    )


def test_live_repository_requires_tenant_scope() -> None:
    service = OperatorStateService(
        require_live_data=True,
        persistence_mode="postgresql",
        provider_mode="live",
        live_repository=OperatorLiveRepository(
            replace(_memory_bundle(), mode="postgresql")
        ),
    )

    with pytest.raises(
        OperatorTenantScopeRequiredError,
        match="authorized tenant_id is required",
    ):
        service.get_today(role_id="ops-lead")


def test_live_operator_http_context_applies_verified_tenant_and_store_scope() -> None:
    bundle = replace(_memory_bundle(), mode="postgresql")
    for store in (
        Store(
            store_id="store-a-visible",
            tenant_id="tenant-a",
            brand_id="brand-a",
            store_name="Tenant A Visible",
            store_status="open",
            region_code="north",
        ),
        Store(
            store_id="store-a-hidden-by-scope",
            tenant_id="tenant-a",
            brand_id="brand-a",
            store_name="Tenant A Hidden",
            store_status="open",
            region_code="south",
        ),
        Store(
            store_id="store-b-secret",
            tenant_id="tenant-b",
            brand_id="brand-b",
            store_name="Tenant B Secret",
            store_status="open",
            region_code="north",
        ),
    ):
        bundle.store_repository.save_store(store)
        bundle.transaction_repository.save_transaction(
            Transaction(
                transaction_id=f"txn-{store.store_id}",
                store_id=store.store_id,
                net_amount=100,
                transaction_status="succeeded",
                source_system="pos",
            )
        )

    service = OperatorStateService(
        require_live_data=True,
        persistence_mode="postgresql",
        provider_mode="live",
        live_repository=OperatorLiveRepository(bundle),
    )
    request = _operator_request(
        subject_id="operator-ops-lead",
        roles=Role.OPERATIONS_MANAGER.value,
        tenant_id="tenant-a",
        store_ids="store-a-visible",
    )
    guard = require_operator_permission(
        OPERATOR_CONSOLE_RESOURCE,
        Action.VIEW,
    )
    guard(request)
    context = _live_operator_request_context(request)
    payload = service.get_today(**context)

    assert payload["meta"]["tenantId"] == "tenant-a"
    assert payload["meta"]["recordCounts"]["stores"] == 1
    assert payload["meta"]["recordCounts"]["transactions"] == 1
    kpis = {item["label"]: item["value"] for item in payload["kpis"]}
    assert kpis["有效門市"] == "1"
    assert kpis["交易淨額"] == "100.00"
    assert "Tenant B Secret" not in str(payload)

    missing_tenant = _operator_request(
        subject_id="operator-ops-lead",
        roles=Role.OPERATIONS_MANAGER.value,
    )
    with pytest.raises(HTTPException) as denied:
        guard(missing_tenant)
    assert denied.value.status_code == 403
    assert denied.value.detail == "Operator Console tenant scope is required"


def _operator_request(
    *,
    subject_id: str,
    roles: str,
    tenant_id: str | None = None,
    store_ids: str | None = None,
) -> Request:
    headers = {
        "x-subject-id": subject_id,
        "x-roles": roles,
    }
    if tenant_id is not None:
        headers["x-tenant-id"] = tenant_id
    if store_ids is not None:
        headers["x-store-ids"] = store_ids
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/operator/today",
            "raw_path": b"/api/v1/operator/today",
            "query_string": b"",
            "headers": [
                (name.encode("latin-1"), value.encode("latin-1"))
                for name, value in headers.items()
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443),
        }
    )


def test_two_tenant_isolation_prevents_foreign_record_leakage_and_false_completeness(
    tmp_path: Any,
) -> None:
    db_path = tmp_path / "two_tenant_isolation.sqlite3"
    bundle = _durable_bundle(db_path)

    try:
        bundle.tenant_repository.save_tenant(
            Tenant(tenant_id="tenant-a", tenant_name="Tenant A")
        )
        bundle.tenant_repository.save_tenant(
            Tenant(tenant_id="tenant-b", tenant_name="Tenant B")
        )
        bundle.brand_repository.save_brand(
            Brand(
                brand_id="brand-a",
                tenant_id="tenant-a",
                brand_code="brand-a",
                brand_name="Brand A",
            )
        )
        bundle.brand_repository.save_brand(
            Brand(
                brand_id="brand-b",
                tenant_id="tenant-b",
                brand_code="brand-b",
                brand_name="Brand B",
            )
        )
        address_a = AddressLocation(address_id="addr-a", raw_address="Address A")
        address_b = AddressLocation(address_id="addr-b", raw_address="Address B")
        bundle.address_location_repository.save_address(address_a)
        bundle.address_location_repository.save_address(address_b)

        bundle.store_repository.save_store(
            Store(
                store_id="store-tenant-a",
                tenant_id="tenant-a",
                brand_id="brand-a",
                store_name="Tenant A Store",
                store_status="open",
                address_id="addr-a",
            )
        )
        bundle.store_repository.save_store(
            Store(
                store_id="store-tenant-b",
                tenant_id="tenant-b",
                brand_id="brand-b",
                store_name="Tenant B Store",
                store_status="open",
                address_id="addr-b",
            )
        )

        from modules.external_data.application.ingestion_store import IngestionRunRecord
        from modules.heatzone.domain import HeatZoneScoreResult
        from modules.heatzone.workers import HeatZoneBatchScoreResult
        from modules.listing.domain.models import ListingDedupKey
        from shared.workflow.sitescore import SiteScoreDecision

        listing_a = Listing(
            listing_id="listing-tenant-a",
            address_id="addr-a",
        )
        key_a = ListingDedupKey(
            source_id="src-a",
            source_listing_id="list-a",
            normalized_address="addr-a",
            rent_amount=1000.0,
            area_ping=30.0,
        )
        repo_a = bundle.listing_repository_for_tenant("tenant-a")
        assert repo_a is not None
        repo_a.save_listing(listing_a, address_a, key_a)

        from shared.workflow.sitescore import DecisionStatus, SiteScoreRecommendation, SiteScoreDecision

        decision_a = SiteScoreDecision(
            decision_id="dec-tenant-a",
            candidate_site_id="cand-a",
            report_id="rep-a",
            report_version=1,
            recommendation=SiteScoreRecommendation.GO,
            status=DecisionStatus.APPROVED,
            policy_version="v1",
            model_version="v1",
            created_by="user-a",
            created_at=datetime.now(UTC),
        )
        decision_store_a = bundle.sitescore_decision_store_for_tenant("tenant-a")
        assert decision_store_a is not None
        decision_store_a.save_decision(decision_a)

        from modules.external_data.workers.scheduled_fetch import SourceFreshnessEvidence

        now = datetime.now(UTC)
        ingestion_a = IngestionRunRecord(
            run_id="run-tenant-a",
            provider_id="prov-a",
            schedule_id="sched-a",
            trigger="scheduled",
            idempotency_key="key-a",
            status="completed",
            data_status="fresh",
            window_start=now,
            window_end=now,
            started_at=now,
            completed_at=now,
            raw_snapshot_id="raw-a",
            canonical_snapshot_id="can-a",
            source_snapshot_id="src-a",
            provider_observed_at=now,
            ingested_at=now,
            last_success_watermark_before=now,
            last_success_watermark_after=now,
            correlation_id="corr-a",
            accepted_count=10,
            quarantined_count=0,
            total_count=10,
            freshness=SourceFreshnessEvidence(
                provider_id="prov-a",
                source_snapshot_id="src-a",
                data_status="fresh",
                provider_observed_at=now,
                ingested_at=now,
                freshness_sla_seconds=86400,
                correlation_id="corr-a",
            ),
        )
        ingestion_store_a = bundle.ingestion_run_store_for_tenant("tenant-a")
        assert ingestion_store_a is not None
        ingestion_store_a.save(ingestion_a)

        from modules.heatzone.domain import HeatZoneState

        hz_result_a = HeatZoneBatchScoreResult(
            job_id="job-tenant-a",
            status="completed",
            scores=(
                HeatZoneScoreResult(
                    heat_zone_id="hz-a",
                    h3_index="8928308280fffff",
                    h3_resolution=9,
                    score=0.9,
                    priority_rank=1,
                    unmet_demand_score=0.9,
                    format_fit_score=0.9,
                    cannibalization_risk_score=0.1,
                    rent_feasibility_score=0.8,
                    listing_availability_score=0.9,
                    confidence=0.95,
                    state=HeatZoneState.UNTOUCHED,
                    feature_snapshot_time=now,
                    prediction_origin_time=now,
                    last_scored_at=now,
                    model_version="v1",
                    feature_version="v1",
                    source_snapshot_ids=("src-a",),
                    reasons=("high_density",),
                ),
            ),
            completed_at=now,
        )
        heatzone_store_a = bundle.heatzone_store_for_tenant("tenant-a")
        assert heatzone_store_a is not None
        heatzone_store_a.put(hz_result_a)

        live_repo = OperatorLiveRepository(bundle)

        state_a = live_repo.load_state(
            tenant_id="tenant-a",
            store_ids=("store-tenant-a",),
        )
        sections_a = state_a["_meta"]["sections"]
        assert sections_a["listings"]["state"] == "available"
        assert sections_a["listings"]["recordCount"] == 1
        assert sections_a["siteScoreDecisions"]["state"] == "available"
        assert sections_a["siteScoreDecisions"]["recordCount"] == 1
        assert sections_a["ingestionRuns"]["state"] == "available"
        assert sections_a["ingestionRuns"]["recordCount"] == 1
        assert sections_a["heatZones"]["state"] == "available"
        assert sections_a["heatZones"]["recordCount"] == 1

        state_b = live_repo.load_state(
            tenant_id="tenant-b",
            store_ids=("store-tenant-b",),
        )
        sections_b = state_b["_meta"]["sections"]
        assert sections_b["listings"]["recordCount"] == 0
        assert sections_b["siteScoreDecisions"]["recordCount"] == 0
        assert sections_b["ingestionRuns"]["recordCount"] == 0
        assert sections_b["heatZones"]["recordCount"] == 0

        assert "listing-tenant-a" not in str(state_b)
        assert "dec-tenant-a" not in str(state_b)
        assert "run-tenant-a" not in str(state_b)
        assert "job-tenant-a" not in str(state_b)

        # Confirm tenant-a data remains intact after tenant-b read
        state_a_again = live_repo.load_state(
            tenant_id="tenant-a",
            store_ids=("store-tenant-a",),
        )
        sections_a_again = state_a_again["_meta"]["sections"]
        assert sections_a_again["listings"]["recordCount"] == 1
        assert sections_a_again["siteScoreDecisions"]["recordCount"] == 1
        assert sections_a_again["ingestionRuns"]["recordCount"] == 1
        assert sections_a_again["heatZones"]["recordCount"] == 1
    finally:
        bundle.engine.close()


def test_unpartitioned_in_memory_stores_remain_unavailable() -> None:
    bundle = _memory_bundle()
    live_repo = OperatorLiveRepository(bundle)

    state = live_repo.load_state(tenant_id="tenant-test")
    sections = state["_meta"]["sections"]
    for section_name in (
        "listings",
        "candidates",
        "siteScoreDecisions",
        "ingestionRuns",
        "heatZones",
    ):
        assert sections[section_name]["state"] == "unavailable"
        assert "OPERATOR_TENANT_" in sections[section_name]["reasonCode"]

    assert state["_meta"]["dataOrigin"]["complete"] is False
