"""Finite synthetic owner/permission controls; not live data or human UAT."""
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from modules.opsboard.application.operator_live_repository import OperatorSectionAvailability
from modules.opsboard.application.operator_state import (
    OperatorLiveRepositoryError,
    OperatorStateService,
)
from modules.opsboard.application.operator_tasks import (
    admit_task_center_records,
    build_task_center_records,
)
from modules.opsboard.application.shell import (
    TASK_ASSIGNMENTS,
    InMemoryShellRepository,
    ShellService,
)
from shared.auth import Principal, Role, Scope

STAMP = datetime(2026, 10, 15, 8, tzinfo=UTC)


def sources():
    return {
        "interventions": [
            {"intervention_id": "retained-closed", "store_id": "store-a", "kind": "PRICING", "status": "CLOSED",
             "created_by": "subject-a", "created_at": STAMP, "history": [{"at": STAMP}], "assigned_to": "subject-a"},
            {"intervention_id": "still-observing", "store_id": "store-a", "kind": "PRICING", "status": "COMPLETED",
             "created_by": "subject-a", "created_at": STAMP},
            {"intervention_id": "foreign", "store_id": "store-b", "kind": "PRICING", "status": "CLOSED",
             "created_by": "subject-a", "created_at": STAMP},
        ],
        "forecast_alerts": [{"alert_id": "alert-closed", "status": "closed", "store_id": "store-a",
                             "opened_at": STAMP, "closed_at": STAMP, "alert_reason_code": "retained actual alert"}],
        "candidates": [{"candidate_site": {"candidate_site_id": "candidate-a", "site_status": "scored", "created_by": "subject-a", "created_at": STAMP}},
                       {"candidate_site": {"candidate_site_id": "candidate-b", "site_status": "scored", "created_by": "subject-a", "created_at": STAMP}}],
        "sitescore_decisions": [],
        "listings": [{"listing_id": "listing-a", "listing_status": "manual_review", "owner": "subject-a"}],
        "ingestion_runs": [{"run_id": "run-a", "status": "failed", "started_at": STAMP, "completed_at": STAMP}],
        "store_ops_issues": [{"id": "actual-derived-issue", "storeId": "store-a", "status": "closed", "title": "Retained issue",
                              "sourceRef": {"kind": "forecastops.alert", "id": "alert-closed"}}],
        "sections": {name: OperatorSectionAvailability("available", f"actual.{name}", 1)
                     for name in ("interventions", "forecastAlerts", "candidates", "listings", "ingestionRuns", "storeOpsIssues", "siteScoreDecisions")},
    }


def scoped(records, *, grants=True, approvals=True, tenant="tenant-a", decisions=None):
    state = {"_meta": {"tenantId": "tenant-a"}, "taskCenterRecords": records, "decisions": decisions or []}
    principal = Principal("subject-a", frozenset({Role.OPERATIONS_MANAGER}), Scope(tenant_id=tenant, store_ids=frozenset({"store-a"})))
    stores = SimpleNamespace(get_store=lambda identity: SimpleNamespace(store_id=identity, is_current=True,
        tenant_id="tenant-a", brand_id="brand-a", region_code="north"))
    brands = SimpleNamespace(get_brand=lambda _: SimpleNamespace(tenant_id="tenant-a"))
    snapshot = {"listings": [{"id": "listing-a", "tenantId": "tenant-a", "storeId": "store-a"},
                             {"id": "listing-b", "tenantId": "tenant-a", "storeId": "store-b"}],
                "candidates": [{"id": "candidate-a", "listingId": "listing-a"}, {"id": "candidate-b", "listingId": "listing-b"}]}
    return admit_task_center_records(state, principal=principal, store_repository=stores, brand_repository=brands,
        listing_snapshot_reader=lambda: snapshot, can_read=lambda _: grants, can_approve=lambda _: approvals)


