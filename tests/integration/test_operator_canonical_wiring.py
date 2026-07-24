from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from modules.avm import (
    AVMProductionExecutor,
    AVMService,
    LiquidityArtifactEvidence,
    ValuationInput,
)
from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
from modules.netplan import (
    ExistingStoreInput,
    NetPlanProductionExecutor,
    NetPlanService,
)
from modules.opsboard.application.network_scoring import NetworkScoringService
from modules.opsboard.application.operator_live_repository import (
    OperatorLiveRepository,
)
from modules.priceops import (
    PriceConstraints,
    PriceElasticityEstimate,
    PriceOpsService,
    PricingPlanItem,
)
from modules.sitescore.application.reporting import SiteScoreReportService
from shared.domain import AddressLocation, CandidateSite, Listing
from shared.infrastructure.persistence import (
    DurableAVMRepository,
    DurableListingRepository,
    DurableNetPlanRepository,
    DurablePriceOpsRepository,
    DurableSiteScoreRepository,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableDecisionStore
from shared.workflow.sitescore import SiteScoreDecisionWorkflow
from solver.netplan import NetPlanConstraints

BASE = "/api/v1/operator"


def _headers(tenant_id: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "x-subject-id": f"operator-{tenant_id}",
        "x-roles": "operations_manager,site_reviewer,expansion_user",
        "x-operator-role": "siteReviewer",
        "x-tenant-id": tenant_id,
        "x-correlation-id": f"corr-{tenant_id}",
    }
    if idempotency_key:
        headers["idempotency-key"] = idempotency_key
    return headers


class RecordingSiteScoreRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def infer(
        self,
        *,
        service: str,
        rows: list[dict[str, Any]],
        expected_feature_schema_version: str,
    ) -> Any:
        self.calls.append(
            {
                "service": service,
                "rows": rows,
                "feature_schema_version": expected_feature_schema_version,
            }
        )
        points = tuple(320_000.0 + index * 10_000 for index in range(len(rows)))
        audit_metadata = {
            "model_id": f"{service}:mlflow-production-test",
            "model_version": f"{service}:mlflow-production-test",
            "model_engine": "lightgbm.LGBMRegressor",
            "artifact_sha256": "sha256:" + "a" * 64,
        }
        return SimpleNamespace(
            binding=SimpleNamespace(model_id=f"{service}:mlflow-production-test"),
            point=points,
            lower=tuple(value * 0.9 for value in points),
            upper=tuple(value * 1.1 for value in points),
            engine="lightgbm.LGBMRegressor",
            artifact_sha256="sha256:" + "a" * 64,
            to_audit_metadata=lambda: audit_metadata,
        )


class RecordingLiquidityRuntime:
    feature_names = ("quality_score", "liquidity_discount")

    def __init__(self) -> None:
        self.calls: list[dict[str, float]] = []

    def predict(self, features: dict[str, float]) -> Any:
        self.calls.append(features)
        values = {
            "sale_probability_30d": 0.42,
            "sale_probability_90d": 0.78,
            "expected_days": 58.0,
        }
        return SimpleNamespace(
            **values,
            to_dict=lambda: values,
        )


class CanonicalHarness:
    def __init__(self, database_path: Path, runtime: Any | None = None) -> None:
        self.bundle = _durable_bundle(database_path)
        self.document_store = SqliteDocumentStore(self.bundle.engine)
        self.runtime = runtime or RecordingSiteScoreRuntime()
        self.liquidity_runtime = RecordingLiquidityRuntime()
        self.avm_executor = AVMProductionExecutor(
            model_runtime=self.runtime,
            liquidity_runtime=self.liquidity_runtime,
            liquidity_evidence=LiquidityArtifactEvidence(
                artifact_uri="gs://models/operator-avm-liquidity.json",
                artifact_sha256="sha256:" + "b" * 64,
                model_version="liquidity-test-v1",
                approved_by="model-risk-test",
                approved_at=datetime(2026, 7, 24, tzinfo=UTC),
                dataset_snapshot_id="liquidity-snapshot-test",
            ),
        )
        self.netplan_executor = NetPlanProductionExecutor()

    def scoped(self, tenant_id: str) -> TenantScopedDocumentStore:
        return TenantScopedDocumentStore(self.document_store, tenant_id)

    def listing(self, tenant_id: str) -> DurableListingRepository:
        return DurableListingRepository(self.scoped(tenant_id))

    def sitescore(self, tenant_id: str) -> DurableSiteScoreRepository:
        return DurableSiteScoreRepository(self.scoped(tenant_id))

    def decisions(self, tenant_id: str) -> DurableDecisionStore:
        return DurableDecisionStore(self.scoped(tenant_id))

    def avm(self, tenant_id: str) -> DurableAVMRepository:
        return DurableAVMRepository(self.scoped(tenant_id))

    def netplan(self, tenant_id: str) -> DurableNetPlanRepository:
        return DurableNetPlanRepository(self.scoped(tenant_id))

    def priceops(self, tenant_id: str) -> DurablePriceOpsRepository:
        return DurablePriceOpsRepository(self.scoped(tenant_id))

    def app(self) -> FastAPI:
        app = FastAPI()
        app.state.job_queue = self.bundle.job_queue

        @app.middleware("http")
        async def correlation_id(request: Request, call_next: Any) -> Any:
            request.state.correlation_id = request.headers.get(
                "x-correlation-id",
                "corr-canonical-test",
            )
            return await call_next(request)

        app.include_router(
            create_operator_router(
                audit_log=self.bundle.audit_log,
                document_store=self.document_store,
                listing_repository_for_tenant=self.listing,
                sitescore_repository_for_tenant=self.sitescore,
                sitescore_decision_repository_for_tenant=self.decisions,
                avm_repository_for_tenant=self.avm,
                netplan_repository_for_tenant=self.netplan,
                priceops_repository_for_tenant=self.priceops,
                model_runtime=self.runtime,
                avm_production_executor=self.avm_executor,
                netplan_production_executor=self.netplan_executor,
                live_repository=OperatorLiveRepository(self.bundle),
                require_live_data=True,
                persistence_mode="postgresql",
                provider_mode="live",
            ),
            prefix="/api/v1",
        )
        return app

    def close(self) -> None:
        self.bundle.engine.close()


def _seed_candidate(harness: CanonicalHarness, tenant_id: str) -> str:
    address = AddressLocation(
        address_id="address-live-1",
        raw_address="台北市信義區測試路 1 號",
        normalized_address="台北市信義區測試路1號",
        city="台北市",
        district="信義區",
        latitude=25.033,
        longitude=121.565,
        geocode_confidence=0.96,
        h3_res_9="892000000000001",
    )
    listing = Listing(
        listing_id="listing-live-1",
        source_listing_id="source-live-1",
        source_id="approved-feed",
        address_id=address.address_id,
        rent_amount=120_000,
        area_ping=35,
        floor="1F",
        frontage_m=5.5,
        snapshot_id="snapshot-live-1",
        confidence=0.94,
    )
    candidate = CandidateSite(
        candidate_site_id="candidate-live-1",
        listing_id=listing.listing_id,
        address_id=address.address_id,
        target_format_code="ODAY_G2",
        created_by="integration-test",
    )
    repository = harness.listing(tenant_id)
    repository.save_listing(
        listing,
        address,
        ListingDedupKey(
            source_id=listing.source_id,
            source_listing_id=listing.source_listing_id,
            normalized_address=address.normalized_address,
            rent_amount=listing.rent_amount,
            area_ping=listing.area_ping,
        ),
    )
    repository.save_candidate(
        CandidateSiteDraft(
            listing=listing,
            address=address,
            candidate_site=candidate,
            heat_zone_id=address.h3_res_9,
            listing_source=listing.source_id,
            model_version="",
            dataset_snapshot_id=listing.snapshot_id,
        )
    )
    return candidate.candidate_site_id


def _seed_rebalance_inputs(
    harness: CanonicalHarness,
    tenant_id: str,
) -> tuple[str, str]:
    store_id = "store-live-1"
    case = AVMService(repository=harness.avm(tenant_id)).create_case(
        ValuationInput(
            store_id=store_id,
            gm_ttm=1_200_000,
            forecast_gm_next_12m=1_350_000,
            asset_book_value=800_000,
            equipment_fair_value=650_000,
            lease_liability=150_000,
            working_capital=80_000,
            comparable_multiples=(2.1, 2.4, 2.7),
            source_snapshot_ids=("finance-snapshot-live-1",),
        ),
        created_by="finance-live",
        correlation_id="corr-avm-live",
    )
    scenario = NetPlanService(
        repository=harness.netplan(tenant_id)
    ).create_scenario(
        tenant_id=tenant_id,
        scenario_name="store-live-1 rebalance",
        planning_horizon="2026-H2",
        existing_stores=[
            ExistingStoreInput(
                store_id=store_id,
                baseline_gross_margin=1_200_000,
                improve_gross_margin_uplift=240_000,
                improve_cost=120_000,
                move_gross_margin_uplift=400_000,
                move_cost=280_000,
                exit_cost=90_000,
                source_snapshot_ids=("network-snapshot-live-1",),
            )
        ],
        constraints=NetPlanConstraints(
            max_budget=500_000,
            min_expected_gross_margin=900_000,
            max_average_risk=0.8,
        ),
        correlation_id="corr-netplan-live",
    )
    assert case.store_id == store_id
    return store_id, scenario.scenario_id


def _seed_priceops(harness: CanonicalHarness, tenant_id: str) -> str:
    service = PriceOpsService(repository=harness.priceops(tenant_id))
    plan = service.create_plan(
        tenant_id=tenant_id,
        items=[
            PricingPlanItem.create(
                store_id="store-live-1",
                machine_type="washer-20kg",
                constraints=PriceConstraints(
                    unit_cost=30,
                    current_price=60,
                    margin_floor_ratio=0.2,
                    max_increase_pct=0.2,
                    max_decrease_pct=0.2,
                    price_ladder_step=5,
                ),
                baseline_demand=1_000,
                elasticity=PriceElasticityEstimate(
                    elasticity_value=-1.2,
                    confidence=0.85,
                ),
            )
        ],
        correlation_id="corr-priceops-live",
    )
    service.simulate(plan.plan_id)
    service.optimize(plan.plan_id)
    return plan.plan_id


def test_scoring_invokes_canonical_runtime_persists_and_isolates_tenant(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    database_path = tmp_path / "operator-canonical-score.sqlite3"
    runtime = RecordingSiteScoreRuntime()
    first = CanonicalHarness(database_path, runtime)
    candidate_id = _seed_candidate(first, "tenant-a")
    monkeypatch.setattr(
        NetworkScoringService,
        "_build_scorecard",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("canonical scoring must not use _build_scorecard")
        ),
    )
    try:
        with TestClient(first.app()) as client:
            snapshot = client.get(
                f"{BASE}/network-scoring",
                headers=_headers("tenant-a"),
            )
            scored = client.post(
                f"{BASE}/network-scoring/candidates/{candidate_id}/score",
                headers=_headers("tenant-a", idempotency_key="score-live-1"),
                json={"actorRoleId": "siteReviewer", "actorName": "Reviewer A"},
            )
            other_tenant = client.get(
                f"{BASE}/network-scoring",
                headers=_headers("tenant-b"),
            )
        assert snapshot.status_code == 200, snapshot.text
        assert [row["id"] for row in snapshot.json()["candidates"]] == [candidate_id]
        assert scored.status_code == 200, scored.text
        assert runtime.calls[0]["service"] == "sitescore"
        assert runtime.calls[0]["rows"][0]["candidate_site_id"] == candidate_id
        assert (
            scored.json()["scorecard"]["modelVersion"]
            == "sitescore:mlflow-production-test"
        )
        assert first.sitescore("tenant-a").latest(candidate_id) is not None
        assert other_tenant.status_code == 200, other_tenant.text
        assert other_tenant.json()["candidates"] == []
        assert first.sitescore("tenant-b").latest(candidate_id) is None
    finally:
        first.close()

    reopened = CanonicalHarness(database_path, runtime)
    try:
        with TestClient(reopened.app()) as client:
            snapshot = client.get(
                f"{BASE}/network-scoring",
                headers=_headers("tenant-a"),
            )
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["scorecards"][0]["id"] == candidate_id
        assert snapshot.json()["scorecards"][0]["reportVersion"] == 1
    finally:
        reopened.close()


def test_rebalance_invokes_avm_and_netplan_oss_and_persists_results(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    database_path = tmp_path / "operator-canonical-rebalance.sqlite3"
    harness = CanonicalHarness(database_path)
    store_id, scenario_id = _seed_rebalance_inputs(harness, "tenant-a")
    monkeypatch.setattr(
        "modules.opsboard.application.network_rebalance._seed_scenarios",
        lambda: (_ for _ in ()).throw(
            AssertionError("canonical rebalance must not use _seed_scenarios")
        ),
    )
    avm_case_id = ""
    try:
        with TestClient(harness.app()) as client:
            requested = client.post(
                f"{BASE}/network-rebalance/stores/{store_id}/avm/request",
                headers=_headers("tenant-a", idempotency_key="avm-request-1"),
                json={"actorRoleId": "operationsManager"},
            )
            completed = client.post(
                f"{BASE}/network-rebalance/stores/{store_id}/avm/complete",
                headers=_headers("tenant-a", idempotency_key="avm-complete-1"),
                json={"actorRoleId": "operationsManager"},
            )
            solved = client.post(
                f"{BASE}/network-rebalance/stores/{store_id}/netplan/solve",
                headers=_headers("tenant-a", idempotency_key="netplan-solve-1"),
                json={"actorRoleId": "operationsManager"},
            )
        assert requested.status_code == 200, requested.text
        assert completed.status_code == 200, completed.text
        assert completed.json()["store"]["avm"]["reportId"].startswith("avm-report-")
        assert solved.status_code == 200, solved.text
        assert solved.json()["store"]["netPlanJob"]["id"] == scenario_id
        durable_solve = harness.netplan("tenant-a").get_solve(scenario_id)
        assert durable_solve is not None
        assert durable_solve.result.solver_version == "netplan-ortools-cp-sat-v2"
        assert durable_solve.result.selected_actions
        assert set(durable_solve.execution_metadata["engines"]) == {
            "authoritative",
            "frontier",
            "robust",
        }
        assert any(call["service"] == "avm" for call in harness.runtime.calls)
        assert harness.liquidity_runtime.calls
        avm_case_id = requested.json()["store"]["canonicalAvmCaseId"]
        assert harness.avm("tenant-a").latest_report(avm_case_id) is not None
    finally:
        harness.close()

    reopened = CanonicalHarness(database_path)
    try:
        assert reopened.avm("tenant-a").latest_report(avm_case_id) is not None
        assert reopened.netplan("tenant-a").get_solve(scenario_id) is not None
        assert reopened.netplan("tenant-b").get_solve(scenario_id) is None
    finally:
        reopened.close()


def test_growth_and_governance_aggregate_canonical_priceops_and_decisions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "operator-canonical-aggregate.sqlite3"
    harness = CanonicalHarness(database_path)
    plan_id = _seed_priceops(harness, "tenant-a")
    candidate_id = _seed_candidate(harness, "tenant-a")
    report_service = SiteScoreReportService(
        repository=harness.sitescore("tenant-a"),
        model_runtime=harness.runtime,
        require_production_model=True,
    )
    report = report_service.score_candidates(
        [
            {
                "candidate_site_id": candidate_id,
                "monthly_rent": 120_000,
                "area_ping": 35,
                "frontage_m": 5.5,
                "average_confidence": 0.9,
                "data_quality_score": 0.9,
                "source_snapshot_ids": ["snapshot-live-1"],
            }
        ]
    )[0]
    decision_store = harness.decisions("tenant-a")
    decision = SiteScoreDecisionWorkflow(store=decision_store).open_decision(
        report,
        created_by="system-live",
        correlation_id="corr-decision-live",
    )
    try:
        with TestClient(harness.app()) as client:
            segments = client.get(
                f"{BASE}/growth/segments",
                headers=_headers("tenant-a"),
            )
            recommendations = client.get(
                f"{BASE}/growth/recommendations",
                headers=_headers("tenant-a"),
            )
            reviews = client.get(
                f"{BASE}/network-reviews",
                headers=_headers("tenant-a"),
            )
            action = (
                report.recommendation.value
                if report.recommendation.value in {"GO", "WAIT", "REJECT"}
                else "RETURN"
            )
            decision_payload: dict[str, Any] = {
                "decision": action,
                "reason": "canonical decision verified by integration reviewer",
                "overrideAck": True,
                "actorRoleId": "siteReviewer",
                "actorName": "Reviewer A",
            }
            if action == "WAIT":
                decision_payload["conditions"] = "re-score after new evidence"
            if action == "RETURN":
                decision_payload["requiredData"] = ["address"]
            decided = client.post(
                f"{BASE}/network-reviews/{decision.decision_id}/decide",
                headers=_headers(
                    "tenant-a",
                    idempotency_key="decision-live-1",
                ),
                json=decision_payload,
            )
            governance = client.get(
                f"{BASE}/governance/snapshot",
                headers=_headers("tenant-a"),
            )
        assert segments.status_code == 200, segments.text
        assert segments.json()["items"][0]["planId"] == plan_id
        assert recommendations.status_code == 200, recommendations.text
        assert recommendations.json()["items"][0]["solverVersion"]
        assert reviews.status_code == 200, reviews.text
        assert reviews.json()["reviews"][0]["candidateId"] == candidate_id
        assert decided.status_code == 200, decided.text
        assert (
            harness.decisions("tenant-a")
            .get_decision(decision.decision_id)
            .status.value
            in {"APPROVED", "REJECTED", "DRAFT"}
        )
        assert governance.status_code == 200, governance.text
        assert governance.json()["source"] == "canonical"
        assert {row["module"] for row in governance.json()["approvals"]} >= {
            "SiteScore",
            "PriceOps",
        }
        serialized = str(governance.json())
        assert "PriceOps-v0.9" not in serialized
        assert "growth-2026-W27" not in serialized
    finally:
        harness.close()

    reopened = CanonicalHarness(database_path)
    try:
        persisted = reopened.decisions("tenant-a").get_decision(
            decision.decision_id
        )
        assert persisted is not None
        assert persisted.history
        assert (
            reopened.decisions("tenant-b").get_decision(decision.decision_id)
            is None
        )
    finally:
        reopened.close()


def test_live_canonical_dependencies_fail_closed_instead_of_empty_success(
    tmp_path: Path,
) -> None:
    bundle = _durable_bundle(tmp_path / "operator-canonical-missing.sqlite3")
    app = FastAPI()
    app.include_router(
        create_operator_router(
            audit_log=bundle.audit_log,
            document_store=SqliteDocumentStore(bundle.engine),
            live_repository=OperatorLiveRepository(bundle),
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
        ),
        prefix="/api/v1",
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                f"{BASE}/network-scoring",
                headers=_headers("tenant-a"),
            )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == (
            "OPERATOR_CANONICAL_DEPENDENCY_UNAVAILABLE"
        )
        assert response.json()["detail"]["dependency"] == "listing_repository"
    finally:
        bundle.engine.close()
