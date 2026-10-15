"""Public HTTP contract for the canonical Operator pending collection.

Inputs are explicitly synthetic and local to each owned SQLite fixture. Pricing
uses the existing POC algorithm; frozen SiteScore reports are test input, never
model inference or source/production approval. Native workflow owners create the
pending records. Reads must preserve them and their original decision owners.

This proposal is source-only until the integrated product is actually tested.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from apps.api.app.routes.priceops import create_priceops_router
from apps.api.oday_api.security import dependencies
from modules.intervention import InterventionKind, InterventionStatus, InterventionWorkflow
from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from shared.api.errors import install_error_handlers
from shared.domain import AddressLocation, Brand, CandidateSite, Listing, Store
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import (
    DurableOperatorDomainStateRepository,
    TenantScopedDocumentStore,
)
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import (
    DurableApprovalRepository,
    DurableAVMRepository,
    DurableDecisionStore,
    DurableListingRepository,
    DurableNetPlanRepository,
    DurableSiteScoreRepository,
)
from shared.workflow.sitescore import DecisionAction, DecisionStatus, SiteScoreDecisionWorkflow
from tests.integration.test_priceops_growth_main_owner import HEADERS, _main_provider, _seed_master
from tests.unit.test_network_canonical_review_projection import report as synthetic_frozen_report

BASE = "/api/v1"
TENANT = "tenant-a"
VISIBLE_STORE = "store-tenant-a"
HIDDEN_STORE = "excluded-store"
AXES = [("x-store-ids", VISIBLE_STORE), ("x-brand-ids", "brand-tenant-a"), ("x-region-ids", "north")]


@dataclass
class PendingContext:
    client: TestClient
    bundle: Any
    documents: Any

    def scoped(self, tenant: str = TENANT):
        return TenantScopedDocumentStore(self.documents, tenant)

    def persisted(self):
        """Observe business owners, excluding allowed security-read audit logs."""
        growth = DurableOperatorDomainStateRepository(self.documents, "growth")
        govern = DurableOperatorDomainStateRepository(self.documents, "governance")
        decisions = DurableDecisionStore(self.scoped())
        approvals = DurableApprovalRepository(self.scoped())
        native = self.bundle.intervention_repository
        pricing = self.bundle.priceops_repository
        plans = pricing.list_plans()
        return deepcopy({
            "growth": growth.load(TENANT),
            "govern": govern.load(TENANT),
            "decisions": decisions.list_decisions(),
            "decisionReports": [decisions.get_report(row.decision_id) for row in decisions.list_decisions()],
            "signatures": [approvals.get(row.approval_id) for row in decisions.list_decisions() if row.approval_id],
            "interventions": [*native.list_by_store(VISIBLE_STORE), *native.list_by_store(HIDDEN_STORE)],
            "pricingPlans": plans,
            "pricingOptimizations": [pricing.get_optimization(row.plan_id) for row in plans],
            "pricingEvaluations": [pricing.get_evaluation(row.plan_id) for row in plans],
            "pricingApprovals": [pricing.list_approvals(row.plan_id) for row in plans],
        })


@pytest.fixture
def pending_context(tmp_path, monkeypatch):
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setattr(dependencies, "_default_boundary", None)
    bundle = _durable_bundle(tmp_path / "own-shared-pending-http.sqlite3")
    _seed_master(bundle)
    bundle.brand_repository.save_brand(Brand(
        brand_id="excluded-brand", tenant_id=TENANT,
        brand_code="synthetic-excluded", brand_name="Explicit synthetic excluded brand",
    ))
    bundle.store_repository.save_store(Store(
        store_id=HIDDEN_STORE, tenant_id=TENANT, brand_id="excluded-brand",
        address_id="address-tenant-a", store_name="Explicit synthetic excluded store", region_code="south",
    ))
    documents = SqliteDocumentStore(bundle.engine)

    def scoped(tenant):
        return TenantScopedDocumentStore(documents, tenant)

    app = FastAPI()
    app.state.job_queue = bundle.job_queue
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-shared-pending")
        response = await call_next(request)
        response.headers["x-correlation-id"] = request.state.correlation_id
        return response

    app.include_router(create_priceops_router(
        repository=bundle.priceops_repository, store_repository=bundle.store_repository,
        brand_repository=bundle.brand_repository, audit_log=bundle.audit_log,
        job_queue=bundle.job_queue, require_durable_commands=True, runtime_mode="poc",
    ), prefix=BASE)
    app.include_router(create_operator_router(
        audit_log=bundle.audit_log, document_store=documents, job_queue=bundle.job_queue,
        intake_workflow_store=bundle.assisted_intake_store,
        live_repository=OperatorLiveRepository(bundle), require_live_data=True,
        priceops_repository_for_tenant=_main_provider(bundle.priceops_repository, documents),
        priceops_store_repository=bundle.store_repository,
        priceops_brand_repository=bundle.brand_repository,
        listing_repository_for_tenant=lambda tenant: DurableListingRepository(scoped(tenant)),
        sitescore_repository_for_tenant=lambda tenant: DurableSiteScoreRepository(scoped(tenant)),
        sitescore_decision_repository_for_tenant=lambda tenant: DurableDecisionStore(scoped(tenant)),
        avm_repository_for_tenant=lambda tenant: DurableAVMRepository(scoped(tenant)),
        netplan_repository_for_tenant=lambda tenant: DurableNetPlanRepository(scoped(tenant)),
    ), prefix=BASE)
    client = TestClient(app, headers=HEADERS)
    try:
        # Real HTTP commands, durable owner and explicit POC algorithm. Neither
        # optimizing a plan nor creating a Growth draft is a submission.
        for plan, store in (("visible-plan", VISIBLE_STORE), ("excluded-plan", HIDDEN_STORE)):
            response = client.post(BASE + "/priceops/plans", headers={"Idempotency-Key": "create-" + plan}, json={
                "tenant_id": TENANT, "plan_id": plan,
                "items": [{"item_id": "item-" + plan, "store_id": store, "machine_type": "washer",
                           "unit_cost": 30, "current_price": 80, "baseline_demand": 40,
                           "elasticity_value": -1, "confidence": 0.8,
                           "applicable_min_price": 60, "applicable_max_price": 100}],
            })
            assert response.status_code == 201, response.text
            for operation in ("simulate", "optimize"):
                response = client.post(BASE + "/priceops/plans/" + plan + "/" + operation,
                                       headers={"Idempotency-Key": operation + "-" + plan},
                                       json={"reason": "Explicit synthetic shared-pending regression"})
                assert response.status_code == 200, response.text
        yield PendingContext(client, bundle, documents)
    finally:
        client.close()
        bundle.engine.close()


def _growth_submission(context, plan="visible-plan", *, suffix="one"):
    client = context.client
    response = client.get(BASE + "/operator/growth/segments")
    assert response.status_code == 200, response.text
    segment = next(row for row in response.json()["items"] if row["planId"] == plan)
    response = client.get(BASE + "/operator/growth/recommendations")
    assert response.status_code == 200, response.text
    recommendation = next(row for row in response.json()["items"] if row["segmentId"] == segment["id"])
    created = client.post(BASE + "/operator/growth/actions", headers={"Idempotency-Key": "growth-create-" + suffix}, json={
        "name": "Explicit synthetic shared-pending " + suffix, "kind": "priceops",
        "segmentId": segment["id"], "sourceRecommendationId": recommendation["id"], "store": segment["storeId"],
        "objective": "Review explicit local input", "targetLift": 0.1,
        "rationale": "Local synthetic regression; not measured performance",
        "rollbackPlan": "Return to the recorded current price after independent review",
    })
    assert created.status_code == 200, created.text
    action = created.json()["id"]
    persisted = client.get(BASE + "/operator/growth/actions/" + action)
    assert persisted.status_code == 200 and persisted.json()["segmentId"] == segment["id"]
    assert persisted.json()["store"] == segment["storeId"]
    submitted = client.post(BASE + "/operator/growth/actions/" + action + "/submit",
                            headers={"Idempotency-Key": "growth-submit-" + suffix}, json={})
    assert submitted.status_code == 200, submitted.text
    return action, submitted.json()["approval"]["id"]


def _native_intervention_pending(context, store=VISIBLE_STORE, *, suffix="one"):
    workflow = InterventionWorkflow(repository=context.bundle.intervention_repository,
                                    audit_log=context.bundle.audit_log)
    start = datetime(2026, 11, 1, tzinfo=UTC)
    case = workflow.open_case(
        store_id=store, kind=InterventionKind.PRICE_CHANGE, trigger_ref="synthetic-test-input:" + suffix,
        expected_outcome="Explicit synthetic draft, no observed outcome or financial claim",
        planned_start=start, planned_end=start + timedelta(days=14), created_by="synthetic-native-maker",
    )
    workflow.check_eligibility(case.intervention_id, eligible=True, actor="synthetic-native-maker")
    workflow.propose_action(case.intervention_id, actor="synthetic-native-maker",
                            action_spec={"price_change_pct": -5, "rollback_plan": "Restore original price"})
    workflow.check_conflict(case.intervention_id, actor="synthetic-native-maker")
    pending = workflow.submit_for_approval(case.intervention_id, actor="synthetic-native-maker")
    assert pending.status is InterventionStatus.PENDING_APPROVAL and pending.approval is None
    assert context.bundle.intervention_repository.get(pending.intervention_id) == pending
    return pending


def _sitescore_owner(context, *, suffix="one", store=VISIBLE_STORE, signatures=True, submit=True):
    """Persist a labelled synthetic parent and use real review/signature owner."""
    scoped = context.scoped()
    listings = DurableListingRepository(scoped)
    metadata = DurableAssistedIntakeRepository(scoped)
    master = context.bundle.store_repository.get_store(store)
    address = AddressLocation(address_id="synthetic-listing-address-" + suffix, tenant_id=TENANT,
                              raw_address="Explicit local synthetic parent " + suffix)
    listing = Listing(listing_id="synthetic-listing-" + suffix, address_id=address.address_id,
                      source_id="explicit-local-test", source_listing_id=suffix,
                      rent_amount=1000, area_ping=30, listing_status="active")
    listings.save_listing(listing, address, ListingDedupKey("explicit-local-test", suffix, suffix, 1000, 30))
    candidate_id = "synthetic-candidate-" + suffix
    draft = CandidateSiteDraft(listing=listing, address=address,
                               candidate_site=CandidateSite(candidate_site_id=candidate_id,
                                                            listing_id=listing.listing_id,
                                                            address_id=address.address_id),
                               heat_zone_id="synthetic-zone-" + suffix)
    listings.save_candidate(draft)
    metadata.save_listing_metadata(listing.listing_id, {
        "tenantId": TENANT, "storeId": store, "brandId": master.brand_id, "regionId": master.region_code,
        "assignedAreaId": "synthetic-area-" + suffix, "heatZoneId": "synthetic-zone-" + suffix,
    })
    scores = DurableSiteScoreRepository(scoped)
    decisions = DurableDecisionStore(scoped)
    approvals = DurableApprovalRepository(scoped)
    workflow = SiteScoreDecisionWorkflow(store=decisions, approval_store=approvals if signatures else None)
    value = scores.save_report(synthetic_frozen_report(candidate_id, heat_zone_id=draft.heat_zone_id))
    opened = workflow.open_decision(value, created_by="synthetic-network-maker", tenant_id=TENANT)
    decision = (workflow.submit_for_review(opened.decision_id, submitted_by="synthetic-network-maker")
                if submit else opened)
    if submit and signatures:
        assert decision.status is DecisionStatus.PENDING_REVIEW
        assert decision.approval_id != decision.decision_id
        assert approvals.get(decision.approval_id).approval_status == "pending"
    else:
        assert decision.approval_id is None
    draft.review_id = decision.decision_id
    listings.save_candidate(draft)
    return workflow, scores, decisions, approvals, decision


def _read_public(context, *, headers=None):
    responses = {name: context.client.get(BASE + "/operator/" + path, headers=headers)
                 for name, path in (("govern", "governance/snapshot"),
                                    ("bootstrap", "bootstrap"), ("today", "today"))}
    for response in responses.values():
        assert response.status_code == 200, response.text
    return {name: response.json() for name, response in responses.items()}


def _assert_shared_pending(bodies, expected):
    govern = bodies["govern"]
    pending = [row for row in govern["approvals"] if row["status"] == "pending"]
    assert {row["id"] for row in pending} == expected
    assert len(pending) == govern["counts"]["pendingApprovals"] == len(expected)
    for name in ("bootstrap", "today"):
        envelope = bodies[name]
        # This exact public assertion reproduces the observed Growth 0 vs 1
        # defect before the list, KPI and search consistency checks below.
        assert envelope["header"]["counts"]["approvals"] == len(expected)
        assert envelope["meta"]["counts"]["approvals"] == len(expected)
        assert envelope["meta"]["recordCounts"]["approvals"] == len(expected)
        section = envelope["meta"]["sections"]["approvals"]
        assert section["state"] == "available" and section["complete"] is True
        assert section["recordCount"] == len(expected)
        for rows in (envelope["approvals"], envelope["decisions"], envelope["today"]["decisions"]):
            assert {row["id"] for row in rows} == expected
            assert len(rows) == len(expected)
        cards = {row["label"]: row for row in envelope["today"]["kpis"]}
        label = "待核准項" if envelope["meta"]["role"]["id"] == "pm-audit" else "待我核准"
        assert cards[label]["value"] == str(len(expected))
        assert cards[label]["availability"] == "available"
        assert expected <= {row["entityId"] for row in envelope["search"]["items"]}


def test_empty_pending_is_a_successful_zero_not_unsubmitted_priceops(pending_context):
    before = pending_context.persisted()
    bodies = _read_public(pending_context)
    assert pending_context.persisted() == before
    _assert_shared_pending(bodies, set())
    assert {row["id"] for row in bodies["govern"]["approvals"] if row["module"] == "PriceOps"} == {
        "visible-plan", "excluded-plan",
    }
    assert {row["status"] for row in bodies["govern"]["approvals"]} == {"not_submitted"}


def test_growth_submission_has_same_pending_in_bootstrap_today_and_governance(pending_context):
    action, approval = _growth_submission(pending_context)
    before = pending_context.persisted()
    bodies = _read_public(pending_context)
    # Security-read audit events may append, but these business owners may not
    # persist a projected aggregate or mutate the submitted workflow.
    assert pending_context.persisted() == before
    row = next(row for row in bodies["govern"]["approvals"] if row["id"] == approval)
    assert row["module"] == "Growth" and row["entityRef"] == action and row["status"] == "pending"
    _assert_shared_pending(bodies, {approval})


def _read_without_business_changes(context, *, headers=None):
    before = context.persisted()
    bodies = _read_public(context, headers=headers)
    assert context.persisted() == before
    return bodies


def test_native_intervention_is_retained_as_original_owner_extension_not_govern_decision(pending_context):
    native = _native_intervention_pending(pending_context)
    _, growth_approval = _growth_submission(pending_context)
    bodies = _read_without_business_changes(pending_context)
    _assert_shared_pending(bodies, {native.intervention_id, growth_approval})
    row = next(row for row in bodies["govern"]["approvals"] if row["id"] == native.intervention_id)
    assert row["module"] == "Store Ops" and row["status"] == "pending"
    assert row["nativeTarget"] == {
        "workspace": "today", "tab": "interventions", "entityId": native.intervention_id,
    }
    assert row["actionAuthority"]["decide"] is False
    assert row["actionAuthority"]["reason"] == "NATIVE_OWNER_REVIEW_REQUIRED"
    before = pending_context.persisted()
    denied = pending_context.client.post(BASE + "/operator/governance/decisions",
                                        headers={"Idempotency-Key": "not-the-native-intervention-owner"},
                                        json={"approvalId": native.intervention_id, "action": "approve",
                                              "reason": "Must use the original full native review owner"})
    assert denied.status_code == 404, denied.text
    assert pending_context.persisted() == before
    preserved = pending_context.bundle.intervention_repository.get(native.intervention_id)
    assert preserved.status is InterventionStatus.PENDING_APPROVAL and preserved.approval is None


def test_signed_sitescore_is_one_approval_despite_different_decision_and_signature_ids(pending_context):
    _, _, _, signatures, decision = _sitescore_owner(pending_context)
    bodies = _read_without_business_changes(pending_context)
    _assert_shared_pending(bodies, {decision.approval_id})
    rows = [row for row in bodies["govern"]["approvals"] if row["module"] == "SiteScore"]
    assert [(row["id"], row["decisionId"], row["status"]) for row in rows] == [
        (decision.approval_id, decision.decision_id, "pending"),
    ]
    assert signatures.get(decision.approval_id).decision_id == decision.decision_id
    assert rows[0]["nativeTarget"] == {
        "workspace": "network", "tab": "review", "entityId": decision.candidate_site_id,
        "candidateId": decision.candidate_site_id, "reviewId": decision.decision_id,
    }
    assert rows[0]["entityRef"] == decision.candidate_site_id
    assert rows[0]["actionAuthority"]["decide"] is False
    assert rows[0]["actionAuthority"]["reason"] == "NATIVE_OWNER_REVIEW_REQUIRED"
    for name in ("bootstrap", "today"):
        assert decision.decision_id not in {row["id"] for row in bodies[name]["approvals"]}
    before = pending_context.persisted()
    denied = pending_context.client.post(BASE + "/operator/governance/decisions",
                                        headers={"Idempotency-Key": "not-the-native-network-owner"},
                                        json={"approvalId": decision.approval_id, "action": "approve",
                                              "reason": "Must use the original native Network review"})
    # Preserve the existing canonical SiteScore owner-required status. Native
    # Intervention's newly projected ID deliberately retains its different404.
    assert denied.status_code == 409, denied.text
    assert pending_context.persisted() == before


@pytest.mark.parametrize("status", ["SYSTEM_RECOMMENDED", "PENDING_REVIEW", "DRAFT"])
def test_unsigned_sitescore_stays_native_review_task_and_is_not_an_approval(pending_context, status):
    workflow, _, decisions, _, decision = _sitescore_owner(
        pending_context, signatures=False, submit=status != "SYSTEM_RECOMMENDED",
    )
    if status == "DRAFT":
        decision = workflow.decide(decision.decision_id, action=DecisionAction.REQUEST_REVISION,
                                   actor="synthetic-independent-network-checker",
                                   reason="Explicit synthetic native revision request").decision
    assert decision.status.value == status and decision.approval_id is None
    bodies = _read_without_business_changes(pending_context)
    _assert_shared_pending(bodies, set())
    for name in ("bootstrap", "today"):
        matches = [row for row in bodies[name]["workQueue"] if row["id"] == decision.decision_id]
        assert len(matches) == 1
        assert matches[0]["status"] == status
        assert matches[0]["target"] == {
            "workspace": "network", "entityId": decision.candidate_site_id, "tab": "review",
            "candidateId": decision.candidate_site_id, "reviewId": decision.decision_id,
        }
        assert bodies[name]["header"]["counts"]["taskCenter"] == len(bodies[name]["workQueue"])
    assert decisions.get_decision(decision.decision_id) == decision


def test_new_report_round_is_not_collapsed_into_prior_approved_candidate(pending_context):
    workflow, scores, decisions, signatures, original = _sitescore_owner(pending_context)
    original = workflow.decide(original.decision_id, action=DecisionAction.APPROVE,
                               actor="synthetic-independent-network-checker",
                               reason="Recorded synthetic prior-round approval").decision
    retained = deepcopy((decisions.get_decision(original.decision_id),
                         decisions.get_report(original.decision_id), signatures.get(original.approval_id)))
    newer = scores.save_report(synthetic_frozen_report(
        original.candidate_site_id, model_version="explicit-synthetic-newer-report",
        heat_zone_id="synthetic-zone-one",
    ))
    new = workflow.open_decision(newer, created_by="synthetic-next-round-maker", tenant_id=TENANT,
                                 previous_decision_id=original.decision_id)
    new = workflow.submit_for_review(new.decision_id, submitted_by="synthetic-next-round-maker")
    assert new.decision_id != original.decision_id and new.approval_id != original.approval_id
    assert new.previous_decision_id == original.decision_id
    bodies = _read_without_business_changes(pending_context)
    _assert_shared_pending(bodies, {new.approval_id})
    rows = {row["id"]: row for row in bodies["govern"]["approvals"] if row["module"] == "SiteScore"}
    assert rows[original.approval_id]["status"] == "approved"
    assert rows[original.approval_id]["decisionId"] == original.decision_id
    assert rows[new.approval_id]["status"] == "pending" and rows[new.approval_id]["decisionId"] == new.decision_id
    assert (decisions.get_decision(original.decision_id), decisions.get_report(original.decision_id),
            signatures.get(original.approval_id)) == retained


@pytest.mark.parametrize(("header", "allowed"), AXES)
def test_growth_pending_scope_matches_true_parents_and_foreign_tenant_is_empty(pending_context, header, allowed):
    _, visible = _growth_submission(pending_context, suffix="visible")
    _, hidden = _growth_submission(pending_context, "excluded-plan", suffix="hidden")
    _assert_shared_pending(_read_without_business_changes(pending_context), {visible, hidden})
    bodies = _read_without_business_changes(pending_context, headers={**HEADERS, header: allowed})
    _assert_shared_pending(bodies, {visible})
    assert hidden not in {row["id"] for row in bodies["govern"]["approvals"]}
    foreign = _read_without_business_changes(pending_context, headers={**HEADERS, "x-tenant-id": "tenant-b"})
    _assert_shared_pending(foreign, set())
    assert {visible, hidden}.isdisjoint(row["id"] for row in foreign["govern"]["approvals"])


@pytest.mark.parametrize(("header", "allowed"), AXES)
def test_native_and_sitescore_pending_keep_actual_store_brand_region_scope(pending_context, header, allowed):
    visible_native = _native_intervention_pending(pending_context, suffix="visible")
    hidden_native = _native_intervention_pending(pending_context, HIDDEN_STORE, suffix="hidden")
    *_, visible = _sitescore_owner(pending_context, suffix="visible")
    *_, hidden = _sitescore_owner(pending_context, suffix="hidden", store=HIDDEN_STORE)
    _assert_shared_pending(_read_without_business_changes(pending_context), {
        visible_native.intervention_id, hidden_native.intervention_id, visible.approval_id, hidden.approval_id,
    })
    bodies = _read_without_business_changes(pending_context, headers={**HEADERS, header: allowed})
    _assert_shared_pending(bodies, {visible_native.intervention_id, visible.approval_id})
    ids = {row["id"] for row in bodies["govern"]["approvals"]}
    assert {hidden_native.intervention_id, hidden.approval_id}.isdisjoint(ids)


@pytest.mark.parametrize(("header", "allowed"), [
    ("x-assigned-area-ids", "synthetic-area-visible"), ("x-heat-zone-ids", "synthetic-zone-visible"),
])
def test_area_and_heat_scope_need_actual_parent_evidence_not_tenant_or_store_only(pending_context, header, allowed):
    native = _native_intervention_pending(pending_context, suffix="visible")
    *_, visible = _sitescore_owner(pending_context, suffix="visible")
    *_, hidden = _sitescore_owner(pending_context, suffix="hidden", store=HIDDEN_STORE)
    _assert_shared_pending(_read_without_business_changes(pending_context), {
        native.intervention_id, visible.approval_id, hidden.approval_id,
    })
    bodies = _read_without_business_changes(pending_context, headers={**HEADERS, header: allowed})
    _assert_shared_pending(bodies, {visible.approval_id})
    # The intervention has a real store, but no assigned-area/zone evidence.
    # Missing evidence on a restricted axis cannot turn into an empty grant.
    assert native.intervention_id not in {row["id"] for row in bodies["govern"]["approvals"]}
    excluded = _read_without_business_changes(pending_context, headers={**HEADERS, header: "not-an-owned-binding"})
    _assert_shared_pending(excluded, set())


def test_read_only_operator_auditor_sees_same_pending_without_new_decision_authority(pending_context):
    _, approval = _growth_submission(pending_context)
    headers = {**HEADERS, "x-subject-id": "synthetic-read-only-auditor", "x-roles": "auditor",
               "x-operator-role": "pm-audit", "x-store-ids": VISIBLE_STORE}
    bodies = _read_without_business_changes(pending_context, headers=headers)
    _assert_shared_pending(bodies, {approval})
    assert bodies["govern"]["actionAuthority"]["verified"] is True
    assert bodies["govern"]["actionAuthority"]["decide"] is False
    before = pending_context.persisted()
    denied = pending_context.client.post(BASE + "/operator/governance/decisions", headers=headers,
                                        json={"approvalId": approval, "action": "approve",
                                              "reason": "Read visibility does not grant a decision"})
    assert denied.status_code == 403, denied.text
    assert pending_context.persisted() == before


def test_owner_outage_is_not_reported_as_a_complete_empty_pending_collection(pending_context, monkeypatch):
    _assert_shared_pending(_read_without_business_changes(pending_context), set())
    _, approval = _growth_submission(pending_context)
    _assert_shared_pending(_read_without_business_changes(pending_context), {approval})
    before = pending_context.persisted()

    def unavailable(_store_id):
        raise RuntimeError("Explicit synthetic canonical store-owner outage")

    monkeypatch.setattr(pending_context.bundle.store_repository, "get_store", unavailable)
    for path in ("bootstrap", "today", "governance/snapshot"):
        response = pending_context.client.get(BASE + "/operator/" + path)
        assert response.status_code == 503, response.text
    assert pending_context.persisted() == before