def test_retained_terminal_states_and_actual_creators_are_independent_of_active_queue():
    raw = sources()
    before = deepcopy(raw)
    projection = build_task_center_records(raw)
    by_id = {row["taskId"]: row for row in projection["records"]}
    assert by_id["intervention:retained-closed"]["completed"] is True
    # COMPLETED is deliberately not terminal: canonical close-out is still due.
    assert by_id["intervention:still-observing"]["completed"] is False
    assert by_id["intervention:retained-closed"]["createdBy"] == "subject-a"
    assert by_id["candidate:candidate-a"]["createdBy"] == "subject-a"
    assert by_id["candidate:candidate-a"]["updatedAt"] is None
    assert by_id["listing:listing-a"]["createdBy"] is None
    assert by_id["forecastops.alert:alert-closed"]["createdBy"] is None
    assert by_id["forecastops.alert:alert-closed"]["target"]["entityId"] == "actual-derived-issue"
    assert projection["coverage"]["complete"] is False
    assert projection["coverage"]["retentionComplete"] is False
    assert raw == before


def test_missing_forecast_issue_does_not_substitute_an_unrelated_task_target():
    raw = sources()
    raw["store_ops_issues"] = []
    row = next(item for item in build_task_center_records(raw)["records"] if item["sourceKind"] == "forecastops.alert")
    assert row["target"] is None
    assert row["sourceDetailAvailable"] is False


def test_actual_store_candidate_parent_and_resource_grants_are_all_required():
    projection = build_task_center_records(sources())
    before = deepcopy(projection)
    rows = scoped(projection)["taskCenterRecords"]["records"]
    identities = {row["taskId"] for row in rows}
    assert "intervention:retained-closed" in identities
    assert "candidate:candidate-a" in identities
    assert "intervention:foreign" not in identities
    assert "candidate:candidate-b" not in identities
    assert "ingestion:run-a" not in identities  # no canonical store relationship
    assert scoped(projection, grants=False)["taskCenterRecords"]["records"] == []
    with pytest.raises(OperatorLiveRepositoryError, match="verified task tenant"):
        scoped(projection, tenant="foreign-tenant")
    assert projection == before


def test_approval_tab_uses_actual_approve_grant_and_never_a_requestor_label():
    projection = build_task_center_records(sources())
    state = {"_meta": {"tenantId": "tenant-a"}, "taskCenterRecords": projection,
             "decisions": [{"id": "canonical-request", "title": "Actual pending request", "status": "pending", "requestor": "subject-a", "meta": "AVM",
                            "target": {"workspace": "govern", "tab": "approvals", "entityId": "canonical-request"}}]}
    principal = Principal("subject-a", scope=Scope(tenant_id="tenant-a"))
    args = dict(principal=principal, store_repository=None, brand_repository=None,
                listing_snapshot_reader=lambda: {}, can_read=lambda kind: kind != "operator_console")
    # Only permit generic pending after clearing native store-bound rows, so the
    # control isolates APPROVE without inventing any canonical store metadata.
    state["taskCenterRecords"] = {**projection, "records": []}
    args["can_read"] = lambda _: True
    denied = admit_task_center_records(state, **args, can_approve=lambda _: False)
    row = denied["taskCenterRecords"]["records"][0]
    assert row["requiresApproval"] is False
    assert row["createdBy"] is None
    allowed = admit_task_center_records(state, **args, can_approve=lambda _: True)
    assert allowed["taskCenterRecords"]["records"][0]["requiresApproval"] is True
    assert state["taskCenterRecords"]["records"] == []


def test_live_service_requires_request_admission_not_just_a_tenant_string():
    class Owner:
        data_origin = {"kind": "authoritative", "sourceId": "unit-retained-owners"}
        def load_state(self, **scope):
            return {"_meta": {"tenantId": scope["tenant_id"], "dataMode": "live"},
                    "taskCenterRecords": build_task_center_records(sources())}
    service = OperatorStateService(live_repository=Owner())
    with pytest.raises(OperatorLiveRepositoryError, match="request-scoped task admission"):
        service.get_task_center(role_id="ops-lead", tenant_id="tenant-a")
    with pytest.raises(OperatorLiveRepositoryError, match="not confirmed"):
        service.get_task_center(role_id="ops-lead", tenant_id="tenant-a", read_projection=lambda state: state)


