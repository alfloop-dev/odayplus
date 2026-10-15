"""Explicit offline durable producers and the existing guarded, masked GETs.

Fixture setup records synthetic decisions. Reads must not change business rows,
load a model or infer. These records never represent production grants.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import create_network_listings_sub_router
from apps.api.app.routes.operator_modules.network_reviews import create_network_review_sub_router
from apps.api.app.routes.operator_modules.network_scoring import create_network_scoring_sub_router
from apps.api.oday_api.security.dependencies import (
    OPERATOR_NETWORK_READ_RESOURCE,
    build_engine,
    require_operator_permission,
)
from modules.listing.domain.models import ListingDedupKey
from modules.opsboard.application.network_listings import NetworkListingService
from modules.opsboard.application.network_scoring import NetworkScoringService
from shared.auth import Action
from shared.domain.models import MEASUREMENT_MEASURED, MEASUREMENT_UNMEASURED
from shared.workflow.sitescore import DecisionStatus
from tests.unit.test_network_canonical_review_projection import report
from tests.unit.test_network_review_canonical_action_metadata import (
    ACTOR,
    TENANT,
    components,
    decision_args,
    install_real_related_producers,
)

HEADERS = {"X-Subject-Id": ACTOR, "X-Roles": "platform_admin,operator_viewer",
           "X-Tenant-Id": TENANT, "X-Operator-Role": "pm-audit"}


class ReadOnlyRuntime:
    calls = 0
    loads = 0

    def infer(self, **kwargs):
        self.calls += 1
        raise AssertionError("read-only producer bridge may not infer")

    def resolve(self, **kwargs):
        self.loads += 1
        raise AssertionError("read-only producer bridge may not load artifacts")


def business_rows(engine):
    # The existing HTTP guard may separately append security authorization
    # evidence. Do not classify that legitimate read audit as a business write.
    return {
        "documents": [dict(row) for row in engine.query(
            "SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id")],
        "sequences": [dict(row) for row in engine.query(
            "SELECT * FROM durable_sequences ORDER BY 1, 2")],
        "audits": [dict(row) for row in engine.query(
            "SELECT * FROM durable_audit_events WHERE event_type != 'security.authorization' ORDER BY event_id")],
    }


def actual_readback(path: Path, action: str, *, latest: int = 1, opened: bool = False, measured: bool = True,
                    other_first: bool = False, ambiguous_pending: bool = False):
    engine, scores, decisions, audit, workflow, reviews = components(path)
    approvals, intake, listings = install_real_related_producers(engine, decisions, workflow, reviews)
    repository = listings._listing_repository
    draft = repository.list_candidates()[0]
    listing = replace(draft.listing, rent_amount=48000, area_ping=22, floor="1F",
                      snapshot_id="synthetic-snapshot-a")
    # This is an explicit local producer verdict, not a missing-value default.
    object.__setattr__(listing, "hard_rule_failures", [])
    address = replace(draft.address, normalized_address="offline recorded address",
                      latitude=25.03, longitude=121.56, geocode_confidence=0.95,
                      h3_res_9="offline-recorded-cell")
    repository.save_listing(listing, address, ListingDedupKey(
        "offline-source", "offline-property", address.normalized_address, 48000, 22))
    repository.save_candidate(replace(draft, listing=listing, address=address,
        candidate_site=replace(draft.candidate_site, site_status="new") if action == "UNSCORED" else draft.candidate_site,
        heat_zone_id="synthetic-zone-a"))
    target_id = draft.candidate_site.candidate_site_id
    if other_first:
        first_report = scores.save_report(report(candidate_id=target_id))
        target_id = "synthetic-review-candidate-b"
        second_listing = replace(listing, listing_id="offline-listing-b", address_id="offline-address-b")
        object.__setattr__(second_listing, "hard_rule_failures", [])
        second_address = replace(address, address_id="offline-address-b", normalized_address="offline recorded address B")
        repository.save_listing(second_listing, second_address,
            ListingDedupKey("offline-source", "offline-property-b", second_address.normalized_address, 48000, 22))
        repository.save_candidate(replace(draft, listing=second_listing, address=second_address,
            candidate_site=replace(draft.candidate_site, candidate_site_id=target_id,
                listing_id=second_listing.listing_id, address_id=second_address.address_id), heat_zone_id="synthetic-zone-a"))
        # A fresh ordinary service sees both actual durable candidate records.
        reviews._candidate_workflow_service = NetworkListingService(listing_repository=repository,
            intake_repository=intake, seed_fixtures=False, tenant_id=TENANT, review_decision_repository=decisions)
    frozen = decision = None
    if action != "UNSCORED":
        frozen = scores.save_report(report(candidate_id=target_id, confidence=0 if measured else None,
            confidence_status=MEASUREMENT_MEASURED if measured else MEASUREMENT_UNMEASURED))
        decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
        if action == "PENDING":
            workflow.submit_for_review(decision.decision_id, submitted_by=ACTOR)
            if ambiguous_pending:
                extra = workflow.open_decision(frozen, created_by="offline-other-author", tenant_id=TENANT)
                workflow.submit_for_review(extra.decision_id, submitted_by=ACTOR)
        elif action == "UNKNOWN_APPROVED":
            decisions.save_decision(replace(decision, status=DecisionStatus.APPROVED, network_action=None))
        else:
            reviews.decide_review(**decision_args(decision.decision_id, action))
    if other_first:
        # The actual producer orders newest submissions first. Create the
        # unrelated candidate's Decision last, without rearranging read JSON.
        first_decision = workflow.open_decision(first_report, created_by="offline-author", tenant_id=TENANT)
        reviews.decide_review(**{**decision_args(first_decision.decision_id, "GO"),
            "idempotency_key": "offline-other-review-key"})
    if opened:
        current = next(row for row in repository.list_candidates() if row.candidate_site.candidate_site_id == target_id)
        repository.save_candidate(replace(current, candidate_site=replace(current.candidate_site, site_status="opened")))
    for version in range(2, latest + 1):
        scores.save_report(replace(report(candidate_id=target_id, confidence=0, confidence_status=MEASUREMENT_MEASURED),
                                   model_version=f"offline-newer-model-{version}"))
    runtime = ReadOnlyRuntime()
    scoring = NetworkScoringService(seed_fixtures=False, require_canonical=True,
        listing_repository=repository, sitescore_repository=scores, model_runtime=runtime, tenant_id=TENANT)
    app = FastAPI()
    auth_engine = build_engine(audit_log=audit)
    # The ordinary tenant resolver creates a new service from current durable
    # rows for every GET. Preserve that read boundary instead of holding the
    # pre-decision service object's presentation cache.
    def current_listings(_request=None):
        return NetworkListingService(listing_repository=repository, intake_repository=intake,
            seed_fixtures=False, tenant_id=TENANT, review_decision_repository=decisions)
    app.include_router(create_network_listings_sub_router(listings,
        require_view_permission_fn=require_operator_permission("listing", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE, engine=auth_engine),
        require_write_permission_fn=require_operator_permission("listing", Action.UPDATE, engine=auth_engine), service_resolver=current_listings))
    def scope_snapshot(request):
        return current_listings().snapshot(tenant_id=request.state.operator_principal.tenant_id)
    app.include_router(create_network_scoring_sub_router(scoring,
        require_view_permission_fn=require_operator_permission("sitescore", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE, engine=auth_engine),
        require_write_permission_fn=require_operator_permission("sitescore", Action.EXECUTE, engine=auth_engine),
        read_scope_snapshot_fn=scope_snapshot))
    app.include_router(create_network_review_sub_router(reviews,
        require_view_permission_fn=require_operator_permission("sitescore", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE, engine=auth_engine),
        require_decide_permission_fn=require_operator_permission("sitescore", Action.APPROVE, engine=auth_engine),
        read_scope_snapshot_fn=scope_snapshot))
    before = business_rows(engine)
    security_before = len(engine.query("SELECT event_id FROM durable_audit_events WHERE event_type = 'security.authorization'"))
    client = TestClient(app)
    snapshots = {}
    for key, route in (("network", "network-listings"), ("scoring", "network-scoring"), ("reviews", "network-reviews")):
        response = client.get(f"/{route}", headers=HEADERS)
        assert response.status_code == 200, response.text
        snapshots[key] = response.json()
    assert business_rows(engine) == before
    assert runtime.calls == runtime.loads == 0
    security_delta = len(engine.query("SELECT event_id FROM durable_audit_events WHERE event_type = 'security.authorization'")) - security_before
    assert security_delta == 3
    persisted = decisions.get_decision(decision.decision_id) if decision else None
    approval = approvals.get(persisted.approval_id) if persisted and persisted.approval_id else None
    payload = {**snapshots, "candidateId": target_id,
               "reviewId": decision.decision_id if decision else None, "frozenReportId": frozen.report_id if frozen else None,
               "frozenReportVersion": frozen.report_version if frozen else None, "latestReportVersion": latest if frozen else None,
               "action": action, "opened": opened, "approvalId": approval.approval_id if approval else None,
               "readProof": {"businessReadMutations": 0, "modelLoads": runtime.loads, "inferenceCalls": runtime.calls,
                             "securityAuthorizationAuditAppends": security_delta,
                             "tenantId": TENANT, "actorId": ACTOR, "localOfflineHeaderTrust": True,
                             "realProductionGrantsEstablished": False}}
    engine.close()
    return payload


def generate_readbacks(directory: Path):
    cases = {action: actual_readback(directory / f"{action}.sqlite", action)
             for action in ("GO", "WAIT", "RETURN", "REJECT", "PENDING", "UNKNOWN_APPROVED")}
    cases["GO_V2"] = actual_readback(directory / "GO_V2.sqlite", "GO", latest=2)
    cases["OPENED_V3"] = actual_readback(directory / "OPENED_V3.sqlite", "GO", latest=3, opened=True)
    cases["GO_UNMEASURED"] = actual_readback(directory / "GO_UNMEASURED.sqlite", "GO", measured=False)
    cases["SECOND_PENDING"] = actual_readback(directory / "SECOND_PENDING.sqlite", "PENDING", other_first=True)
    cases["SECOND_HISTORY"] = actual_readback(directory / "SECOND_HISTORY.sqlite", "GO", latest=2, other_first=True)
    cases["AMBIGUOUS_PENDING"] = actual_readback(directory / "AMBIGUOUS_PENDING.sqlite", "PENDING", ambiguous_pending=True)
    cases["UNSCORED"] = actual_readback(directory / "UNSCORED.sqlite", "UNSCORED")
    return cases


@pytest.mark.parametrize("action", ["GO", "WAIT", "RETURN", "REJECT", "PENDING", "UNKNOWN_APPROVED"])
def test_existing_durable_producers_and_masked_gets_preserve_exact_relationships(tmp_path, action):
    value = actual_readback(tmp_path / "readback.sqlite", action)
    candidate = value["network"]["candidates"][0]
    review = value["reviews"]["reviews"][0]
    assert candidate["id"] == review["candidateId"] == value["candidateId"]
    assert review["id"] == value["reviewId"]
    assert review["reportId"] == value["frozenReportId"] and review["reportVersion"] == 1
    assert review["score"] is None and review["confidence"] == 0
    assert value["readProof"]["businessReadMutations"] == 0
    assert value["readProof"]["securityAuthorizationAuditAppends"] == 3
    if action in {"GO", "WAIT", "RETURN", "REJECT"}:
        assert candidate["reviewWorkflow"]["networkAction"] == action
        assert candidate["reviewWorkflow"]["approvalId"] == value["approvalId"] != value["reviewId"]
    elif action == "PENDING":
        assert candidate["reviewId"] is None and "reviewWorkflow" not in candidate
        assert value["approvalId"] is not None
        assert value["reviews"]["approvals"][0]["approvalStatus"] == "pending"
    else:
        assert review["decision"] is None and review["status"] == "unknown"


@pytest.mark.parametrize("latest,opened", [(2, False), (3, True)])
def test_actual_own_frozen_approval_is_not_replaced_by_fresh_report_or_opened_state(tmp_path, latest, opened):
    value = actual_readback(tmp_path / "historical.sqlite", "GO", latest=latest, opened=opened)
    assert value["reviews"]["reviews"][0]["reportVersion"] == 1
    assert value["scoring"]["scorecards"][0]["reportVersion"] == latest
    candidate = value["network"]["candidates"][0]
    assert candidate["canonicalSiteStatus"] == ("opened" if opened else "approved")
    assert candidate["reviewWorkflow"]["reportVersion"] == 1
    assert candidate["reviewWorkflow"]["decisionId"] == value["reviewId"]


def test_unmeasured_confidence_stays_null_in_an_actual_available_frozen_report(tmp_path):
    value = actual_readback(tmp_path / "unmeasured.sqlite", "GO", measured=False)
    review = value["reviews"]["reviews"][0]
    assert review["confidence"] is None and review["confidenceStatus"] == "unmeasured"
    assert review["reportAvailability"] == "available" and review["score"] is None


@pytest.mark.parametrize("action,latest", [("PENDING", 1), ("GO", 2)])
def test_nonfirst_candidate_has_its_own_actual_review_and_report(tmp_path, action, latest):
    value = actual_readback(tmp_path / "multiple.sqlite", action, latest=latest, other_first=True)
    assert value["network"]["candidates"][0]["id"] != value["candidateId"]
    assert value["reviews"]["reviews"][0]["id"] != value["reviewId"]
    target = next(row for row in value["reviews"]["reviews"] if row["candidateId"] == value["candidateId"])
    assert target["id"] == value["reviewId"] and target["reportVersion"] == 1
    assert target["reportId"] == value["frozenReportId"]


def test_two_actual_pending_decisions_remain_distinct_independent_approval_records(tmp_path):
    value = actual_readback(tmp_path / "ambiguous.sqlite", "PENDING", ambiguous_pending=True)
    rows = value["reviews"]["reviews"]
    assert len(rows) == 2 and len({row["id"] for row in rows}) == 2
    assert all(row["candidateId"] == value["candidateId"] and row["reportId"] == value["frozenReportId"]
               and row["canonicalDecisionStatus"] == "PENDING_REVIEW" for row in rows)
    assert len({row["id"] for row in value["reviews"]["approvals"]}) == 2


def test_actual_data_ready_candidate_has_no_report_review_or_execution_admission(tmp_path):
    value = actual_readback(tmp_path / "unscored.sqlite", "UNSCORED")
    assert value["network"]["candidates"][0]["status"] == "new"
    candidate = value["scoring"]["candidates"][0]
    assert candidate["gate"]["passed"] is True and candidate["gate"]["okCount"] == 6
    assert candidate["scored"] is False and value["scoring"]["scorecards"] == []
    assert value["reviews"]["reviews"] == [] and value["reviews"]["source"] == "canonical"
    assert candidate["currentAdmission"]["model"]["state"] == "unverified"
    assert value["readProof"]["businessReadMutations"] == 0


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="odayplus_candidate_stage_offline_") as directory:
        print(json.dumps(generate_readbacks(Path(directory))))
