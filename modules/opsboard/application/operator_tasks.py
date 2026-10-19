"""Read-only Task Center facts from the same authorized retained domain owners.

No task completion, initiator, approval authority or retention coverage is inferred
from a role label, a missing active queue entry, or the time this projection ran.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Any

from modules.intervention.domain.lifecycle import TERMINAL_STATUSES
from modules.opsboard.application.network_read_scope import (
    candidate_scope_evidence,
    record_in_scope,
)
from modules.opsboard.application.operator_live_repository import OperatorLiveRepositoryError
from modules.opsboard.application.operator_pending_projection import _restricted, _store_in_scope
from shared.auth import Principal

# Canonical pending module names survive project_pending_state as ``meta``.
# Use each existing decision resource, never its display workspace. Growth's
# Operator decision owner is GovernanceService._decide_growth, guarded by the
# existing intervention:APPROVE route; it is not the AdLift budget writer.
_PENDING_APPROVAL_RESOURCES = {
    "SiteScore": "sitescore",
    "AVM": "avm",
    "NetPlan": "netplan",
    "PriceOps": "priceops",
    "Growth": "intervention",
}


def _value(row: Any, name: str, default: Any = None) -> Any:
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def _text(value: Any) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    return value.strip() or None if isinstance(value, str) else None


def _time(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    text = _text(value)
    if text is not None:
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return text


def _intervention_notification_request(row: Any) -> dict[str, Any] | None:
    history = _value(row, "history", ())
    identity = _text(_value(row, "intervention_id"))
    version = _value(row, "version")
    if (_text(_value(row, "status")) != "PENDING_APPROVAL" or not identity
        or not isinstance(history, (list, tuple)) or not history
        or type(version) is not int or version < 1):
        return None
    # Assignment/unassignment append a same-status transition and increment the
    # owner version. They do not submit a new approval request. Stay within the
    # current contiguous pending segment and retain its actual submit transition.
    for index in range(len(history) - 1, -1, -1):
        event = history[index]
        if _text(_value(event, "to_status")) != "PENDING_APPROVAL":
            return None
        if _text(_value(event, "from_status")) == "PENDING_APPROVAL":
            if _text(_value(event, "action")) not in {"assign", "unassign"}:
                return None
            continue
        if _text(_value(event, "action")) != "submit_for_approval":
            return None
        actor = _text(_value(event, "actor"))
        at = _time(_value(event, "at"))
        if not actor or not at or datetime.fromisoformat(at.replace("Z", "+00:00")).tzinfo is None:
            return None
        # History position is the retained transition's identity within this
        # aggregate, including distinct same-clock resubmissions. No read time,
        # assignee, or later owner version can replace the original event.
        key = sha256(json.dumps(["intervention", identity, index, at, actor, "submit_for_approval"], separators=(",", ":")).encode()).hexdigest()
        return {"key": key, "requestId": identity, "ownerVersion": version,
                "occurredAt": at, "actorSubjectId": actor}
    return None


def _notification_request(row: Any, kind: str) -> dict[str, Any] | None:
    """An actual current submission transition, never an active-queue surrogate."""
    if kind == "intervention":
        return _intervention_notification_request(row)
    history = _value(row, "history", ())
    if not isinstance(history, (list, tuple)) or not history:
        return None
    tail = history[-1]
    status = _text(_value(row, "status"))
    expected = "PENDING_REVIEW" if kind == "sitescore.decision" else "PENDING_APPROVAL"
    action = "submit" if kind == "sitescore.decision" else "submit_for_approval"
    version = _value(row, "version")
    at = _time(_value(tail, "at"))
    actor = _text(_value(tail, "actor"))
    if (status != expected or _text(_value(tail, "to_status")) != expected
        or _text(_value(tail, "action")) != action or not actor or not at
        or isinstance(version, bool) or not isinstance(version, int) or version < 1):
        return None
    if datetime.fromisoformat(at.replace("Z", "+00:00")).tzinfo is None:
        return None
    identity = _text(_value(row, "decision_id" if kind == "sitescore.decision" else "intervention_id"))
    request_id = identity
    if kind == "sitescore.decision":
        request_id = _text(_value(row, "approval_id"))
        if (not request_id or _text(_value(tail, "approval_id")) != request_id
            or _value(tail, "version") != version
            or _value(row, "submission_actor_verified") is not True
            or _value(tail, "submission_actor_verified") is not True
            or _text(_value(row, "submitted_by")) != actor
            or _text(_value(tail, "submitted_by")) != actor
            or _value(tail, "report_id") != _value(row, "report_id")
            or _value(tail, "report_version") != _value(row, "report_version")
            or not _text(_value(row, "tenant_id"))
            or _value(tail, "tenant_id") != _value(row, "tenant_id")
            or not _text(_value(row, "report_id"))
            or type(_value(row, "report_version")) is not int or _value(row, "report_version") < 1
            or _value(tail, "previous_decision_id") != _value(row, "previous_decision_id")):
            return None
    if not identity or not request_id:
        return None
    # A resubmission gets a fresh approval ID/version/time. A new report decision
    # keeps its own source ID, even when the candidate is already approved/opened.
    key = sha256(json.dumps([kind, identity, request_id, version, at], separators=(",", ":")).encode()).hexdigest()
    return {"key": key, "requestId": request_id, "ownerVersion": version,
            "occurredAt": at, "actorSubjectId": actor}


def _base(kind: str, identity: str, *, title: str, status: str, workspace: str,
          roles: list[str], completed: bool, creator: Any = None, created: Any = None,
          updated: Any = None, store_id: Any = None) -> dict[str, Any]:
    return {
        "taskId": f"{kind}:{identity}", "sourceId": identity, "sourceKind": kind,
        "title": title, "taskType": kind, "status": status,
        "workspace": workspace, "roles": roles, "priority": None,
        "owner": None, "createdBy": _text(creator), "createdAt": _time(created),
        "updatedAt": _time(updated), "storeId": _text(store_id),
        "completed": completed, "requiresApproval": False,
        "target": None, "sourceDetailAvailable": False,
    }


def build_task_center_records(sources: dict[str, Any]) -> dict[str, Any]:
    """Only owner-retained records, including states omitted by the active queue."""
    records: list[dict[str, Any]] = []
    for row in sources["interventions"]:
        identity = _text(_value(row, "intervention_id"))
        status = _text(_value(row, "status"))
        if identity is None or status is None:
            continue
        history = _value(row, "history", ())
        updated = _value(history[-1], "at") if isinstance(history, (list, tuple)) and history else None
        task = _base("intervention", identity, title=f"{_text(_value(row, 'kind')) or 'Intervention'} intervention",
                     status=status, workspace="today", roles=["ops-lead"],
                     completed=status in {item.value for item in TERMINAL_STATUSES},
                     creator=_value(row, "created_by"), created=_value(row, "created_at"),
                     updated=updated, store_id=_value(row, "store_id"))
        task.update(target={"workspace": "today", "tab": "interventions", "entityId": identity},
                    sourceDetailAvailable=True, assigneeId=_text(_value(row, "assigned_to")),
                    notificationRequest=_notification_request(row, "intervention"))
        records.append(task)
    for row in sources["forecast_alerts"]:
        identity = _text(_value(row, "alert_id"))
        status = _text(_value(row, "status"))
        if identity is None or status is None:
            continue
        task = _base("forecastops.alert", identity, title=_text(_value(row, "alert_reason_code")) or "Forecast alert",
                     status=status, workspace="store", roles=["ops-lead", "field-lead", "pm-audit"],
                     completed=status.lower() == "closed", created=_value(row, "opened_at"),
                     updated=_value(row, "closed_at") or _value(row, "disposition_set_at") or _value(row, "acknowledged_at"),
                     store_id=_value(row, "store_id"))
        matches = [issue for issue in sources["store_ops_issues"]
                   if isinstance(issue, dict) and _text(issue.get("id"))
                   and issue.get("storeId") == task["storeId"]
                   and isinstance(issue.get("sourceRef"), dict)
                   and issue["sourceRef"].get("kind") == "forecastops.alert"
                   and issue["sourceRef"].get("id") == identity]
        if len(matches) == 1:
            task.update(target={"workspace": "store", "tab": "forecastOps", "entityId": matches[0]["id"]},
                        sourceDetailAvailable=True)
        records.append(task)
    for row in sources["candidates"]:
        candidate = _value(row, "candidate_site")
        identity = _text(_value(candidate, "candidate_site_id"))
        status = _text(_value(candidate, "site_status"))
        if identity is None or status not in {"new", "screened", "scored", "visited"}:
            continue
        # Candidate intake is not a completed review round. Each actual decision
        # below retains its own ID/report/creator/terminal state independently.
        # These are the canonical CandidateSite review states. A created_at is
        # not a completion/update time, and a listing's department is not an actor.
        task = _base("candidate", identity, title="Candidate site review", status=status,
                     workspace="network", roles=["ops-lead", "expansion-manager", "expansion-staff", "pm-audit"],
                     completed=False,
                     creator=_value(candidate, "created_by"), created=_value(candidate, "created_at"))
        task.update(target={"workspace": "network", "tab": "candidates", "entityId": identity, "candidateId": identity},
                    sourceDetailAvailable=True)
        records.append(task)
    for decision in sources["sitescore_decisions"]:
        identity = _text(_value(decision, "decision_id"))
        candidate_id = _text(_value(decision, "candidate_site_id"))
        status = _text(_value(decision, "status")) or _text(_value(decision, "decision_status"))
        if not identity or not candidate_id or not status:
            continue
        history = _value(decision, "history", ())
        updated = _value(history[-1], "at") if isinstance(history, (list, tuple)) and history else None
        creator = (_value(decision, "submitted_by") if _value(decision, "submission_actor_verified") is True
                   else _value(decision, "created_by") if _value(decision, "created_by_verified") is True else None)
        task = _base("sitescore.decision", identity, title=f"SiteScore review {identity}", status=status,
                     workspace="network", roles=["ops-lead", "expansion-manager", "expansion-staff", "pm-audit"],
                     completed=status in {"APPROVED", "REJECTED"}, creator=creator,
                     created=_value(decision, "created_at"), updated=updated)
        task.update(candidateId=candidate_id, reportId=_text(_value(decision, "report_id")),
                    reportVersion=_value(decision, "report_version"), previousDecisionId=_text(_value(decision, "previous_decision_id")),
                    target={"workspace": "network", "tab": "review", "entityId": candidate_id,
                            "candidateId": candidate_id, "reviewId": identity}, sourceDetailAvailable=True,
                    notificationRequest=_notification_request(decision, "sitescore.decision"))
        records.append(task)
    for row in sources["listings"]:
        identity = _text(_value(row, "listing_id"))
        status = _text(_value(row, "listing_status"))
        if identity and status in {"manual_review", "stale"}:
            records.append(_base("listing", identity, title="Listing requires review", status=status,
                                 workspace="network", roles=["ops-lead", "expansion-manager", "expansion-staff", "pm-audit"],
                                 completed=False))
    for row in sources["ingestion_runs"]:
        identity = _text(_value(row, "run_id"))
        status = _text(_value(row, "status"))
        if identity and status in {"failed", "partial", "quarantined", "degraded"}:
            records.append(_base("ingestion", identity, title="External ingestion requires review", status=status,
                                 workspace="govern", roles=["ops-lead", "pm-audit"], completed=False,
                                 created=_value(row, "started_at"), updated=_value(row, "completed_at")))
    for issue in sources["store_ops_issues"]:
        identity = _text(issue.get("id"))
        status = _text(issue.get("status"))
        if not identity or not status:
            continue
        task = _base("storeops.issue", identity, title=_text(issue.get("title")) or "Store Ops issue",
                     status=status, workspace="store", roles=["ops-lead", "cs-lead", "field-lead"],
                     completed=status == "closed", store_id=issue.get("storeId"))
        task.update(target={"workspace": "store", "tab": "overview", "entityId": identity},
                    sourceDetailAvailable=True, owner=_text(issue.get("ownerName")),
                    slaDueAt=_time(issue.get("slaDueAt")))
        records.append(task)
    for row in sources.get("survey_assignments", []):
        identity = _text(_value(row, "assignment_id"))
        tenant = _text(_value(row, "tenant_id"))
        parent = _text(_value(row, "target_entity_id"))
        parent_kind = _text(_value(row, "target_entity_kind"))
        status = _text(_value(row, "status"))
        if not identity or not tenant or not parent or parent_kind not in {"CANDIDATE_SITE", "HEAT_ZONE"} or not status:
            continue
        # Legacy created_by strings alone are not authenticated actor evidence.
        # The new canonical command retains this exact native creation event.
        creators = [event for event in sources.get("audit_events", [])
                    if _value(event, "event_type") == "market_survey.assignment_created.v1"
                    and _value(event, "resource") == f"survey_assignment/{identity}"
                    and _value(event, "actor") == _value(row, "created_by")
                    and isinstance(_value(event, "metadata"), dict)
                    and _value(event, "metadata").get("tenant_id") == tenant
                    and _value(event, "metadata").get("target_entity_id") == parent
                    and _value(event, "metadata").get("actor_evidence") == "authenticated_principal.v1"]
        task = _base("market-survey.assignment", identity,
                     title=f"{'候選點現勘' if parent_kind == 'CANDIDATE_SITE' else '熱區查核'} {parent}",
                     status=status, workspace="network",
                     roles=["ops-lead", "expansion-manager", "expansion-staff", "pm-audit"],
                     completed=status in {"SUBMITTED", "CANCELLED"},
                     creator=_value(row, "created_by") if len(creators) == 1 else None,
                     created=_value(row, "created_at"), updated=_value(row, "updated_at"))
        task.update(assigneeId=_text(_value(row, "assigned_to")), expiresAt=_time(_value(row, "expires_at")),
                    _surveyParent={"tenantId": tenant, "kind": parent_kind, "id": parent})
        if parent_kind == "CANDIDATE_SITE":
            task.update(target={"workspace": "network", "tab": "candidates", "entityId": parent, "candidateId": parent},
                        sourceDetailAvailable=True)
        else:
            matches = [item for item in sources.get("heatzones", []) if _value(item, "heat_zone_id") == parent]
            # Retain the unique owner's exact identity here. A source target is
            # exposed only after the request's current scope/resource admission.
            if len(matches) == 1 and isinstance(matches[0], dict):
                task["_surveyParent"]["scope"] = {"tenantId": tenant, **deepcopy(matches[0]), "heatZoneId": parent}
        records.append(task)
    domains = []
    for key, kind, creator, history in (
        ("storeOpsIssues", "storeops.issue", False, False),
        ("interventions", "intervention", True, True), ("forecastAlerts", "forecastops.alert", False, True),
        ("candidates", "candidate", True, False),
        ("siteScoreDecisions", "sitescore.decision", False, True), ("listings", "listing", False, False),
        ("ingestionRuns", "ingestion", False, False),
    ):
        section = sources["sections"][key]
        domains.append({"domain": kind, "state": section.state, "source": section.source,
                        "creatorAvailable": creator, "retainedCompletedStatesAvailable": history})
    if "survey_section" in sources:
        # Optional Survey coverage does not change unrelated workspace readiness.
        section = sources["survey_section"]
        domains.append({"domain": "market-survey.assignment", "state": section.state, "source": section.source,
                        "creatorAvailable": False, "retainedCompletedStatesAvailable": True})
    identities = [row["taskId"] for row in records]
    if len(identities) != len(set(identities)):
        raise OperatorLiveRepositoryError("canonical task identities are ambiguous")
    return {"records": records, "coverage": {"complete": False, "retentionComplete": False,
            "domains": domains, "reasonCode": "TASK_RETAINED_OWNER_COVERAGE_PARTIAL"}}


def admit_task_center_records(
    state: dict[str, Any], *, principal: Principal,
    store_repository: Any, brand_repository: Any,
    listing_snapshot_reader: Callable[[], dict[str, Any]], can_read: Callable[[str], bool],
    can_approve: Callable[[str], bool],
) -> dict[str, Any]:
    """Request-local admission, using existing canonical object scope evidence.

    The caller has already composed the canonical pending snapshot. The callback
    uses the existing resource VIEW decisions, never a display persona grant.
    """
    if not principal.authenticated or not principal.tenant_id or state.get("_meta", {}).get("tenantId") != principal.tenant_id:
        raise OperatorLiveRepositoryError("verified task tenant owner is required")
    source = state.get("taskCenterRecords")
    if not isinstance(source, dict):
        raise OperatorLiveRepositoryError("canonical task owner projection is unavailable")
    listing_snapshot: dict[str, Any] | None = None

    def network_snapshot() -> dict[str, Any]:
        nonlocal listing_snapshot
        if listing_snapshot is None:
            listing_snapshot = listing_snapshot_reader()
        return listing_snapshot

    visible: list[dict[str, Any]] = []
    for row in source["records"]:
        kind = row["sourceKind"]
        if kind in {"intervention", "forecastops.alert", "storeops.issue"}:
            allowed = can_read("operator_console") and _store_in_scope(
                principal, row["storeId"] or "", store_repository, brand_repository)
        elif kind in {"candidate", "sitescore.decision"}:
            candidate_id = row["sourceId"] if kind == "candidate" else row["candidateId"]
            allowed = can_read("sitescore") and candidate_id in candidate_scope_evidence(principal, network_snapshot())
        elif kind == "market-survey.assignment":
            parent = row.get("_surveyParent")
            allowed = isinstance(parent, dict) and parent.get("tenantId") == principal.tenant_id
            if allowed and parent.get("kind") == "CANDIDATE_SITE":
                allowed = can_read("listing") and parent.get("id") in candidate_scope_evidence(principal, network_snapshot())
            elif allowed and parent.get("kind") == "HEAT_ZONE":
                evidence = parent.get("scope")
                allowed = (can_read("heatzone") and isinstance(evidence, dict)
                           and record_in_scope(principal, evidence))
            else:
                allowed = False
        elif kind == "listing":
            allowed = can_read("listing") and any(
                item["id"] == row["sourceId"] and record_in_scope(principal, item)
                for item in network_snapshot().get("listings", []))
        else:
            allowed = can_read("operator_console") and not _restricted(principal)
        if allowed:
            admitted = deepcopy(row)
            parent = row.get("_surveyParent")
            if kind == "market-survey.assignment" and isinstance(parent, dict) and parent.get("kind") == "HEAT_ZONE":
                zone_id, evidence = parent.get("id"), parent.get("scope")
                if (isinstance(zone_id, str) and zone_id.strip() and isinstance(evidence, dict)
                    and evidence.get("heat_zone_id") == zone_id and evidence.get("heatZoneId") == zone_id):
                    admitted.update(target={"workspace": "network", "tab": "areas", "entityId": zone_id,
                                            "heatZoneId": zone_id}, sourceDetailAvailable=True)
            admitted.pop("_surveyParent", None)
            visible.append(admitted)
    # Canonical pending owners were scoped by project_pending_snapshot already;
    # only these actual submitted requests populate the pending-approval view.
    if can_read("operator_console"):
        for approval in state.get("decisions", []):
            target = approval.get("target")
            if approval.get("status") != "pending" or not isinstance(target, dict):
                continue
            identity = _text(approval.get("id"))
            if identity is None:
                continue
            native = next((item for item in visible if item["sourceKind"] == "intervention"
                           and item["sourceId"] == target.get("entityId")
                           and target.get("workspace") == "today" and target.get("tab") == "interventions"), None)
            if native is not None:
                native["requiresApproval"] = can_approve("intervention")
                continue
            review = next((item for item in visible if item["sourceKind"] == "sitescore.decision"
                           and item["sourceId"] == target.get("reviewId")
                           and item["candidateId"] == target.get("candidateId")
                           and target.get("workspace") == "network" and target.get("tab") == "review"), None)
            if review is not None:
                review["requiresApproval"] = can_approve("sitescore")
                request = review.get("notificationRequest")
                if isinstance(request, dict) and request.get("requestId") != identity:
                    review["notificationRequest"] = None  # not this canonical signature
                continue
            task = _base("approval", identity, title=approval["title"], status="pending",
                         workspace=target.get("workspace", "govern"), roles=[], completed=False)
            # A requestor display string is not a verified initiating subject.
            resource = _PENDING_APPROVAL_RESOURCES.get(_text(approval.get("meta")))
            task.update(requiresApproval=resource is not None and can_approve(resource),
                        target=deepcopy(target), sourceDetailAvailable=True)
            visible.append(task)
    result = deepcopy(state)
    result["taskCenterRecords"] = {"records": visible, "coverage": deepcopy(source["coverage"])}
    result["_taskCenterRequestAdmitted"] = True
    return result