def test_view_filters_use_subject_identity_and_keep_retained_history_and_missing_selection():
    projection = scoped(build_task_center_records(sources()))["taskCenterRecords"]
    class State:
        def resolve_role(self, **_):
            return {"id": "ops-lead", "label": "Operations", "allowedWorkspaces": ["today", "store", "network", "govern"]}
        def get_task_center(self, **_):
            return {**projection, "dataOrigin": {"kind": "authoritative"}}
    repo = InMemoryShellRepository()
    repo.save_record(TASK_ASSIGNMENTS, "still-observing", {"taskId": "still-observing", "assigneeId": "subject-a"})
    service = ShellService(State(), repository=repo)
    current = dict(role_id="ops-lead", subject_id="subject-a")
    done = service.get_tasks(**current, view="completed")
    assert "intervention:retained-closed" in {row["taskId"] for row in done["items"]}
    assert "intervention:still-observing" not in {row["taskId"] for row in done["items"]}
    initiated = service.get_tasks(**current, view="initiated")
    assert {row["taskId"] for row in initiated["items"]} == {"intervention:still-observing", "candidate:candidate-a"}
    assert [row["taskId"] for row in service.get_tasks(**current, view="assigned")["items"]] == ["intervention:still-observing"]
    assert service.get_tasks(role_id="ops-lead", subject_id="someone-else", view="initiated")["items"] == []
    missing = service.get_tasks(**current, view="assigned", task_id="denied-or-missing")
    assert missing["selected"] is None and missing["selectionUnavailable"] is True
    assert missing["meta"]["coverage"]["complete"] is False
    assert projection["records"][0]["createdBy"] == "subject-a"


def test_newer_report_round_does_not_flatten_prior_approved_opened_decision_or_infer_legacy_creator():
    from modules.sitescore.domain.scoring import SiteScoreRecommendation
    from shared.workflow.sitescore import DecisionStatus, SiteScoreDecision

    raw = sources()
    raw["candidates"][0]["candidate_site"]["site_status"] = "opened"
    first = SiteScoreDecision(
        decision_id="old-round", candidate_site_id="candidate-a", report_id="report-1", report_version=1,
        recommendation=SiteScoreRecommendation.GO, status=DecisionStatus.APPROVED,
        policy_version="policy-owner", model_version="model-owner", created_by="legacy-unverified",
        created_at=STAMP, tenant_id="tenant-a",
    )
    second = SiteScoreDecision(
        decision_id="new-round", candidate_site_id="candidate-a", report_id="report-2", report_version=2,
        recommendation=SiteScoreRecommendation.GO, status=DecisionStatus.PENDING_REVIEW,
        policy_version="policy-owner", model_version="model-owner", created_by="subject-a", created_by_verified=True,
        created_at=STAMP, tenant_id="tenant-a", previous_decision_id="old-round",
    )
    raw["sitescore_decisions"] = [first, second]
    projection = build_task_center_records(raw)
    state = scoped(projection, decisions=[{"id": "actual-new-signature", "title": "Current new report review", "status": "pending",
        "target": {"workspace": "network", "tab": "review", "entityId": "candidate-a", "candidateId": "candidate-a", "reviewId": "new-round"}}])
    rows = {row["taskId"]: row for row in state["taskCenterRecords"]["records"]}
    assert rows["sitescore.decision:old-round"]["completed"] is True
    assert rows["sitescore.decision:old-round"]["createdBy"] is None
    assert rows["sitescore.decision:new-round"]["completed"] is False
    assert rows["sitescore.decision:new-round"]["createdBy"] == "subject-a"
    assert rows["sitescore.decision:new-round"]["reportId"] == "report-2"
    assert rows["sitescore.decision:new-round"]["requiresApproval"] is True
    assert "approval:actual-new-signature" not in rows
    assert rows["sitescore.decision:old-round"]["requiresApproval"] is False
    assert rows["sitescore.decision:new-round"]["previousDecisionId"] == "old-round"
    assert "candidate:candidate-a" not in rows  # opened candidate status is not a review round
    assert first.status == DecisionStatus.APPROVED and second.status == DecisionStatus.PENDING_REVIEW
    assert raw["candidates"][0]["candidate_site"]["site_status"] == "opened"


