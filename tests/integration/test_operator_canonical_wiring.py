from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import uvloop
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from models.shared_ml.output_contracts import SITESCORE_OUTPUT_TRANSFORM
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
from modules.opsboard.application.network_listings import NetworkListingService
from modules.opsboard.application.network_scoring import NetworkScoringService
from modules.opsboard.application.operator_live_repository import (
    OperatorLiveRepository,
)
from modules.priceops import (
    PlanStatus,
    PriceConstraints,
    PriceElasticityEstimate,
    PriceOpsService,
    PricingPlanItem,
)
from modules.sitescore.application.candidate_features import feature_from_candidate
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
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import DurableDecisionStore
from shared.workflow.sitescore import SiteScoreDecisionWorkflow
from solver.netplan import NetPlanConstraints
from tests.intake_registry_fixture import APPROVER, MANAGER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle as _postgres_bundle
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)

BASE = "/api/v1/operator"
_CONVERTIBLE_LISTING_ID = "00000000-0000-0000-0000-000000000501"


@pytest.fixture(autouse=True)
def _use_production_event_loop_policy() -> Any:
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


def _headers(tenant_id: str, *, idempotency_key: str | None = None, subject: str | None = None) -> dict[str, str]:
    headers = {
        "x-subject-id": subject or f"operator-{tenant_id}",
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
            model_metadata={"output_transform": dict(SITESCORE_OUTPUT_TRANSFORM)},
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
    def __init__(self, database_path: Path | None, runtime: Any | None = None, *, bundle: Any = None) -> None:
        self.bundle = bundle if bundle is not None else _durable_bundle(database_path)
        self.document_store = (
            bundle.assisted_intake_store._store if bundle is not None
            else SqliteDocumentStore(self.bundle.engine)
        )
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

    def app(self, *, priceops_scope_masters: bool = False) -> FastAPI:
        app = FastAPI()
        app.state.job_queue = self.bundle.job_queue
        app.state.persistence_bundle = self.bundle

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
                intake_workflow_store=getattr(self.bundle, "assisted_intake_store", None),
                intake_workflow_policy=getattr(self.bundle, "assisted_intake_workflow_policy", None),
                sitescore_repository_for_tenant=self.sitescore,
                sitescore_decision_repository_for_tenant=self.decisions,
                avm_repository_for_tenant=self.avm,
                netplan_repository_for_tenant=self.netplan,
                priceops_repository_for_tenant=self.priceops,
                priceops_store_repository=self.bundle.store_repository if priceops_scope_masters else None,
                priceops_brand_repository=self.bundle.brand_repository if priceops_scope_masters else None,
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
        if getattr(self.bundle, "assisted_intake_workflow_policy", None) is not None:
            from apps.api.app.routes.listings import create_assisted_intake_router

            app.include_router(create_assisted_intake_router(
                self.bundle.assisted_intake_store,
                workflow_policy=self.bundle.assisted_intake_workflow_policy,
            ), prefix="/api/v1")
        return app

    def close(self) -> None:
        self.bundle.engine.close()


def _postgres_listing_harness(database, monkeypatch, runtime=None):
    """Exercise the production resolver with its required same-engine workflow.

    The older SQLite harness cannot emulate PostgreSQL advisory locks or the
    canonical registry. Only the listing/candidate cases below use this real
    database; their SiteScore runtime remains an explicit recording double.
    """
    bundle = _postgres_bundle(database, monkeypatch)
    monkeypatch.setenv("ODP_INTAKE_SCORE_ATTEMPT_SIGNING_KEY", "synthetic-canonical-writer-test-signing-key")
    monkeypatch.setenv("ODP_INTAKE_SCORE_RETRY_POLICY_JSON", json.dumps({
        "policy_id": "synthetic-canonical-writer-retry", "version": 1,
        "max_attempts": 5, "backoff_seconds": [10, 30, 120, 600],
    }))
    # This suite persists feature timestamps as datetime objects. The bridge
    # fixture's local Clock subclass cannot be pickled, and this suite has no
    # Intake/SLA wall-clock assertions requiring that synthetic clock.
    monkeypatch.setattr("modules.opsboard.application.network_listings.datetime", datetime)
    monkeypatch.setattr("apps.api.app.routes.listings.datetime", datetime)
    assert bundle.engine.dialect == "postgresql"
    assert bundle.assisted_intake_store._engine is bundle.engine
    assert bundle.assisted_intake_workflow_policy is not None
    return CanonicalHarness(None, runtime, bundle=bundle)


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
    # Local synthetic measured verdict; this is not a legacy data backfill.
    object.__setattr__(listing, "hard_rule_failures", [])
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
            prior_90d_cell_net_revenue=180_000.0,
            prior_90d_cell_transaction_count=30,
            prior_90d_cell_store_count=1,
            feature_snapshot_time=datetime.now(UTC),
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
            quality_score=0.95,
            source_snapshot_ids=("finance-snapshot-live-1",),
            equipment_depreciation_basis="appraised_fair_value",
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


def _seed_priceops(
    harness: CanonicalHarness,
    tenant_id: str,
    *,
    stage: PlanStatus = PlanStatus.OPTIMIZED,
) -> str:
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
    if stage is PlanStatus.CANDIDATE:
        return plan.plan_id
    service.simulate(plan.plan_id)
    if stage is PlanStatus.SIMULATED:
        return plan.plan_id
    assert stage is PlanStatus.OPTIMIZED
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
        execution = SiteScoreReportService(repository=first.sitescore("tenant-a"), model_runtime=runtime,
            require_production_model=True, runtime_mode="test").score_candidates_with_execution([
                feature_from_candidate(first.listing("tenant-a").list_candidates()[0], tenant_id="tenant-a"),
            ])
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
        assert scored.status_code == 503, scored.text
        assert scored.json()["detail"]["message"] == "SITESCORE_EXECUTION_OWNER_UNAVAILABLE"
        assert len(runtime.calls) == 1
        assert runtime.calls[0]["service"] == "sitescore"
        assert runtime.calls[0]["rows"][0]["tenant_id"] == "tenant-a"
        assert runtime.calls[0]["rows"][0]["h3_index"] == "892000000000001"
        assert runtime.calls[0]["rows"][0]["view_version"] == "candidate-site-view-v2"
        assert (
            execution.reports[0].model_version
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
        solved_scenarios = solved.json()["store"]["netPlanScenarios"]
        assert len(solved_scenarios) > 0
        assert "isStale" in solved_scenarios[0]
        assert "isInfeasible" in solved_scenarios[0]
        assert "diagnostics" in solved_scenarios[0]
        assert "modelledConstraintClasses" in solved_scenarios[0]
        assert "unmodelledConstraintClasses" in solved_scenarios[0]
        assert solved_scenarios[0]["modelledConstraintClasses"] == ["CAPITAL"]
        assert "LEASE" in solved_scenarios[0]["unmodelledConstraintClasses"]
        assert "SEQUENCING" in solved_scenarios[0]["unmodelledConstraintClasses"]
        assert solved_scenarios[0]["modelled_constraint_classes"] == ["CAPITAL"]
        assert "LEASE" in solved_scenarios[0]["unmodelled_constraint_classes"]
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


def _rebalance_row(payload: dict[str, Any], store_id: str) -> dict[str, Any]:
    row = next(
        (item for item in payload["stores"] if item["storeId"] == store_id),
        None,
    )
    assert row is not None, f"{store_id} missing from rebalance snapshot"
    return row


def _strip_quality_status(item: Any) -> Any:
    """Reproduce a record pickled before ``quality_score_status`` existed.

    ``__init__`` always writes the field, so deleting it from the instance dict
    is what unpickling a previous-release record actually yields -- as opposed
    to constructing a fresh object that merely left the argument out, which is
    a measured input and must not be treated as legacy.
    """

    for field_name in ("quality_score_status", "quality_disposition"):
        if field_name in item.__dict__:
            object.__delattr__(item, field_name)
    return item


def _seed_additional_rebalance_case(
    harness: CanonicalHarness,
    tenant_id: str,
    store_id: str,
) -> str:
    """Seed a second, independently measured AVM case for the same tenant."""

    case = AVMService(repository=harness.avm(tenant_id)).create_case(
        ValuationInput(
            store_id=store_id,
            gm_ttm=1_100_000,
            forecast_gm_next_12m=1_250_000,
            asset_book_value=780_000,
            equipment_fair_value=620_000,
            lease_liability=140_000,
            working_capital=70_000,
            comparable_multiples=(2.0, 2.3, 2.6),
            quality_score=0.93,
            source_snapshot_ids=(f"finance-snapshot-{store_id}",),
            equipment_depreciation_basis="appraised_fair_value",
        ),
        created_by="finance-live",
        correlation_id=f"corr-avm-{store_id}",
    )
    return case.case_id


def _completions_for(payload: dict[str, Any], store_id: str) -> list[dict[str, Any]]:
    return [
        event
        for event in payload["auditEvents"]
        if event.get("action") == "rebalance.avm.completed"
        and event.get("targetId") == store_id
    ]


def _replay_avm_completion(
    client: TestClient,
    store_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Re-POST a finished AVM completion under its original Idempotency-Key.

    A 200 here is only reachable through the durable idempotency cache: the
    store is already ``avmready``, so a request that fell through to the action
    itself would be refused as a conflict. That is what makes this the response
    shape a real client retry gets back, rather than a fresh valuation.
    """

    replayed = client.post(
        f"{BASE}/network-rebalance/stores/{store_id}/avm/complete",
        headers=_headers("tenant-a", idempotency_key=idempotency_key),
        json={"actorRoleId": "operationsManager"},
    )
    assert replayed.status_code == 200, replayed.text
    return replayed.json()


def test_durable_rebalance_card_cannot_outlive_the_quality_claim_it_was_written_with(
    tmp_path: Path,
) -> None:
    """A restored AVM card must re-derive its quality claim from its own report.

    The Operator card is a projection persisted in rebalance state, so a restart
    brings back whatever confidence was written when the valuation completed.
    That is the whole failure: a card produced before quality-score handling
    keeps advertising high confidence even though its case cannot tell a
    measured perfect score from an omitted one. The measured card must survive
    the same restart untouched, or the downgrade would just be a blanket
    pessimism that says nothing.

    The idempotency cache is durable too, and is restored as a separate copy
    that no read path passes through. A client still holding the original key
    therefore has a second way to ask for the same card -- one that has to reach
    the same answer as the snapshot, or the downgrade is only cosmetic and the
    replay becomes the response that still claims the retired quality.
    """

    database_path = tmp_path / "operator-canonical-rebalance-quality.sqlite3"
    harness = CanonicalHarness(database_path)
    store_id, _ = _seed_rebalance_inputs(harness, "tenant-a")
    fresh_store_id = "store-live-fresh-1"
    fresh_case_id = _seed_additional_rebalance_case(harness, "tenant-a", fresh_store_id)
    try:
        with TestClient(harness.app()) as client:
            requested = client.post(
                f"{BASE}/network-rebalance/stores/{store_id}/avm/request",
                headers=_headers("tenant-a", idempotency_key="avm-request-quality-1"),
                json={"actorRoleId": "operationsManager"},
            )
            completed = client.post(
                f"{BASE}/network-rebalance/stores/{store_id}/avm/complete",
                headers=_headers("tenant-a", idempotency_key="avm-complete-quality-1"),
                json={"actorRoleId": "operationsManager"},
            )
        assert requested.status_code == 200, requested.text
        assert completed.status_code == 200, completed.text
        card = completed.json()["store"]
        completion_audit_id = completed.json()["auditEvent"]["id"]
        case_id = card["canonicalAvmCaseId"]
        report_id = card["avm"]["reportId"]
        measured_confidence = card["avmConf"]
        measured_p50 = card["avmP50"]
        assert measured_confidence
        assert card["avmQualityScoreStatus"] == "measured"
        assert card["avmQualityDisposition"] is None
    finally:
        harness.close()

    # A measured card is not collateral damage: restarting must not downgrade it,
    # on the snapshot or on a retry of the key that produced it.
    restarted = CanonicalHarness(database_path)
    try:
        with TestClient(restarted.app()) as client:
            snapshot = client.get(
                f"{BASE}/network-rebalance",
                headers=_headers("tenant-a"),
            )
            replayed = _replay_avm_completion(client, store_id, "avm-complete-quality-1")
        assert snapshot.status_code == 200, snapshot.text
        row = _rebalance_row(snapshot.json(), store_id)
        assert row["avmConf"] == measured_confidence
        assert row["avmQualityDisposition"] is None
        assert row["avmP50"] is not None
        replayed_card = replayed["store"]
        assert replayed_card["avmConf"] == measured_confidence
        assert replayed_card["avm"]["confidence"] == measured_confidence
        assert replayed_card["avmQualityScoreStatus"] == "measured"
        assert replayed_card["avmQualityDisposition"] is None
        # The retry answered the original action, so it neither valued again nor
        # recorded a second completion.
        assert replayed["auditEvent"]["id"] == completion_audit_id
        assert len(restarted.avm("tenant-a").report_history(case_id)) == 1
    finally:
        restarted.close()

    # Make the stored case opaque exactly the way a pre-nullability pickle is,
    # while leaving the already-written card claiming its original confidence.
    opaque = CanonicalHarness(database_path)
    try:
        scoped = opaque.scoped("tenant-a")
        stored_case = scoped.get("avm.cases", case_id)
        assert stored_case is not None
        _strip_quality_status(stored_case.valuation_input)
        scoped.put("avm.cases", case_id, stored_case)
    finally:
        opaque.close()

    legacy = CanonicalHarness(database_path)
    try:
        with TestClient(legacy.app()) as client:
            snapshot = client.get(
                f"{BASE}/network-rebalance",
                headers=_headers("tenant-a"),
            )
            legacy_replay = _replay_avm_completion(
                client, store_id, "avm-complete-quality-1"
            )
            # A valuation measured after the legacy card exists, so the downgrade
            # has to be about this card rather than about the tenant.
            fresh_requested = client.post(
                f"{BASE}/network-rebalance/stores/{fresh_store_id}/avm/request",
                headers=_headers("tenant-a", idempotency_key="avm-request-fresh-1"),
                json={"actorRoleId": "operationsManager"},
            )
            fresh_completed = client.post(
                f"{BASE}/network-rebalance/stores/{fresh_store_id}/avm/complete",
                headers=_headers("tenant-a", idempotency_key="avm-complete-fresh-1"),
                json={"actorRoleId": "operationsManager"},
            )
            fresh_replay = _replay_avm_completion(
                client, fresh_store_id, "avm-complete-fresh-1"
            )
            after = client.get(
                f"{BASE}/network-rebalance",
                headers=_headers("tenant-a"),
            )
        assert snapshot.status_code == 200, snapshot.text
        row = _rebalance_row(snapshot.json(), store_id)
        assert row["avmQualityScoreStatus"] == "legacy_unknown"
        assert row["avmQualityDisposition"] == "legacy_unknown_downgraded"
        assert row["avmConf"] == "low"
        assert row["avmConf"] != measured_confidence
        # The historical price the operator was shown is still the record.
        assert row["avmP50"] is not None
        # The downgrade came from this card's own report, not from a newer one.
        disposed = legacy.avm("tenant-a").latest_report(case_id)
        assert disposed is not None
        assert disposed.report_id == report_id
        assert disposed.quality_disposition == "legacy_unknown_downgraded"

        # The retry of the original key reaches the same verdict as the snapshot,
        # in both the flattened and the nested shape it publishes.
        legacy_card = legacy_replay["store"]
        assert legacy_card["avmConf"] == "low"
        assert legacy_card["avmConf"] != measured_confidence
        assert legacy_card["avmQualityScoreStatus"] == "legacy_unknown"
        assert legacy_card["avmQualityDisposition"] == "legacy_unknown_downgraded"
        assert legacy_card["avm"]["confidence"] == "low"
        assert legacy_card["avm"]["qualityScoreStatus"] == "legacy_unknown"
        assert legacy_card["avm"]["qualityDisposition"] == "legacy_unknown_downgraded"
        # Identity and price are the historical record and stay put; the replay is
        # still the same action, answered once.
        assert legacy_card["avm"]["reportId"] == report_id
        assert legacy_card["avm"]["requestId"] == case_id
        assert legacy_card["avmP50"] == measured_p50
        assert legacy_replay["auditEvent"]["id"] == completion_audit_id

        assert fresh_requested.status_code == 200, fresh_requested.text
        assert fresh_completed.status_code == 200, fresh_completed.text
        fresh_card = fresh_completed.json()["store"]
        assert fresh_card["avmQualityScoreStatus"] == "measured"
        assert fresh_card["avmQualityDisposition"] is None
        assert fresh_card["avmConf"]
        fresh_replayed_card = fresh_replay["store"]
        assert fresh_replayed_card["avmConf"] == fresh_card["avmConf"]
        assert fresh_replayed_card["avm"]["confidence"] == fresh_card["avmConf"]
        assert fresh_replayed_card["avmQualityScoreStatus"] == "measured"
        assert fresh_replayed_card["avmQualityDisposition"] is None

        # No replay valued anything again or filed a second completion.
        assert len(legacy.avm("tenant-a").report_history(case_id)) == 1
        assert len(legacy.avm("tenant-a").report_history(fresh_case_id)) == 1
        assert len(_completions_for(after.json(), store_id)) == 1
        assert len(_completions_for(after.json(), fresh_store_id)) == 1
    finally:
        legacy.close()

    # The cached card answers for itself. Point only the cached copy at a report
    # the canonical store cannot produce: the snapshot still resolves the store's
    # own report, so a replay that reported anything but "unverifiable" would be
    # reading the current store instead of the card it is replaying.
    cached_dangling = CanonicalHarness(database_path)
    try:
        scoped = cached_dangling.scoped("tenant-a")
        state = scoped.get("operator.live_domain_state", "network-rebalance")
        assert state is not None
        cached = state["idempotencyCache"][("complete_avm", "avm-complete-quality-1")]
        # The stored record still carries the claim it was written with: the
        # downgrade is a projection applied on the way out, not a rewrite of the
        # response that was actually served.
        assert cached["store"]["avm"]["reportId"] == report_id
        assert cached["store"]["avm"]["confidence"] == measured_confidence
        cached["store"]["avm"]["reportId"] = "avm-report-does-not-exist"
        scoped.put("operator.live_domain_state", "network-rebalance", state)
    finally:
        cached_dangling.close()

    unverifiable_replay = CanonicalHarness(database_path)
    try:
        with TestClient(unverifiable_replay.app()) as client:
            snapshot = client.get(
                f"{BASE}/network-rebalance",
                headers=_headers("tenant-a"),
            )
            replayed = _replay_avm_completion(client, store_id, "avm-complete-quality-1")
        assert snapshot.status_code == 200, snapshot.text
        row = _rebalance_row(snapshot.json(), store_id)
        assert row["avmQualityDisposition"] == "legacy_unknown_downgraded"
        replayed_card = replayed["store"]
        assert replayed_card["avmConf"] is None
        assert replayed_card["avmQualityScoreStatus"] is None
        assert replayed_card["avmQualityDisposition"] == "unverifiable_report_reference"
        assert replayed_card["avm"]["confidence"] is None
        assert replayed_card["avm"]["qualityScoreStatus"] is None
        assert replayed_card["avm"]["qualityDisposition"] == "unverifiable_report_reference"
        assert replayed_card["avmP50"] == measured_p50
    finally:
        unverifiable_replay.close()

    # A card naming a report the canonical store cannot produce is unverifiable,
    # so it claims nothing -- rather than borrowing the case's latest report.
    dangling = CanonicalHarness(database_path)
    try:
        scoped = dangling.scoped("tenant-a")
        state = scoped.get("operator.live_domain_state", "network-rebalance")
        assert state is not None
        for entry in state["stores"]:
            if entry.get("storeId") == store_id:
                entry["avm"]["reportId"] = "avm-report-does-not-exist"
        scoped.put("operator.live_domain_state", "network-rebalance", state)
    finally:
        dangling.close()

    unverifiable = CanonicalHarness(database_path)
    try:
        with TestClient(unverifiable.app()) as client:
            snapshot = client.get(
                f"{BASE}/network-rebalance",
                headers=_headers("tenant-a"),
            )
        assert snapshot.status_code == 200, snapshot.text
        row = _rebalance_row(snapshot.json(), store_id)
        assert row["avmConf"] is None
        assert row["avmQualityScoreStatus"] is None
        assert row["avmQualityDisposition"] == "unverifiable_report_reference"
    finally:
        unverifiable.close()


@pytest.mark.parametrize(
    "stage", [PlanStatus.CANDIDATE, PlanStatus.SIMULATED, PlanStatus.OPTIMIZED],
)
def test_governance_unsubmitted_priceops_keeps_overview_row_without_pending_count(
    tmp_path: Path, stage: PlanStatus,
) -> None:
    harness = CanonicalHarness(tmp_path / f"priceops-unsubmitted-{stage.value}.sqlite3")
    try:
        plan_id = _seed_priceops(harness, "tenant-a", stage=stage)
        with TestClient(harness.app()) as client:
            response = client.get(f"{BASE}/governance/snapshot", headers=_headers("tenant-a"))
        assert response.status_code == 200, response.text
        payload = response.json()
        rows = [row for row in payload["approvals"] if row["module"] == "PriceOps"]
        assert len(rows) == 1
        assert rows[0]["id"] == plan_id
        assert rows[0]["status"] == "not_submitted"
        assert payload["counts"]["pendingApprovals"] == 0
        assert harness.priceops("tenant-a").get_plan(plan_id).status is stage
        assert harness.priceops("tenant-a").list_approvals(plan_id) == []
        assert payload["decisions"] == []
    finally:
        harness.close()


def test_governance_submitted_priceops_has_one_pending_without_fabricated_decision(
    tmp_path: Path,
) -> None:
    harness = CanonicalHarness(tmp_path / "priceops-first-submission.sqlite3")
    try:
        plan_id = _seed_priceops(harness, "tenant-a")
        service = PriceOpsService(repository=harness.priceops("tenant-a"))
        service.submit_for_approval(plan_id, actor="pricing-maker")
        with TestClient(harness.app()) as client:
            response = client.get(f"{BASE}/governance/snapshot", headers=_headers("tenant-a"))
        assert response.status_code == 200, response.text
        payload = response.json()
        rows = [row for row in payload["approvals"] if row["module"] == "PriceOps"]
        assert [(row["id"], row["status"]) for row in rows] == [(plan_id, "pending")]
        assert payload["counts"]["pendingApprovals"] == 1
        assert service.get_plan_comparison(plan_id).approval_status == "pending_review"
        assert service.repository.list_approvals(plan_id) == []
        assert payload["decisions"] == []
    finally:
        harness.close()


def test_governance_priceops_new_review_round_keeps_prior_decision_as_history(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "priceops-new-review-round.sqlite3"
    harness = CanonicalHarness(database_path)
    try:
        plan_id = _seed_priceops(harness, "tenant-a")
        service = PriceOpsService(repository=harness.priceops("tenant-a"))
        service.submit_for_approval(plan_id, actor="pricing-maker")
        prior = service.approve(plan_id, actor_id="pricing-reviewer", reason="first pilot approved")
        prior_record = prior.to_dict()
        service.activate(plan_id, executor="pricing-runner")
        service.start_observation(plan_id)
        simulation = service.repository.get_simulation(plan_id)
        optimization = service.repository.get_optimization(plan_id)
        assert simulation is not None and optimization is not None
        assert optimization.total_incremental_gross_margin > 0
        evaluated = service.evaluate(
            plan_id,
            actual_gross_margin=(
                simulation.expected_gross_margin + optimization.total_incremental_gross_margin / 4
            ),
        )
        assert evaluated.plan.status is PlanStatus.ADJUST
        with TestClient(harness.app()) as client:
            for advance, expected_status, pending_count in [
                (service.simulate, "not_submitted", 0),
                (service.optimize, "not_submitted", 0),
                (service.submit_for_approval, "pending", 1),
            ]:
                advance(plan_id)
                response = client.get(f"{BASE}/governance/snapshot", headers=_headers("tenant-a"))
                assert response.status_code == 200, response.text
                payload = response.json()
                rows = [row for row in payload["approvals"] if row["module"] == "PriceOps"]
                assert [(row["id"], row["status"]) for row in rows] == [(plan_id, expected_status)]
                assert payload["counts"]["pendingApprovals"] == pending_count
                history = [row for row in payload["decisions"] if row["module"] == "PriceOps"]
                assert len(history) == 1
                assert history[0]["id"] == prior.decision_id
                assert history[0]["finalDecision"] == "APPROVED"
                assert history[0]["actor"] == prior.actor_id
                assert history[0]["reason"] == prior.decision_reason
                assert [record.to_dict() for record in service.repository.list_approvals(plan_id)] == [prior_record]
    finally:
        harness.close()

    reopened = CanonicalHarness(database_path)
    try:
        assert reopened.priceops("tenant-a").get_plan(plan_id).status is PlanStatus.PENDING_APPROVAL
        assert [record.to_dict() for record in reopened.priceops("tenant-a").list_approvals(plan_id)] == [prior_record]
        with TestClient(reopened.app()) as client:
            response = client.get(f"{BASE}/governance/snapshot", headers=_headers("tenant-a"))
        assert response.status_code == 200, response.text
        assert response.json()["counts"]["pendingApprovals"] == 1
        assert [row["id"] for row in response.json()["decisions"] if row["module"] == "PriceOps"] == [prior.decision_id]
    finally:
        reopened.close()


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_governance_priceops_final_decision_preserves_original_mapping(
    tmp_path: Path, decision: str,
) -> None:
    harness = CanonicalHarness(tmp_path / f"priceops-decision-{decision}.sqlite3")
    try:
        plan_id = _seed_priceops(harness, "tenant-a")
        service = PriceOpsService(repository=harness.priceops("tenant-a"))
        service.submit_for_approval(plan_id, actor="pricing-maker")
        record = service.approve(
            plan_id, actor_id="pricing-reviewer", reason=f"pilot {decision}", decision=decision,
        )
        with TestClient(harness.app()) as client:
            response = client.get(f"{BASE}/governance/snapshot", headers=_headers("tenant-a"))
        assert response.status_code == 200, response.text
        payload = response.json()
        rows = [row for row in payload["approvals"] if row["module"] == "PriceOps"]
        assert [(row["id"], row["status"]) for row in rows] == [(plan_id, decision)]
        assert payload["counts"]["pendingApprovals"] == 0
        assert service.get_plan_comparison(plan_id).approval_status == decision
        assert [row["id"] for row in payload["decisions"] if row["module"] == "PriceOps"] == [record.decision_id]
        assert [stored.to_dict() for stored in service.repository.list_approvals(plan_id)] == [record.to_dict()]
    finally:
        harness.close()


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
                "tenant_id": "tenant-a",
                "target_format_code": "ODAY_G2",
                "h3_index": "892000000000001",
                "latitude": 25.033,
                "longitude": 121.565,
                "geocode_confidence": 0.94,
                "prior_90d_cell_net_revenue": 180_000.0,
                "prior_90d_cell_transaction_count": 30,
                "prior_90d_cell_store_count": 1,
                "monthly_rent": 120_000,
                "area_ping": 35,
                "frontage_m": 5.5,
                "average_confidence": 0.9,
                "data_quality_score": 0.9,
                "source_snapshot_ids": ["snapshot-live-1"],
                "feature_snapshot_time": datetime.now(UTC),
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


def test_canonical_scoring_fails_closed_when_point_in_time_features_missing(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test-b1-missing-pit.sqlite3"
    harness = CanonicalHarness(database_path)
    address = AddressLocation(
        address_id="addr-b1",
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
        listing_id="lst-b1",
        source_listing_id="src-b1",
        source_id="approved-feed",
        address_id=address.address_id,
        rent_amount=120_000,
        area_ping=35,
        floor="1F",
        frontage_m=5.5,
        snapshot_id="snap-b1",
        confidence=0.94,
    )
    # Local recorded pass isolates this test's existing PIT failure boundary.
    object.__setattr__(listing, "hard_rule_failures", [])
    candidate = CandidateSite(
        candidate_site_id="cand-b1",
        listing_id=listing.listing_id,
        address_id=address.address_id,
        target_format_code="ODAY_G2",
        created_by="integration-test",
    )
    repository = harness.listing("tenant-a")
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
    # Missing prior_90d_cell_* features
    repository.save_candidate(
        CandidateSiteDraft(
            listing=listing,
            address=address,
            candidate_site=candidate,
            heat_zone_id=address.h3_res_9,
            listing_source=listing.source_id,
            feature_snapshot_time=datetime.now(UTC),
        )
    )
    try:
        with TestClient(harness.app()) as client:
            scored = client.post(
                f"{BASE}/network-scoring/candidates/cand-b1/score",
                headers=_headers("tenant-a"),
                json={"actorRoleId": "siteReviewer"},
            )
        assert scored.status_code == 503
        detail = scored.json()["detail"]
        assert "missing features" in detail["message"].lower()
        # An incomplete feature row is a permanent data gap: re-issuing the
        # same score request cannot succeed, so it must not be advertised as
        # retryable.
        assert detail["code"] == "SITESCORE_FEATURE_CONTRACT_INCOMPLETE"
        assert detail["retryable"] is False
    finally:
        harness.close()


def test_canonical_scoring_fails_closed_when_ungeocoded_cell_has_no_h3(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test-b2-ungeocoded.sqlite3"
    harness = CanonicalHarness(database_path)
    address = AddressLocation(
        address_id="addr-b2",
        raw_address=" ungeocoded address ",
        normalized_address="",
        city="",
        district="",
        latitude=0.0,
        longitude=0.0,
        geocode_confidence=0.0,
        h3_res_9="",
    )
    listing = Listing(
        listing_id="lst-b2",
        source_listing_id="src-b2",
        source_id="approved-feed",
        address_id=address.address_id,
        rent_amount=120_000,
        area_ping=35,
        floor="1F",
        snapshot_id="snap-b2",
    )
    candidate = CandidateSite(
        candidate_site_id="cand-b2",
        listing_id=listing.listing_id,
        address_id=address.address_id,
        target_format_code="ODAY_G2",
    )
    repository = harness.listing("tenant-a")
    repository.save_listing(
        listing,
        address,
        ListingDedupKey(
            source_id=listing.source_id,
            source_listing_id=listing.source_listing_id,
            normalized_address=address.raw_address,
            rent_amount=listing.rent_amount,
            area_ping=listing.area_ping,
        ),
    )
    repository.save_candidate(
        CandidateSiteDraft(
            listing=listing,
            address=address,
            candidate_site=candidate,
            heat_zone_id="",
            prior_90d_cell_net_revenue=100.0,
            prior_90d_cell_transaction_count=1,
            prior_90d_cell_store_count=1,
            feature_snapshot_time=datetime.now(UTC),
        )
    )
    try:
        with TestClient(harness.app()) as client:
            scored = client.post(
                f"{BASE}/network-scoring/candidates/cand-b2/score",
                headers=_headers("tenant-a"),
                json={"actorRoleId": "siteReviewer"},
            )
        assert scored.status_code in {422, 503}
    finally:
        harness.close()


def test_canonical_scoring_fails_closed_when_feature_snapshot_time_is_stale_or_missing(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test-b4-stale.sqlite3"
    harness = CanonicalHarness(database_path)
    address = AddressLocation(
        address_id="addr-b4",
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
        listing_id="lst-b4",
        source_listing_id="src-b4",
        source_id="approved-feed",
        address_id=address.address_id,
        rent_amount=120_000,
        area_ping=35,
        floor="1F",
        snapshot_id="snap-b4",
        confidence=0.94,
    )
    # Local recorded pass isolates this test's existing PIT failure boundary.
    object.__setattr__(listing, "hard_rule_failures", [])
    candidate = CandidateSite(
        candidate_site_id="cand-b4",
        listing_id=listing.listing_id,
        address_id=address.address_id,
        target_format_code="ODAY_G2",
    )
    repository = harness.listing("tenant-a")
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
    # Stale feature snapshot time (> 90 days ago)
    stale_time = datetime.now(UTC) - timedelta(days=120)
    repository.save_candidate(
        CandidateSiteDraft(
            listing=listing,
            address=address,
            candidate_site=candidate,
            heat_zone_id=address.h3_res_9,
            prior_90d_cell_net_revenue=180_000.0,
            prior_90d_cell_transaction_count=30,
            prior_90d_cell_store_count=1,
            feature_snapshot_time=stale_time,
        )
    )
    try:
        with TestClient(harness.app()) as client:
            scored = client.post(
                f"{BASE}/network-scoring/candidates/cand-b4/score",
                headers=_headers("tenant-a"),
                json={"actorRoleId": "siteReviewer"},
            )
        assert scored.status_code == 503
        detail = scored.json()["detail"]
        assert "stale" in detail["message"].lower()
        assert detail["code"] == "SITESCORE_FEATURE_CONTRACT_INCOMPLETE"
        assert detail["retryable"] is False
    finally:
        harness.close()


def _seed_convertible_listing(
    harness: CanonicalHarness,
    tenant_id: str,
    *,
    listing_id: str = _CONVERTIBLE_LISTING_ID,
    snapshot_time: datetime | None = None,
    prior_revenue: float | None = 180_000.0,
) -> None:
    """Seed a listing the operator can convert, with the v2 PIT contract.

    The prior_90d_cell_* aggregates come from the model-ready views and have no
    field on the domain ``Listing``, so the operator workflow carries them as
    listing metadata — exactly the path a real intake/backfill writes.
    """

    address = AddressLocation(
        address_id="00000000-0000-0000-0000-000000000601",
        raw_address="台北市信義區松仁路 96 號 1F",
        normalized_address="台北市信義區松仁路96號1F",
        city="台北市",
        district="信義區",
        latitude=25.033964,
        longitude=121.564468,
        geocode_confidence=0.94,
        h3_res_9="892000000000001",
    )
    listing = Listing(
        listing_id=listing_id,
        source_listing_id=f"source-{listing_id}",
        source_id="approved-feed",
        listing_status="new",
        address_id=address.address_id,
        rent_amount=58_000,
        area_ping=18,
        floor="1F",
        frontage_m=6,
        snapshot_id=str(uuid4()),
        confidence=0.94,
    )
    object.__setattr__(listing, "property_id", str(uuid4()))
    object.__setattr__(listing, "listing_obs_id", str(uuid4()))
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
    DurableAssistedIntakeRepository(
        TenantScopedDocumentStore(harness.document_store, tenant_id)
    ).save_listing_metadata(
        listing_id,
        {
            "heatZoneId": address.h3_res_9,
            "hardRuleFailures": [],
            "hardRuleSummary": "3/3 pass",
            "address": address.normalized_address,
            "prior90dCellNetRevenue": prior_revenue,
            "prior90dCellTransactionCount": 30,
            "prior90dCellStoreCount": 1,
            "featureSnapshotTime": (
                snapshot_time or datetime.now(UTC)
            ).isoformat(),
        },
    )


def _approve_convertible_listing(database, harness, client):
    """Real request/review creates the candidate from the original metadata seed.

    No candidate, promotion decision or approval is written by the test. Only
    measured source admission is an explicit local synthetic input.
    """
    from apps.api.app.routes.listings import V1ListingRepositoryAdapter
    from tests.integration.test_intake_score_delivery_postgresql import _seed_manual_admission
    from tests.integration.test_operator_intake_promotion_postgresql import (
        _request,
        _review,
        _reviewer_headers,
    )
    from tests.integration.test_operator_intake_workflow_postgresql import (
        _headers as intake_headers,
    )

    direct = client.post(
        f"{BASE}/network-listings/listings/{_CONVERTIBLE_LISTING_ID}/convert",
        headers=_headers(TENANT, subject=MANAGER, idempotency_key="direct-convert-before-approval"),
        json={"actorRoleId": "expansionManager", "target_format_code": "FORGED-TARGET"},
    )
    assert direct.status_code == 422, direct.text
    assert "PROMOTION_APPROVAL_REQUIRED" in direct.text
    assert harness.listing(TENANT).list_candidates() == []
    with database.connect() as conn:
        for role in ("site_reviewer", "expansion_user"):
            conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                         (APPROVER, role, "isolated-synthetic-writer-test"))
    base = f"{BASE}/network-listings/intake"
    submitted = client.post(base + "/submit", json={"url": "https://rent.591.com.tw/synthetic-writer"}, headers=intake_headers())
    assert submitted.status_code == 200, submitted.text
    iid = submitted.json()["id"]
    corrected = client.post(f"{base}/{iid}/correct", json={"fields": {"floor": "1F"}, "reason": "Synthetic recorded input",
                            "riskSummary": "Synthetic local writer coverage", "riskAcknowledged": True},
                            headers=intake_headers(key="synthetic-writer-correct"))
    assert corrected.status_code == 200, corrected.text
    decided = client.post(f"{base}/{iid}/decide", json={"action": "revise", "targetListingId": _CONVERTIBLE_LISTING_ID,
                          "reason": "Synthetic measured listing", "riskSummary": "Synthetic local writer coverage", "riskAcknowledged": True},
                          headers=_reviewer_headers("synthetic-writer-decide"))
    assert decided.status_code == 200, decided.text
    intake = decided.json()
    # Read through the real metadata adapter; do not move PIT values onto the
    # stored Listing or construct/save a CandidateSiteDraft in the fixture.
    listing = V1ListingRepositoryAdapter(harness.listing(TENANT)).get_domain_listing(_CONVERTIBLE_LISTING_ID)
    _seed_manual_admission(database, harness.bundle, intake, listing)
    requested = _request(client, intake, key="synthetic-writer-request")
    assert requested.status_code == 202, requested.text
    assert requested.json()["proposer_subject_id"] == MANAGER
    reviewed = _review(client, requested.json(), key="synthetic-writer-review")
    assert reviewed.status_code == 200, reviewed.text
    receipt = reviewed.json()
    assert receipt["reviewer_subject_id"] == APPROVER != MANAGER
    assert receipt["candidate_site_id"]
    return receipt


@pytest.mark.requires_live_env
def test_operator_convert_carries_point_in_time_contract_into_scoring(
    bridge_database, monkeypatch,
) -> None:
    """B2: the production writer — not the test — must supply the v2 contract.

    Drives canonical request/review, then the approved-candidate compatibility
    writer ``POST /listings/{id}/convert``, and scores the resulting candidate. Nothing here calls ``repository.save_candidate``, so a
    pass proves the point-in-time features survive
    ``_candidate_to_dict``/``_dict_to_candidate`` and reach the model row.
    """

    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime
    from tests.integration.test_intake_score_delivery_postgresql import _claim
    runtime = RecordingRuntime()
    harness = _postgres_listing_harness(bridge_database, monkeypatch, runtime)
    _seed_convertible_listing(harness, "00000000-0000-0000-0000-000000000001")
    try:
        with TestClient(harness.app()) as client:
            accepted = _approve_convertible_listing(bridge_database, harness, client)
            converted = client.post(
                f"{BASE}/network-listings/listings/{_CONVERTIBLE_LISTING_ID}/convert",
                headers=_headers("00000000-0000-0000-0000-000000000001", subject=MANAGER, idempotency_key="convert-live-1"),
                json={"actorRoleId": "expansionManager", "actorName": "Expansion A"},
            )
            assert converted.status_code == 200, converted.text
            candidate_id = converted.json()["candidate"]["id"]

            handle_intake_score(_claim(harness.bundle, accepted["site_score_job_id"]), harness.bundle, model_runtime=runtime)
            scored = client.get(f"{BASE}/network-scoring", headers=_headers(TENANT, subject=MANAGER))

        assert scored.status_code == 200, scored.text
        assert scored.json()["scorecards"][0]["id"] == candidate_id
        assert scored.json()["scorecards"][0]["reportVersion"] == 1
        row = runtime.calls[0]["rows"][0]
        assert row["prior_90d_cell_net_revenue"] == 180_000.0
        assert row["prior_90d_cell_transaction_count"] == 30
        assert row["prior_90d_cell_store_count"] == 1
        assert row["geocode_confidence"] == 0.94
        assert row["h3_index"] == "892000000000001"
        assert row["latitude"] == 25.033964
        assert row["longitude"] == 121.564468
        assert row["feature_snapshot_time"] is not None

        # The persisted draft must still carry the contract, so the next
        # operator write does not silently wipe it.
        draft = harness.listing("00000000-0000-0000-0000-000000000001").list_candidates()[0]
        assert draft.prior_90d_cell_net_revenue == 180_000.0
        assert draft.prior_90d_cell_transaction_count == 30
        assert draft.prior_90d_cell_store_count == 1
        assert draft.feature_snapshot_time is not None
        assert draft.candidate_site.target_format_code == "FORMAT-B"
        assert draft.candidate_site.created_by == APPROVER
        assert draft.property_entity_id == draft.listing.property_id
        assert draft.listing_observation_id == draft.listing.listing_obs_id
    finally:
        harness.close()


@pytest.mark.requires_live_env
def test_operator_listing_write_does_not_wipe_candidate_features(
    bridge_database, monkeypatch,
) -> None:
    """B2 (wipe path): re-syncing a complete candidate must be lossless.

    ``_sync_candidate_to_repo`` rebuilds the persisted draft from the dict, so a
    second operator write used to overwrite an already-complete candidate with
    all four point-in-time features set to ``None``.
    """

    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime
    from tests.integration.test_intake_score_delivery_postgresql import _claim
    runtime = RecordingRuntime()
    harness = _postgres_listing_harness(bridge_database, monkeypatch, runtime)
    _seed_convertible_listing(harness, "00000000-0000-0000-0000-000000000001")
    try:
        with TestClient(harness.app()) as client:
            accepted = _approve_convertible_listing(bridge_database, harness, client)
            first = client.post(
                f"{BASE}/network-listings/listings/{_CONVERTIBLE_LISTING_ID}/convert",
                headers=_headers("00000000-0000-0000-0000-000000000001", subject=MANAGER, idempotency_key="convert-nowipe-1"),
                json={"actorRoleId": "expansionManager"},
            )
            assert first.status_code == 200, first.text
            # A second convert re-runs the writer over the existing candidate.
            second = client.post(
                f"{BASE}/network-listings/listings/{_CONVERTIBLE_LISTING_ID}/convert",
                headers=_headers("00000000-0000-0000-0000-000000000001", subject=MANAGER, idempotency_key="convert-nowipe-2"),
                json={"actorRoleId": "expansionManager"},
            )
            assert second.status_code == 200, second.text
            candidate_id = second.json()["candidate"]["id"]

            handle_intake_score(_claim(harness.bundle, accepted["site_score_job_id"]), harness.bundle, model_runtime=runtime)
            scored = client.get(f"{BASE}/network-scoring", headers=_headers(TENANT, subject=MANAGER))

        assert scored.status_code == 200, scored.text
        assert scored.json()["scorecards"][0]["id"] == candidate_id
        assert scored.json()["scorecards"][0]["reportVersion"] == 1
        draft = harness.listing("00000000-0000-0000-0000-000000000001").list_candidates()[0]
        assert draft.prior_90d_cell_net_revenue == 180_000.0
        assert draft.feature_snapshot_time is not None
        # M7: the recorded hard-rule verdict must not be silently recomputed.
        assert draft.feasibility_flags == ()
    finally:
        harness.close()


@pytest.mark.requires_live_env
def test_approved_candidate_resync_preserves_queued_worker_authority(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime
    from tests.integration.test_intake_score_delivery_postgresql import _claim, _physical

    harness = _postgres_listing_harness(bridge_database, monkeypatch)
    _seed_convertible_listing(harness, TENANT)
    try:
        with TestClient(harness.app()) as client:
            receipt = _approve_convertible_listing(bridge_database, harness, client)
            repo = harness.listing(TENANT)
            before = deepcopy(repo.list_candidates()[0])
            result = client.post(
                f"{BASE}/network-listings/listings/{_CONVERTIBLE_LISTING_ID}/convert",
                headers=_headers(TENANT, subject=MANAGER, idempotency_key="resync-queued-candidate"),
                json={"actorRoleId": "expansionManager"},
            )
            assert result.status_code == 200, result.text
            assert result.json()["created"] is False
            after = repo.list_candidates()[0]
            assert vars(after) == vars(before)
            assert vars(after.listing) == vars(before.listing)
            runtime = RecordingRuntime()
            handle_intake_score(_claim(harness.bundle, receipt["site_score_job_id"]),
                                harness.bundle, model_runtime=runtime)
            physical = _physical(bridge_database, receipt["site_score_job_id"])
            assert physical["status"] == "SUCCEEDED"
            assert len(runtime.calls) == 1
            assert runtime.calls[0]["rows"][0]["prior_90d_cell_net_revenue"] == 180_000.0
            assert harness.sitescore(TENANT).get_report(physical["result"]["report_id"]) is not None
            assert repo.list_candidates()[0].candidate_site.candidate_site_id == receipt["candidate_site_id"]
    finally:
        harness.close()


@pytest.mark.requires_live_env
def test_operator_convert_without_cell_aggregates_still_fails_closed(
    bridge_database, monkeypatch,
) -> None:
    """Criterion 3 on the production path: a real gap must not be scored.

    Carrying the contract must not become fabricating it — a listing whose cell
    revenue was never resolved has to fail closed as a permanent data gap.
    """

    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime
    from tests.integration.test_intake_score_delivery_postgresql import _claim, _physical
    runtime = RecordingRuntime()
    harness = _postgres_listing_harness(bridge_database, monkeypatch, runtime)
    _seed_convertible_listing(harness, "00000000-0000-0000-0000-000000000001", prior_revenue=None)
    try:
        with TestClient(harness.app()) as client:
            accepted = _approve_convertible_listing(bridge_database, harness, client)
            converted = client.post(
                f"{BASE}/network-listings/listings/{_CONVERTIBLE_LISTING_ID}/convert",
                headers=_headers("00000000-0000-0000-0000-000000000001", subject=MANAGER, idempotency_key="convert-gap-1"),
                json={"actorRoleId": "expansionManager"},
            )
            assert converted.status_code == 200, converted.text
            candidate_id = converted.json()["candidate"]["id"]

            handle_intake_score(_claim(harness.bundle, accepted["site_score_job_id"]), harness.bundle, model_runtime=runtime)

        failed = _physical(bridge_database, accepted["site_score_job_id"])
        assert failed["status"] == "FAILED"
        assert failed["last_error"]["retryable"] is False
        assert runtime.calls == []
        assert harness.sitescore(TENANT).latest(candidate_id) is None

    finally:
        harness.close()


@pytest.mark.requires_live_env
@pytest.mark.parametrize(
    ("heat_zone_id", "label"),
    [
        (None, "null"),
        ("", "empty"),
        ("   ", "whitespace"),
        ("\t\n", "whitespace-control"),
    ],
)
def test_operator_candidate_write_rejects_blank_heat_zone_before_persistence(
    bridge_database, monkeypatch,
    heat_zone_id: str | None,
    label: str,
) -> None:
    """M7: a blank heat zone must be rejected, not persisted as ``""``.

    Drives the production writer (``save_candidate`` -> ``_sync_candidate_to_repo``)
    rather than the repository, because the regression was in the dict round
    trip: the guard only checked for an absent key, so ``None``/``""``/blank
    values reached ``CandidateSiteDraft`` as ``""`` and made the candidate
    unjoinable to its H3 cell aggregates. The durable read-back proves the
    rejection happened before persistence, not after a partial write.
    """

    harness = _postgres_listing_harness(bridge_database, monkeypatch)
    _seed_convertible_listing(harness, "00000000-0000-0000-0000-000000000001")
    try:
        with TestClient(harness.app()) as client:
            _approve_convertible_listing(bridge_database, harness, client)
            converted = client.post(
                f"{BASE}/network-listings/listings/{_CONVERTIBLE_LISTING_ID}/convert",
                headers=_headers("00000000-0000-0000-0000-000000000001", subject=MANAGER, idempotency_key=f"convert-hz-{label}"),
                json={"actorRoleId": "expansionManager"},
            )
            assert converted.status_code == 200, converted.text
            candidate_id = converted.json()["candidate"]["id"]

        repository = harness.listing("00000000-0000-0000-0000-000000000001")
        before = repository.list_candidates()
        assert len(before) == 1
        good_heat_zone_id = before[0].heat_zone_id
        assert good_heat_zone_id.strip()

        service = NetworkListingService(
            listing_repository=repository,
            intake_repository=DurableAssistedIntakeRepository(
                harness.scoped("00000000-0000-0000-0000-000000000001")
            ),
            seed_fixtures=False,
        )
        candidate = service.get_candidate(candidate_id)
        assert candidate is not None
        candidate = {**candidate, "heatZoneId": heat_zone_id}

        with pytest.raises(ValueError, match="no usable heatZoneId"):
            service.save_candidate(candidate)

        # Nothing partial was written: the stored candidate still carries the
        # original heat zone and the point-in-time contract.
        after = harness.listing("00000000-0000-0000-0000-000000000001").list_candidates()
        assert len(after) == 1
        assert after[0].heat_zone_id == good_heat_zone_id
        assert after[0].prior_90d_cell_net_revenue == 180_000.0
        assert after[0].feature_snapshot_time is not None
    finally:
        harness.close()


@pytest.mark.requires_live_env
def test_operator_candidate_write_rejects_missing_heat_zone_key(
    bridge_database, monkeypatch,
) -> None:
    """M7 (absent key): the original gap must stay closed too."""

    harness = _postgres_listing_harness(bridge_database, monkeypatch)
    _seed_convertible_listing(harness, "00000000-0000-0000-0000-000000000001")
    try:
        with TestClient(harness.app()) as client:
            _approve_convertible_listing(bridge_database, harness, client)
            converted = client.post(
                f"{BASE}/network-listings/listings/{_CONVERTIBLE_LISTING_ID}/convert",
                headers=_headers("00000000-0000-0000-0000-000000000001", subject=MANAGER, idempotency_key="convert-hz-missing"),
                json={"actorRoleId": "expansionManager"},
            )
            assert converted.status_code == 200, converted.text
            candidate_id = converted.json()["candidate"]["id"]

        repository = harness.listing("00000000-0000-0000-0000-000000000001")
        service = NetworkListingService(
            listing_repository=repository,
            intake_repository=DurableAssistedIntakeRepository(
                harness.scoped("00000000-0000-0000-0000-000000000001")
            ),
            seed_fixtures=False,
        )
        candidate = service.get_candidate(candidate_id)
        assert candidate is not None
        candidate = {k: v for k, v in candidate.items() if k != "heatZoneId"}

        with pytest.raises(ValueError, match="no usable heatZoneId"):
            service.save_candidate(candidate)

        after = harness.listing("00000000-0000-0000-0000-000000000001").list_candidates()
        assert len(after) == 1
        assert after[0].heat_zone_id.strip()
    finally:
        harness.close()


# Owned SQLite canonical HTTP controls, not native PG, model approval or human UAT.
def _decision_reference_plan(
    harness: CanonicalHarness, tenant_id: str, source_ids: tuple[str, ...], *, brand_id: str | None = None,
) -> Any:
    return PriceOpsService(repository=harness.priceops(tenant_id)).create_plan(
        tenant_id=tenant_id,
        items=[PricingPlanItem.create(
            store_id="store-decision-reference", machine_type="washer", brand_id=brand_id,
            constraints=PriceConstraints(
                unit_cost=30, current_price=60, margin_floor_ratio=0.2,
                max_increase_pct=0.2, max_decrease_pct=0.2, price_ladder_step=5,
            ),
            baseline_demand=1000,
            # A declared input label is deliberately not a registry-verified model.
            elasticity=PriceElasticityEstimate(
                elasticity_value=-1.2, confidence=0.85,
                model_version="unit-input-label-not-an-approved-model",
            ),
            source_snapshot_ids=source_ids,
        )],
        created_by="unit-canonical-input-owner",
        correlation_id="unit-canonical-input-reference",
    )


def _submit_reference_growth(client: TestClient, plan: Any, *, before_submit: Any = None) -> tuple[str, str]:
    headers = {**_headers("tenant-a", subject="unit-maker"), "x-operator-role": "ops-lead"}
    draft = client.post(
        f"{BASE}/growth/actions", headers={**headers, "idempotency-key": "unit-reference-draft"},
        json={"name": "Unit reference-only Growth action", "kind": "offpeak",
              "segmentId": f"priceops:{plan.plan_id}:{plan.items[0].item_id}",
              "objective": "Explicit unit objective, not measured impact", "targetLift": 1,
              "audit": {"sourceSnapshotIds": ["forged-body-source"]}},
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["audit"]["sourceSnapshotIds"] == (list(plan.items[0].source_snapshot_ids) or None)
    action_id = draft.json()["id"]
    if before_submit is not None:
        before_submit()
    submitted = client.post(
        f"{BASE}/growth/actions/{action_id}/submit", json={},
        headers={**headers, "idempotency-key": "unit-reference-submit"},
    )
    assert submitted.status_code == 200, submitted.text
    return action_id, submitted.json()["approval"]["id"]


@pytest.mark.parametrize("source_ids", [("unit-snapshot-a", "unit-snapshot-b"), ()])
def test_canonical_growth_decision_freezes_actor_real_approval_and_single_item_sources(
    tmp_path: Path, source_ids: tuple[str, ...],
) -> None:
    from dataclasses import replace

    path = tmp_path / "canonical-growth-decision-reference.sqlite3"
    harness = CanonicalHarness(path)
    plan = _decision_reference_plan(harness, "tenant-a", source_ids)
    headers = {**_headers("tenant-a", subject="unit-actual-reviewer"), "x-operator-role": "ops-lead",
               "idempotency-key": "unit-reference-govern-decision", "x-correlation-id": "unit-reference-decision-cid"}
    try:
        with TestClient(harness.app()) as client:
            def change_plan_before_submit() -> None:
                # Existing typed owner permits updates. This temporal negative
                # must not replace the input actually captured by action CREATE.
                harness.priceops("tenant-a").save_plan(replace(
                    plan, items=(replace(plan.items[0], source_snapshot_ids=("unit-later-same-item-source",)),),
                ))
            action_id, approval_id = _submit_reference_growth(client, plan, before_submit=change_plan_before_submit)
            command = {"approvalId": approval_id, "action": "reject",
                       "reason": "Unit-only canonical rejection with actual owner references",
                       "actorName": "forged-body-actor", "role": "forged-body-role"}
            foreign_command = client.post(
                f"{BASE}/governance/decisions", headers={**_headers("tenant-b"), "x-operator-role": "ops-lead"}, json=command,
            )
            assert foreign_command.status_code == 404, foreign_command.text
            assert client.get(f"{BASE}/growth/decisions", headers=headers).json()["items"] == []
            response = client.post(f"{BASE}/governance/decisions", headers=headers, json=command)
            assert response.status_code == 200, response.text
            parent_decision = response.json()["decision"]
            growth_rows = client.get(f"{BASE}/growth/decisions", headers=headers).json()["items"]
            assert len(growth_rows) == 1
            child = growth_rows[0]
            assert child["ref"] == action_id
            assert child["decidedBy"] == "unit-actual-reviewer"
            assert child["approvalId"] == approval_id != action_id
            assert child["sourceSnapshotIds"] == (list(source_ids) if source_ids else None)
            assert child["modelVersion"] is None
            assert parent_decision["actor"] == "unit-actual-reviewer"
            assert parent_decision["approvalId"] == approval_id
            assert parent_decision["model"] is None
            assert parent_decision["datasetSnapshot"] == (", ".join(source_ids) if source_ids else None)
            before = client.get(f"{BASE}/governance/snapshot", headers=headers).json()
            rows = [row for row in before["decisions"] if row["module"] == "Growth"]
            assert {row["id"] for row in rows} == {parent_decision["id"], child["id"]}
            assert all(row["actor"] == "unit-actual-reviewer" and row["approvalId"] == approval_id for row in rows)
            assert all(row["model"] is None and row["datasetSnapshot"] == parent_decision["datasetSnapshot"] for row in rows)
            assert any(row["entityRef"] == action_id and row["actor"] == "unit-actual-reviewer"
                       and row["correlationId"] == "unit-reference-decision-cid" for row in before["auditRows"])
            # A new owner's freshness must never rebind historical decisions.
            _decision_reference_plan(harness, "tenant-a", ("unit-new-unrelated-snapshot",))
            replay = client.post(f"{BASE}/governance/decisions", headers=headers, json=command)
            assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
            assert replay.json()["decision"] == parent_decision
            assert client.get(f"{BASE}/growth/decisions", headers=headers).json()["items"] == growth_rows
    finally:
        harness.close()
    reopened = CanonicalHarness(path)
    try:
        with TestClient(reopened.app()) as client:
            snapshot = client.get(f"{BASE}/governance/snapshot", headers=headers)
            assert snapshot.status_code == 200, snapshot.text
            assert [row for row in snapshot.json()["decisions"] if row["module"] == "Growth"] == rows
            foreign = client.get(f"{BASE}/governance/snapshot", headers=_headers("tenant-b"))
            assert foreign.status_code == 200, foreign.text
            assert not any(row["id"] in {parent_decision["id"], child["id"]} for row in foreign.json()["decisions"])
    finally:
        reopened.close()


@pytest.mark.parametrize("legacy_kind", ["missing", "malformed"])
def test_legacy_growth_decision_unknown_metadata_remains_readable_without_freshness_fill(
    tmp_path: Path, legacy_kind: str,
) -> None:
    from shared.infrastructure.persistence.operator_domains import (
        DurableOperatorDomainStateRepository,
    )

    harness = CanonicalHarness(tmp_path / "legacy-growth-decision-reference.sqlite3")
    plan = _decision_reference_plan(harness, "tenant-a", ("unit-current-snapshot-must-not-fill-history",))
    try:
        with TestClient(harness.app()) as client:
            # The historical read negative still needs a real owned parent.
            # A decision with no in-scope action must remain filtered out.
            draft = client.post(
                f"{BASE}/growth/actions", headers={**_headers("tenant-a"), "x-operator-role": "ops-lead"},
                json={"name": "Owned parent for legacy optional-field negative", "kind": "offpeak",
                      "segmentId": f"priceops:{plan.plan_id}:{plan.items[0].item_id}",
                      "objective": "Unit-only legacy readability check", "targetLift": 1},
            )
            assert draft.status_code == 200, draft.text
            action_id = draft.json()["id"]
            legacy = {"id": "unit-legacy-growth-decision", "module": "Growth", "ref": action_id,
                      "title": "Historical decision with unknown metadata", "verdict": "rejected",
                      "reason": "Preserved historical reason", "occurredAt": "2026-01-01T00:00:00Z"}
            if legacy_kind == "malformed":
                legacy.update(decidedBy={}, approvalId=[], modelVersion={}, sourceSnapshotIds=[{}])
            repository = DurableOperatorDomainStateRepository(harness.document_store, "growth")
            historical_state = repository.load("tenant-a")
            assert any(row["id"] == action_id for row in historical_state["actions"])
            historical_state["decisions"] = [legacy]
            repository.save("tenant-a", historical_state)
            response = client.get(f"{BASE}/governance/snapshot", headers=_headers("tenant-a"))
            assert response.status_code == 200, response.text
            row = next(row for row in response.json()["decisions"] if row["id"] == legacy["id"])
            assert row["module"] == "Growth" and action_id in row["item"]
            assert row["reason"] == "Preserved historical reason"
            assert row["actor"] is None and row["approvalId"] is None
            assert row["model"] is None and row["datasetSnapshot"] is None
    finally:
        harness.close()


def test_outcome_decision_records_actual_command_actor_without_inventing_missing_approval() -> None:
    from modules.opsboard.application.growth import GrowthService

    # Explicit POC fixture supplies an eligible outcome stage; not observed KPI
    # or native acceptance. The actual writer must preserve its command actor.
    service = GrowthService()
    action = next(row for row in service.list_actions() if row["status"] == "OUTCOME_READY")
    result = service.write_outcome(
        action_id=action["id"], outcome="INCONCLUSIVE", required_action="EXTEND",
        rationale="Unit-only explicit fixture outcome reason", actor_name="unit-outcome-author",
    )
    decision = result["decision"]
    assert decision["decidedBy"] == "unit-outcome-author"
    assert decision["approvalId"] == action.get("approvalId")
    assert decision["modelVersion"] is None and decision["sourceSnapshotIds"] is None


@pytest.mark.parametrize("invalid_sources", ["unit-malformed-snapshot-string", {"unit-malformed-key": "unit-not-a-snapshot"}])
def test_canonical_growth_create_does_not_convert_malformed_owner_snapshot_container_to_references(
    invalid_sources: Any,
) -> None:
    from modules.opsboard.application.growth import GrowthService
    from modules.priceops import InMemoryPriceOpsRepository

    # Real typed Memory owner preserves the historical malformed value; a
    # durable decoder that coerces it first would hide this boundary control.
    repository = InMemoryPriceOpsRepository()
    owner = SimpleNamespace(priceops=lambda tenant_id: repository)
    plan = _decision_reference_plan(owner, "tenant-a", invalid_sources)
    assert repository.get_plan(plan.plan_id).items[0].source_snapshot_ids == invalid_sources
    service = GrowthService(
        priceops_repository=repository, tenant_id="tenant-a",
        require_canonical=True, seed_fixtures=False,
    )
    action = service.create_action(
        name="Malformed owner reference remains unknown",
        segment_id=f"priceops:{plan.plan_id}:{plan.items[0].item_id}",
        objective="Unit-only source shape negative", target_lift=1,
        actor_name="unit-canonical-maker", tenant_id="tenant-a",
    )
    assert action["audit"]["sourceSnapshotIds"] is None
    assert repository.get_plan(plan.plan_id).items[0].source_snapshot_ids == invalid_sources


@pytest.mark.parametrize(
    ("negative", "expected_status"),
    [("missing-key", 404), ("new-key", 404), ("changed-reason", 409), ("changed-action", 409),
     ("other-actor", 404), ("foreign-tenant", 404), ("unknown-history", 404),
     ("forged-replay-flag", 404)],
)
def test_historical_growth_replay_requires_exact_current_owned_parent_actor_and_receipt(
    tmp_path: Path, negative: str, expected_status: int,
) -> None:
    from shared.infrastructure.persistence.operator_domains import (
        DurableOperatorDomainStateRepository,
    )

    harness = CanonicalHarness(tmp_path / "scoped-growth-decision-replay.sqlite3")
    plan = _decision_reference_plan(harness, "tenant-a", ("unit-replay-frozen-source",))
    headers = {**_headers("tenant-a", subject="unit-actual-reviewer"), "x-operator-role": "ops-lead",
               "idempotency-key": "unit-existing-govern-replay", "x-correlation-id": "unit-original-govern-cid"}
    command = {"approvalId": "filled-from-actual-submit", "action": "reject",
               "reason": "Unit-only bound historical command requires exact receipt"}
    try:
        with TestClient(harness.app()) as client:
            action_id, approval_id = _submit_reference_growth(client, plan)
            command["approvalId"] = approval_id
            accepted = client.post(f"{BASE}/governance/decisions", headers=headers, json=command)
            assert accepted.status_code == 200, accepted.text
            repositories = {name: DurableOperatorDomainStateRepository(harness.document_store, name)
                            for name in ("growth", "governance")}
            before = {name: repository.load("tenant-a") for name, repository in repositories.items()}
            request_headers, request_body = dict(headers), dict(command)
            if negative == "missing-key":
                request_headers.pop("idempotency-key")
            elif negative in {"new-key", "forged-replay-flag"}:
                request_headers["idempotency-key"] = "unit-unbound-key"
                if negative == "forged-replay-flag":
                    request_body["replay_only"] = True
            elif negative == "changed-reason":
                request_body["reason"] = "A distinct valid reason must conflict with the saved payload"
            elif negative == "changed-action":
                request_body["action"] = "approve"
            elif negative == "other-actor":
                request_headers["x-subject-id"] = "unit-other-verified-reviewer"
            elif negative == "foreign-tenant":
                request_headers["x-tenant-id"] = "tenant-b"
            elif negative == "unknown-history":
                request_body["approvalId"] = "unit-nonexistent-historical-id"
            rejected = client.post(f"{BASE}/governance/decisions", headers=request_headers, json=request_body)
            assert rejected.status_code == expected_status, rejected.text
            assert {name: repository.load("tenant-a") for name, repository in repositories.items()} == before
            assert all(repository.load("tenant-b") is None for repository in repositories.values())
            replay = client.post(f"{BASE}/governance/decisions", headers=headers, json=command)
            assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
            assert replay.json()["decision"] == accepted.json()["decision"]
            assert {name: repository.load("tenant-a") for name, repository in repositories.items()} == before
            assert before["growth"]["actions"][0]["id"] == action_id
    finally:
        harness.close()


def test_historical_growth_replay_does_not_use_a_receipt_when_current_store_scope_is_revoked(tmp_path: Path) -> None:
    from modules.opsboard.application.governance import GovernanceNotFound, GovernanceService
    from modules.opsboard.application.growth import GrowthService
    from modules.opsboard.application.priceops_read_scope import (
        ScopedPriceOpsReadRepository,
        project_governance_read_state,
        project_growth_read_state,
    )
    from shared.domain.models import Brand, Store, Tenant
    from shared.infrastructure.persistence.operator_domains import (
        DurableOperatorDomainStateRepository,
    )

    harness = CanonicalHarness(tmp_path / "revoked-growth-replay-parent.sqlite3")
    # Opt into the same-engine real master owners for this scope control only.
    # Both stores exist; the changed scope must exclude the actual plan parent.
    harness.bundle.tenant_repository.save_tenant(Tenant(tenant_id="tenant-a", tenant_name="Synthetic scoped tenant"))
    brand_id = "unit-decision-reference-brand"
    harness.bundle.brand_repository.save_brand(Brand(
        brand_id=brand_id, tenant_id="tenant-a", brand_code="unit-scoped", brand_name="Synthetic scoped brand",
    ))
    harness.bundle.address_location_repository.save_address(AddressLocation(
        address_id="unit-scoped-address", tenant_id="tenant-a",
        raw_address="Synthetic scoped address", normalized_address="Synthetic scoped address",
    ))
    for store_id in ("store-decision-reference", "unit-other-store"):
        harness.bundle.store_repository.save_store(Store(
            store_id=store_id, tenant_id="tenant-a", brand_id=brand_id,
            address_id="unit-scoped-address", store_name="Synthetic scoped store", region_code="north",
        ))
    plan = _decision_reference_plan(harness, "tenant-a", ("unit-replay-source",), brand_id=brand_id)
    headers = {**_headers("tenant-a", subject="unit-actual-reviewer"), "x-operator-role": "ops-lead",
               "idempotency-key": "unit-existing-revoked-replay", "X-Store-Ids": "store-decision-reference"}
    try:
        with TestClient(harness.app(priceops_scope_masters=True)) as client:
            _action_id, approval_id = _submit_reference_growth(client, plan)
            command = {"approvalId": approval_id, "action": "reject",
                       "reason": "Unit-only existing command before scope is removed"}
            accepted = client.post(f"{BASE}/governance/decisions", headers=headers, json=command)
            assert accepted.status_code == 200, accepted.text
            repositories = {name: DurableOperatorDomainStateRepository(harness.document_store, name)
                            for name in ("growth", "governance")}
            before = {name: repository.load("tenant-a") for name, repository in repositories.items()}
            revoked = client.post(f"{BASE}/governance/decisions", headers={**headers, "X-Store-Ids": "unit-other-store"}, json=command)
            assert revoked.status_code in {403, 404}, revoked.text
            # Exercise the precise scoped read seam too. No visible plans means
            # copied old receipts cannot authorize a historical Growth action.
            owner = ScopedPriceOpsReadRepository(harness.priceops("tenant-a"), [])
            growth_state = project_growth_read_state(before["growth"], [])
            state = project_governance_read_state(before["governance"], [], growth_state)
            growth = GrowthService(initial_state=growth_state, seed_fixtures=False,
                priceops_repository=owner, tenant_id="tenant-a", require_canonical=True)
            service = GovernanceService(initial_state=state, growth_service=growth, seed_fixtures=False,
                sitescore_decision_repository=harness.decisions("tenant-a"),
                avm_repository=harness.avm("tenant-a"), netplan_repository=harness.netplan("tenant-a"),
                priceops_repository=owner, tenant_id="tenant-a", require_canonical=True)
            assert service.has_visible_growth_decision_receipt(
                approval_id=approval_id, action="reject", reason=command["reason"], role="ops-lead",
                actor_name="unit-actual-reviewer", tenant_id="tenant-a", idempotency_key=headers["idempotency-key"],
            ) is False
            # The command-side backstop must refuse a disappearing receipt
            # before it can dispatch any new decision/audit, even if called directly.
            visible_growth = GrowthService(initial_state=before["growth"], seed_fixtures=False,
                priceops_repository=harness.priceops("tenant-a"), tenant_id="tenant-a", require_canonical=True)
            missing = {**before["governance"], "commandIdempotency": {}}
            missing_service = GovernanceService(initial_state=missing, growth_service=visible_growth,
                seed_fixtures=False, priceops_repository=harness.priceops("tenant-a"),
                sitescore_decision_repository=harness.decisions("tenant-a"),
                avm_repository=harness.avm("tenant-a"), netplan_repository=harness.netplan("tenant-a"),
                tenant_id="tenant-a", require_canonical=True)
            missing_before = missing_service.export_state()
            growth_before = visible_growth.export_state()
            with pytest.raises(GovernanceNotFound, match="bound decision replay unavailable"):
                missing_service.decide(approval_id=approval_id, action="reject", reason=command["reason"],
                    role="ops-lead", actor_name="unit-actual-reviewer", tenant_id="tenant-a",
                    idempotency_key=headers["idempotency-key"], replay_only=True)
            assert missing_service.export_state() == missing_before
            assert visible_growth.export_state() == growth_before
            assert {name: repository.load("tenant-a") for name, repository in repositories.items()} == before
    finally:
        harness.close()
