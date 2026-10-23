"""Finite native-assignment/task projection controls; not human UAT or media proof."""
from copy import deepcopy

import pytest

from modules.opsboard.application.operator_live_repository import OperatorSectionAvailability
from modules.opsboard.application.operator_tasks import (
    admit_task_center_records,
    build_task_center_records,
)
from modules.opsboard.application.shell import (
    TASK_ASSIGNMENTS,
    InMemoryShellRepository,
    ShellService,
)
from shared.auth import Principal, Scope

STAMP = "2026-10-15T08:00:00+00:00"


def _sources(kind="CANDIDATE_SITE", status="ASSIGNED"):
    parent = "candidate-a" if kind == "CANDIDATE_SITE" else "zone-a"
    return {**{key: [] for key in ("interventions", "forecast_alerts", "candidates", "sitescore_decisions",
                                  "listings", "ingestion_runs", "store_ops_issues")},
        "survey_assignments": [{"assignment_id": "asgn-a", "tenant_id": "tenant-a", "target_entity_id": parent,
            "target_entity_kind": kind, "status": status, "assigned_to": "field-a", "created_by": "creator-a",
            "created_at": STAMP, "updated_at": STAMP, "expires_at": "2026-10-17T08:00:00+00:00"}],
        "heatzones": [{"heat_zone_id": "zone-a", "storeId": "store-a"}],
        "audit_events": [{"event_type": "market_survey.assignment_created.v1", "resource": "survey_assignment/asgn-a",
            "actor": "creator-a", "metadata": {"tenant_id": "tenant-a", "target_entity_id": parent,
                                                  "actor_evidence": "authenticated_principal.v1"}}],
        "survey_section": OperatorSectionAvailability("available", "synthetic.survey_assignments", 1),
        "sections": {key: OperatorSectionAvailability("available", "synthetic." + key, 1)
            for key in ("storeOpsIssues", "interventions", "forecastAlerts", "candidates", "siteScoreDecisions",
                        "listings", "ingestionRuns")}}


def _admit(raw, *, scope=None, grant=True):
    state = {"_meta": {"tenantId": "tenant-a"}, "taskCenterRecords": build_task_center_records(raw)}
    principal = Principal("field-a", scope=scope or Scope(tenant_id="tenant-a", store_ids=frozenset({"store-a"})))
    snapshot = {"listings": [{"id": "listing-a", "tenantId": "tenant-a", "storeId": "store-a"}],
                "candidates": [{"id": "candidate-a", "listingId": "listing-a"}]}
    return admit_task_center_records(state, principal=principal, store_repository=None, brand_repository=None,
        listing_snapshot_reader=lambda: snapshot, can_read=lambda _: grant, can_approve=lambda _: False)["taskCenterRecords"]


def test_native_survey_task_keeps_real_assignment_parent_actor_and_expiry_without_claiming_sla_or_media():
    raw = _sources()
    before = deepcopy(raw)
    row = _admit(raw)["records"][0]
    assert row["taskId"] == "market-survey.assignment:asgn-a"
    assert row["sourceId"] == "asgn-a" and row["assigneeId"] == "field-a"
    assert row["createdBy"] == "creator-a" and row["completed"] is False
    assert row["expiresAt"] == "2026-10-17T08:00:00+00:00"
    assert "slaDueAt" not in row and "_surveyParent" not in row
    assert row["target"] == {"workspace": "network", "tab": "candidates", "entityId": "candidate-a", "candidateId": "candidate-a"}
    assert row["sourceDetailAvailable"] is True
    assert raw == before


@pytest.mark.parametrize("change", ["missing", "duplicate", "actor", "tenant", "parent", "marker"])
def test_creator_is_unknown_without_one_exact_authenticated_native_audit_join(change):
    raw = _sources()
    if change == "missing":
        raw["audit_events"] = []
    elif change == "duplicate":
        raw["audit_events"] *= 2
    elif change == "actor":
        raw["audit_events"][0]["actor"] = "other"
    else:
        key = {"tenant": "tenant_id", "parent": "target_entity_id", "marker": "actor_evidence"}[change]
        raw["audit_events"][0]["metadata"][key] = "not-matching"
    assert _admit(raw)["records"][0]["createdBy"] is None


@pytest.mark.parametrize("kind", ["CANDIDATE_SITE", "HEAT_ZONE"])
def test_current_parent_scope_and_resource_grant_are_required_for_each_native_task(kind):
    raw = _sources(kind)
    assert len(_admit(raw)["records"]) == 1
    assert _admit(raw, grant=False)["records"] == []
    assert _admit(raw, scope=Scope(tenant_id="tenant-a", store_ids=frozenset({"other-store"})))["records"] == []
    raw["survey_assignments"][0]["tenant_id"] = "foreign"
    assert _admit(raw)["records"] == []


def test_heat_zone_task_requires_unique_current_owner_and_exposes_the_exact_admitted_link():
    raw = _sources("HEAT_ZONE")
    before = deepcopy(raw)
    unadmitted = build_task_center_records(raw)["records"][0]
    assert unadmitted["target"] is None and unadmitted["sourceDetailAvailable"] is False
    row = _admit(raw)["records"][0]
    assert row["title"] == "熱區查核 zone-a"
    assert row["target"] == {"workspace": "network", "tab": "areas", "entityId": "zone-a", "heatZoneId": "zone-a"}
    assert row["sourceDetailAvailable"] is True
    assert raw == before
    raw["heatzones"] *= 2
    assert _admit(raw)["records"] == []
    raw["heatzones"] = []
    assert _admit(raw)["records"] == []


@pytest.mark.parametrize("status,done", [("ASSIGNED", False), ("IN_PROGRESS", False), ("EXPIRED", False),
                                        ("SUBMITTED", True), ("CANCELLED", True)])
def test_only_native_submitted_or_cancelled_assignment_states_complete_the_task(status, done):
    assert _admit(_sources(status=status))["records"][0]["completed"] is done


def test_shell_generic_assignment_cannot_replace_the_native_assignee_or_turn_task_expiry_into_sla():
    projection = _admit(_sources())
    class State:
        def resolve_role(self, **_): return {"id": "expansion-staff", "allowedWorkspaces": ["network"]}
        def get_task_center(self, **_): return {**projection, "dataOrigin": {"kind": "authoritative"}}
    repository = InMemoryShellRepository()
    repository.save_record(TASK_ASSIGNMENTS, "asgn-a", {"taskId": "asgn-a", "assigneeId": "other", "slaDueAt": STAMP})
    result = ShellService(State(), repository=repository).get_tasks(role_id="expansion-staff", subject_id="field-a", view="assigned")
    row = result["items"][0]
    assert row["assigneeId"] == "field-a" and row["assignedToMe"] is True
    assert row["slaDueAt"] is None and row["slaState"] == "none"
    assert row["expiresAt"] == "2026-10-17T08:00:00+00:00"
    assert result["meta"]["coverage"]["complete"] is False