@pytest.mark.parametrize(("role", "module", "expected"), [
    (Role.EXECUTIVE, "NetPlan", True),
    (Role.EXECUTIVE, "AVM", False),
    (Role.EXECUTIVE, "PriceOps", False),
    (Role.OPERATIONS_MANAGER, "NetPlan", False),
    (Role.OPERATIONS_MANAGER, "AVM", False),
    (Role.OPERATIONS_MANAGER, "PriceOps", False),
    (Role.OPERATIONS_MANAGER, "Growth", True),
    (Role.MARKETING_MANAGER, "Growth", False),
    (Role.FINANCE_LEGAL, "AVM", True),
    (Role.PRICING_MANAGER, "PriceOps", True),
    (Role.SITE_REVIEWER, "SiteScore", True),
])
def test_pending_approval_uses_canonical_module_and_existing_decision_resource(role, module, expected):
    from modules.opsboard.application.operator_pending_projection import project_pending_state
    from shared.auth import Action, rbac_allows

    principal = Principal("subject-a", frozenset({role}), Scope(tenant_id="tenant-a"))
    empty = {"records": [], "coverage": {"complete": False, "retentionComplete": False, "domains": []}}
    state = {"_meta": {"tenantId": "tenant-a"}, "taskCenterRecords": empty}
    state = project_pending_state(state, {"approvals": [
        {"id": "actual-pending", "title": "Actual canonical pending", "module": module, "status": "pending"},
    ]})
    before = deepcopy(state)
    admitted = admit_task_center_records(state, principal=principal,
        store_repository=None, brand_repository=None, listing_snapshot_reader=lambda: {},
        can_read=lambda _: True,
        can_approve=lambda resource: rbac_allows(principal, resource, Action.APPROVE))
    row = admitted["taskCenterRecords"]["records"][0]
    assert state["decisions"][0]["meta"] == module
    assert row["requiresApproval"] is expected
    assert row["status"] == "pending"
    assert row["target"] == {"workspace": "govern", "tab": "approvals", "entityId": "actual-pending"}
    assert row["createdBy"] is None
    assert state == before


@pytest.mark.parametrize("module", [None, "unregistered-owner", "netplan", "", {"module": "NetPlan"}])
def test_unproved_pending_module_is_read_only_even_with_an_unrelated_approve_grant(module):
    state = {"_meta": {"tenantId": "tenant-a"},
        "taskCenterRecords": {"records": [], "coverage": {"complete": False}},
        "decisions": [{"id": "unknown-request", "title": "Unproved owner", "status": "pending", "meta": module,
            "target": {"workspace": "network", "tab": "review", "entityId": "unrelated"}}]}
    requested_resources = []
    def can_approve(resource):
        requested_resources.append(resource)
        return True
    result = admit_task_center_records(state, principal=Principal("subject-a", scope=Scope(tenant_id="tenant-a")),
        store_repository=None, brand_repository=None, listing_snapshot_reader=lambda: {},
        can_read=lambda _: True, can_approve=can_approve)
    assert result["taskCenterRecords"]["records"][0]["requiresApproval"] is False
    assert result["taskCenterRecords"]["records"][0]["status"] == "pending"
    assert requested_resources == []
