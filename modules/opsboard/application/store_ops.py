"""Store Ops issue lifecycle service for the Operator Console.

The service keeps the frontend-facing Store Ops contract intentionally close to
the TypeScript view model: issues, evidence, stores, and audit rows are plain
serializable dictionaries. Repositories can be in-memory or backed by the shared
SQLite document store, so the same lifecycle writes survive a product-E2E
restart when durable persistence is enabled.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Any, Protocol

from shared.audit.events import AuditEvent, InMemoryAuditLog

IssueStatus = str
LightDimension = str
LightStatus = str

_STORE_OPS_STATE_DOC_ID = "default"
_STORE_OPS_STATE_COLLECTION = "opsboard.store_ops.state"
_STORE_OPS_IDEMPOTENCY_COLLECTION = "opsboard.store_ops.idempotency"

_LIGHT_DIMENSIONS = ("demand", "operations", "staffing", "margin")
_LIGHT_STATUSES = ("green", "yellow", "red")
_ISSUE_STATUSES = {
    "new",
    "triaged",
    "assigned",
    "inprogress",
    "executed",
    "observing",
    "outcomeready",
    "closed",
    "waitingevidence",
    "waitingapproval",
    "escalated",
}
_ISSUE_SEVERITIES = {"low", "medium", "high", "critical"}
_ISSUE_SOURCES = {
    "googleReview",
    "csCase",
    "camera",
    "iot",
    "payment",
    "forecastOps",
    "cleaning",
    "multiSignal",
}
_PERMITTED_CAMERA_PURPOSE_KEYWORDS = (
    "incident",
    "service",
    "safety",
    "clean",
    "cleanliness",
    "payment",
    "refund",
    "device",
    "customer",
    "quality",
    "audit",
)

_SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


class StoreOpsError(Exception):
    """Base Store Ops service error."""


class StoreOpsNotFound(StoreOpsError):
    """Requested Store Ops resource does not exist."""


class StoreOpsConflict(StoreOpsError):
    """Lifecycle transition is invalid for the current issue state."""


class StoreOpsPolicyError(StoreOpsError):
    """Policy-controlled Store Ops action was rejected."""


class StoreOpsRepository(Protocol):
    def get_state(self) -> dict[str, Any]:
        ...

    def save_state(self, state: Mapping[str, Any]) -> None:
        ...

    def get_idempotency_result(self, key: str) -> dict[str, Any] | None:
        ...

    def save_idempotency_result(self, key: str, result: Mapping[str, Any]) -> None:
        ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _seed_state() -> dict[str, Any]:
    """Return the canonical Store Ops demo state used by API and frontend."""
    return {
        "stores": [
            {
                "id": "ST-008",
                "name": "台北信義 A11",
                "district": "Xinyi",
                "city": "Taipei",
                "manager": "Mina Chen",
                "lights": {
                    "demand": "green",
                    "operations": "red",
                    "staffing": "yellow",
                    "margin": "yellow",
                },
                "riskScore": 88,
            },
            {
                "id": "ST-014",
                "name": "台北大安復興",
                "district": "Da-an",
                "city": "Taipei",
                "manager": "Leo Huang",
                "lights": {
                    "demand": "green",
                    "operations": "yellow",
                    "staffing": "green",
                    "margin": "green",
                },
                "riskScore": 52,
            },
            {
                "id": "ST-021",
                "name": "新北板橋文化",
                "district": "Banqiao",
                "city": "New Taipei",
                "manager": "An Lin",
                "lights": {
                    "demand": "yellow",
                    "operations": "yellow",
                    "staffing": "red",
                    "margin": "red",
                },
                "riskScore": 79,
            },
        ],
        "issues": [
            {
                "id": "ISS-1024",
                "title": "晚間負評與清潔分數同步惡化",
                "storeId": "ST-008",
                "storeName": "台北信義 A11",
                "status": "new",
                "severity": "critical",
                "source": "multiSignal",
                "ownerRoleId": "opsLead",
                "ownerName": "營運主管",
                "slaDueAt": "2026-07-05T11:00:00.000Z",
                "createdAt": "2026-07-05T06:24:00.000Z",
                "updatedAt": "2026-07-05T06:24:00.000Z",
                "evidenceIds": [
                    "EV-1024-GR",
                    "EV-1024-CS",
                    "EV-1024-CAM",
                    "EV-1024-IOT",
                    "EV-1024-PAY",
                    "EV-1024-FOUR",
                    "EV-1024-CLN",
                ],
                "summary": (
                    "Google one-star reviews, CS complaints, and cleaning audit all "
                    "point to a peak-hour service quality incident."
                ),
            },
            {
                "id": "ISS-1021",
                "title": "冷氣遠端重啟等待核准",
                "storeId": "ST-014",
                "storeName": "台北大安復興",
                "status": "waitingapproval",
                "severity": "high",
                "source": "iot",
                "ownerRoleId": "facilitiesLead",
                "ownerName": "工務主任",
                "slaDueAt": "2026-07-05T10:30:00.000Z",
                "createdAt": "2026-07-05T04:50:00.000Z",
                "updatedAt": "2026-07-05T07:30:00.000Z",
                "evidenceIds": ["EV-1021-IOT", "EV-1021-PAY"],
                "relatedApprovalId": "APR-502",
                "summary": (
                    "HVAC telemetry shows repeated compressor fault codes; remote "
                    "restart needs manager approval before peak traffic."
                ),
            },
            {
                "id": "ISS-1008",
                "title": "補班日人力不足觀察中",
                "storeId": "ST-021",
                "storeName": "新北板橋文化",
                "status": "observing",
                "severity": "medium",
                "source": "forecastOps",
                "ownerRoleId": "supportLead",
                "ownerName": "客服主管",
                "slaDueAt": "2026-07-06T03:00:00.000Z",
                "createdAt": "2026-07-04T02:10:00.000Z",
                "updatedAt": "2026-07-05T01:15:00.000Z",
                "evidenceIds": ["EV-1008-FOUR", "EV-1008-CS"],
                "summary": (
                    "ForecastOps staffing light remains red after shift swap; CS queue "
                    "is improving but still above baseline."
                ),
            },
        ],
        "evidence": [
            {
                "id": "EV-1024-GR",
                "issueId": "ISS-1024",
                "kind": "googleReview",
                "title": "Google review cluster",
                "sourceLabel": "Google Reviews",
                "summary": (
                    "Three one-star reviews mention sticky tables and slow pickup "
                    "between 19:00 and 21:00."
                ),
                "polarity": "supporting",
                "confidence": 0.91,
                "occurredAt": "2026-07-04T21:22:00.000Z",
            },
            {
                "id": "EV-1024-CS",
                "issueId": "ISS-1024",
                "kind": "csCase",
                "title": "Customer service cases",
                "sourceLabel": "Zendesk POC",
                "summary": "Two refund requests match the same time window and counter lane.",
                "polarity": "supporting",
                "confidence": 0.86,
                "occurredAt": "2026-07-04T21:44:00.000Z",
            },
            {
                "id": "EV-1024-CAM",
                "issueId": "ISS-1024",
                "kind": "camera",
                "title": "Camera event placeholder",
                "sourceLabel": "Camera Access",
                "summary": "Video access is locked until an operator records a purpose for review.",
                "polarity": "neutral",
                "confidence": 0.7,
                "occurredAt": "2026-07-04T20:05:00.000Z",
                "lockedReason": "Purpose confirmation required before camera evidence can be opened.",
            },
            {
                "id": "EV-1024-IOT",
                "issueId": "ISS-1024",
                "kind": "iot",
                "title": "Dining-area sensor anomaly",
                "sourceLabel": "IoT",
                "summary": "Table-zone humidity and waste-bin fill events exceeded normal thresholds.",
                "polarity": "supporting",
                "confidence": 0.82,
                "occurredAt": "2026-07-04T20:12:00.000Z",
            },
            {
                "id": "EV-1024-PAY",
                "issueId": "ISS-1024",
                "kind": "payment",
                "title": "Payment queue slowdown",
                "sourceLabel": "Payment",
                "summary": "Median checkout time rose 34 percent during the complaint window.",
                "polarity": "supporting",
                "confidence": 0.78,
                "occurredAt": "2026-07-04T20:40:00.000Z",
            },
            {
                "id": "EV-1024-FOUR",
                "issueId": "ISS-1024",
                "kind": "forecastOps",
                "title": "ForecastOps four-light snapshot",
                "sourceLabel": "ForecastOps",
                "summary": "Operations light is red; staffing and margin lights are yellow.",
                "polarity": "supporting",
                "confidence": 0.88,
                "occurredAt": "2026-07-05T05:00:00.000Z",
            },
            {
                "id": "EV-1024-CLN",
                "issueId": "ISS-1024",
                "kind": "cleaning",
                "title": "Cleaning checklist miss",
                "sourceLabel": "Cleaning QA",
                "summary": "19:30 lobby reset checklist was not completed before the second dinner wave.",
                "polarity": "supporting",
                "confidence": 0.8,
                "occurredAt": "2026-07-04T19:40:00.000Z",
            },
            {
                "id": "EV-1021-IOT",
                "issueId": "ISS-1021",
                "kind": "iot",
                "title": "HVAC compressor code",
                "sourceLabel": "IoT",
                "summary": "Fault code C-18 repeated four times after 05:00.",
                "polarity": "supporting",
                "confidence": 0.94,
                "occurredAt": "2026-07-05T05:21:00.000Z",
            },
            {
                "id": "EV-1021-PAY",
                "issueId": "ISS-1021",
                "kind": "payment",
                "title": "No revenue drop yet",
                "sourceLabel": "Payment",
                "summary": "Morning payment volume remains inside normal band.",
                "polarity": "contrary",
                "confidence": 0.72,
                "occurredAt": "2026-07-05T07:05:00.000Z",
            },
            {
                "id": "EV-1008-FOUR",
                "issueId": "ISS-1008",
                "kind": "forecastOps",
                "title": "Staffing light red",
                "sourceLabel": "ForecastOps",
                "summary": "Forecast requires two more crew hours between 12:00 and 14:00.",
                "polarity": "supporting",
                "confidence": 0.84,
                "occurredAt": "2026-07-05T01:00:00.000Z",
            },
            {
                "id": "EV-1008-CS",
                "issueId": "ISS-1008",
                "kind": "csCase",
                "title": "Support wait improving",
                "sourceLabel": "Zendesk POC",
                "summary": "Queue wait dropped after shift swap but remains above target.",
                "polarity": "neutral",
                "confidence": 0.76,
                "occurredAt": "2026-07-05T01:10:00.000Z",
            },
        ],
        "auditEvents": [
            {
                "id": "AUD-OPS-7001",
                "occurredAt": "2026-07-05T07:28:00.000Z",
                "actorRoleId": "facilitiesLead",
                "actorName": "工務主任",
                "category": "approval",
                "action": "approval.requested",
                "targetType": "approval",
                "targetId": "APR-502",
                "message": "Store Ops approval APR-502 was requested for ISS-1021.",
                "metadata": {"issueId": "ISS-1021"},
            },
            {
                "id": "AUD-OPS-7002",
                "occurredAt": "2026-07-05T06:24:00.000Z",
                "actorRoleId": "opsLead",
                "actorName": "ForecastOps",
                "category": "workflow",
                "action": "issue.created",
                "targetType": "issue",
                "targetId": "ISS-1024",
                "message": "ISS-1024 created from payment, review, cleaning, and four-light evidence.",
                "metadata": {"issueId": "ISS-1024", "light": "operations", "lightStatus": "red"},
            },
        ],
        "nextAuditOrdinal": 7003,
    }


class InMemoryStoreOpsRepository:
    def __init__(self, initial_state: Mapping[str, Any] | None = None) -> None:
        self._state = _clone(initial_state or _seed_state())
        self._idempotency_results: dict[str, dict[str, Any]] = {}

    def get_state(self) -> dict[str, Any]:
        return _clone(self._state)

    def save_state(self, state: Mapping[str, Any]) -> None:
        self._state = _clone(state)

    def get_idempotency_result(self, key: str) -> dict[str, Any] | None:
        result = self._idempotency_results.get(key)
        return None if result is None else _clone(result)

    def save_idempotency_result(self, key: str, result: Mapping[str, Any]) -> None:
        self._idempotency_results[key] = _clone(result)


class DurableStoreOpsRepository:
    def __init__(self, store: Any) -> None:
        self._store = store

    def get_state(self) -> dict[str, Any]:
        state = self._store.get(_STORE_OPS_STATE_COLLECTION, _STORE_OPS_STATE_DOC_ID)
        if state is None:
            state = _seed_state()
            self.save_state(state)
        return _clone(state)

    def save_state(self, state: Mapping[str, Any]) -> None:
        self._store.put(
            _STORE_OPS_STATE_COLLECTION,
            _STORE_OPS_STATE_DOC_ID,
            _clone(state),
        )

    def get_idempotency_result(self, key: str) -> dict[str, Any] | None:
        result = self._store.get(_STORE_OPS_IDEMPOTENCY_COLLECTION, key)
        return None if result is None else _clone(result)

    def save_idempotency_result(self, key: str, result: Mapping[str, Any]) -> None:
        self._store.put(_STORE_OPS_IDEMPOTENCY_COLLECTION, key, _clone(result))


class StoreOpsService:
    def __init__(
        self,
        repository: StoreOpsRepository | None = None,
        *,
        audit_log: InMemoryAuditLog | None = None,
    ) -> None:
        self._repository = repository or InMemoryStoreOpsRepository()
        self._audit_log = audit_log or InMemoryAuditLog()

    def snapshot(
        self,
        *,
        query: str | None = None,
        statuses: Iterable[str] = (),
        sources: Iterable[str] = (),
        severities: Iterable[str] = (),
        mine_only: bool = False,
        role_id: str = "opsLead",
        light: str | None = None,
        light_status: str | None = None,
    ) -> dict[str, Any]:
        state = self._repository.get_state()
        stores = state["stores"]
        issues = self._filter_issues(
            state,
            query=query,
            statuses=statuses,
            sources=sources,
            severities=severities,
            mine_only=mine_only,
            role_id=role_id,
            light=light,
            light_status=light_status,
        )
        return {
            "stores": stores,
            "issues": issues,
            "evidence": state["evidence"],
            "auditEvents": _sorted_audit_events(state),
            "fourLightSummary": _four_light_summary(stores=stores, issues=state["issues"]),
            "count": len(issues),
            "filters": {
                "query": query or "",
                "statuses": list(statuses),
                "sources": list(sources),
                "severities": list(severities),
                "mineOnly": mine_only,
                "roleId": role_id,
                "light": light,
                "lightStatus": light_status,
            },
        }

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        state = self._repository.get_state()
        return _clone(_find_issue(state, issue_id))

    def issue_evidence(self, issue_id: str) -> dict[str, Any]:
        state = self._repository.get_state()
        issue = _find_issue(state, issue_id)
        evidence = [item for item in state["evidence"] if item["id"] in issue["evidenceIds"]]
        return {"issue": _clone(issue), "evidence": _clone(evidence)}

    def transition_issue(
        self,
        *,
        issue_id: str,
        action_type: str,
        payload: Mapping[str, Any],
        correlation_id: str,
        idempotency_key: str | None = None,
        actor_role_id: str = "opsLead",
        actor_name: str = "Operator",
    ) -> dict[str, Any]:
        if idempotency_key:
            replay = self._repository.get_idempotency_result(idempotency_key)
            if replay is not None:
                replay["idempotentReplay"] = True
                return replay

        state = self._repository.get_state()
        issue = _find_issue(state, issue_id)
        previous_status = issue["status"]
        normalized_action = _normalize_action(action_type)

        if normalized_action == "triage":
            self._apply_triage(issue, payload)
        elif normalized_action == "assign":
            self._apply_assign(issue, payload)
        elif normalized_action == "actions":
            self._apply_action(issue, payload)
        elif normalized_action == "field-report":
            self._apply_field_report(issue, payload)
        elif normalized_action == "outcome":
            self._apply_outcome(issue, payload)
        elif normalized_action == "escalate":
            self._apply_escalation(issue, payload)
        elif normalized_action == "reply-review":
            self._apply_reply_review(issue, payload)
        elif normalized_action == "transfer":
            self._apply_transfer(issue, payload)
        else:
            raise StoreOpsNotFound(f"unknown Store Ops action: {action_type}")

        issue["updatedAt"] = _now_iso()
        audit = _append_audit_event(
            state,
            action=f"issue.{normalized_action}",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            category="workflow",
            message=f"{issue_id} {normalized_action} moved {previous_status} -> {issue['status']}.",
            metadata={
                "issueId": issue_id,
                "previousStatus": previous_status,
                "status": issue["status"],
                "actionType": normalized_action,
                "idempotencyKey": idempotency_key,
            },
        )
        self._record_shared_audit(
            event_type="operator.store_ops.issue_transition",
            actor=actor_name,
            action=normalized_action,
            resource=f"operator/store-ops/issues/{issue_id}",
            outcome="accepted",
            correlation_id=correlation_id,
            metadata=audit["metadata"],
        )
        self._repository.save_state(state)

        result = self.snapshot()
        result.update(
            {
                "issue": _clone(issue),
                "auditEvent": _clone(audit),
                "idempotentReplay": False,
            }
        )
        if idempotency_key:
            self._repository.save_idempotency_result(idempotency_key, result)
        return result

    def record_camera_purpose(
        self,
        *,
        issue_id: str,
        payload: Mapping[str, Any],
        correlation_id: str,
        idempotency_key: str | None = None,
        actor_role_id: str = "opsLead",
        actor_name: str = "Operator",
    ) -> dict[str, Any]:
        if idempotency_key:
            replay = self._repository.get_idempotency_result(idempotency_key)
            if replay is not None:
                replay["idempotentReplay"] = True
                return replay

        state = self._repository.get_state()
        issue = _find_issue(state, issue_id)
        camera_evidence = _find_camera_evidence(state, issue)
        _validate_camera_purpose(payload)

        camera_evidence.pop("lockedReason", None)
        camera_evidence["purpose"] = str(payload.get("purpose", "")).strip()
        camera_evidence["cameraLocation"] = str(payload.get("cameraLocation", "")).strip()
        camera_evidence["timeWindow"] = str(payload.get("timeWindow", "")).strip()
        camera_evidence["retentionHours"] = int(payload.get("retentionHours") or 24)
        camera_evidence["unlockedAt"] = _now_iso()
        camera_evidence["summary"] = (
            "Camera evidence unlocked for a recorded, audit-scoped purpose."
        )

        audit = _append_audit_event(
            state,
            action="evidence.camera_purpose.recorded",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            category="evidence",
            message=f"{issue_id} camera evidence purpose recorded before access.",
            metadata={
                "issueId": issue_id,
                "evidenceId": camera_evidence["id"],
                "purpose": camera_evidence["purpose"],
                "cameraLocation": camera_evidence["cameraLocation"],
                "timeWindow": camera_evidence["timeWindow"],
                "retentionHours": camera_evidence["retentionHours"],
                "idempotencyKey": idempotency_key,
            },
        )
        self._record_shared_audit(
            event_type="operator.store_ops.camera_purpose",
            actor=actor_name,
            action="record_camera_purpose",
            resource=f"operator/store-ops/issues/{issue_id}/evidence/{camera_evidence['id']}",
            outcome="accepted",
            correlation_id=correlation_id,
            metadata=audit["metadata"],
        )
        self._repository.save_state(state)

        result = self.snapshot()
        result.update(
            {
                "issue": _clone(issue),
                "evidenceItem": _clone(camera_evidence),
                "auditEvent": _clone(audit),
                "idempotentReplay": False,
            }
        )
        if idempotency_key:
            self._repository.save_idempotency_result(idempotency_key, result)
        return result

    def _apply_triage(self, issue: MutableMapping[str, Any], payload: Mapping[str, Any]) -> None:
        _require_status(issue, {"new", "waitingevidence"}, "triage")
        severity = payload.get("severity")
        if isinstance(severity, str) and severity in _ISSUE_SEVERITIES:
            issue["severity"] = severity
        decision = payload.get("decision")
        need_evidence = bool(payload.get("needEvidence")) or decision == "needEvidence"
        issue["status"] = "waitingevidence" if need_evidence else "triaged"

    def _apply_assign(self, issue: MutableMapping[str, Any], payload: Mapping[str, Any]) -> None:
        _require_status(issue, {"triaged"}, "assign")
        owner_role_id = str(payload.get("ownerRoleId") or issue["ownerRoleId"])
        owner_name = str(payload.get("ownerName") or issue["ownerName"]).strip()
        sla_due_at = str(payload.get("slaDueAt") or issue["slaDueAt"]).strip()
        issue["ownerRoleId"] = owner_role_id
        issue["ownerName"] = owner_name or issue["ownerName"]
        issue["slaDueAt"] = sla_due_at or issue["slaDueAt"]
        issue["status"] = "assigned"

    def _apply_action(self, issue: MutableMapping[str, Any], payload: Mapping[str, Any]) -> None:
        _require_status(issue, {"assigned"}, "actions")
        issue["status"] = "waitingapproval" if bool(payload.get("requiresApproval")) else "inprogress"

    def _apply_field_report(self, issue: MutableMapping[str, Any], payload: Mapping[str, Any]) -> None:
        _require_status(issue, {"inprogress", "executed"}, "field-report")
        checklist_status = payload.get("checklistStatus")
        issue["status"] = "waitingevidence" if checklist_status == "blocked" else "observing"

    def _apply_outcome(self, issue: MutableMapping[str, Any], payload: Mapping[str, Any]) -> None:
        _require_status(issue, {"observing", "outcomeready"}, "outcome")
        outcome = payload.get("outcome")
        close_issue = bool(payload.get("closeIssue"))
        if outcome == "effective" and close_issue:
            issue["status"] = "closed"
        elif outcome in {"ineffective", "inconclusive"}:
            issue["status"] = "escalated"
        else:
            issue["status"] = "outcomeready"

    def _apply_escalation(self, issue: MutableMapping[str, Any], payload: Mapping[str, Any]) -> None:
        if issue["status"] == "closed":
            raise StoreOpsConflict("closed issues cannot be escalated")
        issue["status"] = "escalated"
        issue["relatedGrowthId"] = payload.get("target") if payload.get("target") == "growth" else issue.get("relatedGrowthId")

    def _apply_reply_review(self, issue: MutableMapping[str, Any], payload: Mapping[str, Any]) -> None:
        if issue["status"] == "closed":
            raise StoreOpsConflict("closed issues cannot accept reply review")
        issue["updatedAt"] = _now_iso()

    def _apply_transfer(self, issue: MutableMapping[str, Any], payload: Mapping[str, Any]) -> None:
        if issue["status"] == "closed":
            raise StoreOpsConflict("closed issues cannot be transferred")
        target_role = str(payload.get("targetRoleId") or issue["ownerRoleId"])
        target_owner = str(payload.get("targetOwnerName") or "").strip()
        issue["ownerRoleId"] = target_role
        if target_owner:
            issue["ownerName"] = target_owner

    def _filter_issues(
        self,
        state: Mapping[str, Any],
        *,
        query: str | None,
        statuses: Iterable[str],
        sources: Iterable[str],
        severities: Iterable[str],
        mine_only: bool,
        role_id: str,
        light: str | None,
        light_status: str | None,
    ) -> list[dict[str, Any]]:
        status_set = {status for status in statuses if status in _ISSUE_STATUSES}
        source_set = {source for source in sources if source in _ISSUE_SOURCES}
        severity_set = {severity for severity in severities if severity in _ISSUE_SEVERITIES}
        normalized_query = (query or "").strip().lower()
        normalized_light = light if light in _LIGHT_DIMENSIONS else None
        normalized_light_status = light_status if light_status in _LIGHT_STATUSES else None
        stores_by_id = {store["id"]: store for store in state["stores"]}

        filtered: list[dict[str, Any]] = []
        for issue in state["issues"]:
            store = stores_by_id.get(issue["storeId"], {})
            if mine_only and issue["ownerRoleId"] != role_id:
                continue
            if status_set and issue["status"] not in status_set:
                continue
            if source_set and issue["source"] not in source_set:
                continue
            if severity_set and issue["severity"] not in severity_set:
                continue
            if normalized_light and normalized_light_status:
                if store.get("lights", {}).get(normalized_light) != normalized_light_status:
                    continue
            if normalized_query:
                haystack = " ".join(
                    [
                        str(issue.get("id", "")),
                        str(issue.get("title", "")),
                        str(issue.get("storeName", "")),
                        str(issue.get("summary", "")),
                        str(issue.get("ownerName", "")),
                        str(issue.get("status", "")),
                    ]
                ).lower()
                if normalized_query not in haystack:
                    continue
            filtered.append(_clone(issue))

        return sorted(
            filtered,
            key=lambda item: (
                -_SEVERITY_WEIGHT.get(str(item.get("severity")), 0),
                str(item.get("slaDueAt") or ""),
            ),
        )

    def _record_shared_audit(
        self,
        *,
        event_type: str,
        actor: str,
        action: str,
        resource: str,
        outcome: str,
        correlation_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self._audit_log.record(
            AuditEvent(
                event_type=event_type,
                actor=actor,
                action=action,
                resource=resource,
                outcome=outcome,
                correlation_id=correlation_id,
                metadata=dict(metadata),
            )
        )


def _normalize_action(action_type: str) -> str:
    normalized = action_type.strip()
    aliases = {
        "action": "actions",
        "fieldReport": "field-report",
        "cameraPurpose": "camera-purpose",
        "replyReview": "reply-review",
    }
    return aliases.get(normalized, normalized)


def _require_status(issue: Mapping[str, Any], allowed: set[str], action: str) -> None:
    status = str(issue.get("status"))
    if status not in allowed:
        allowed_list = ", ".join(sorted(allowed))
        raise StoreOpsConflict(
            f"{action} is invalid for issue {issue.get('id')} in status {status}; expected {allowed_list}"
        )


def _find_issue(state: Mapping[str, Any], issue_id: str) -> MutableMapping[str, Any]:
    for issue in state["issues"]:
        if issue["id"] == issue_id:
            return issue
    raise StoreOpsNotFound(f"issue not found: {issue_id}")


def _find_camera_evidence(
    state: Mapping[str, Any], issue: Mapping[str, Any]
) -> MutableMapping[str, Any]:
    evidence_ids = set(issue.get("evidenceIds") or [])
    for item in state["evidence"]:
        if item["id"] in evidence_ids and item.get("kind") == "camera":
            return item
    raise StoreOpsNotFound(f"camera evidence not found for issue: {issue.get('id')}")


def _validate_camera_purpose(payload: Mapping[str, Any]) -> None:
    purpose = str(payload.get("purpose") or "").strip()
    if not purpose:
        raise StoreOpsPolicyError("camera purpose is required")
    if not bool(payload.get("privacyAcknowledged")):
        raise StoreOpsPolicyError("camera privacy acknowledgement is required")
    lowered = purpose.lower()
    if not any(keyword in lowered for keyword in _PERMITTED_CAMERA_PURPOSE_KEYWORDS):
        raise StoreOpsPolicyError("camera purpose is not permitted for Store Ops evidence access")
    retention_hours = int(payload.get("retentionHours") or 24)
    if retention_hours < 1 or retention_hours > 72:
        raise StoreOpsPolicyError("camera retention hours must be between 1 and 72")


def _append_audit_event(
    state: MutableMapping[str, Any],
    *,
    action: str,
    actor_role_id: str,
    actor_name: str,
    category: str,
    message: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    ordinal = int(state.get("nextAuditOrdinal") or 1)
    state["nextAuditOrdinal"] = ordinal + 1
    event = {
        "id": f"AUD-OPS-{ordinal}",
        "occurredAt": _now_iso(),
        "actorRoleId": actor_role_id,
        "actorName": actor_name,
        "category": category,
        "action": action,
        "targetType": "issue",
        "targetId": str(metadata["issueId"]),
        "message": message,
        "metadata": dict(metadata),
    }
    state["auditEvents"].append(event)
    return event


def _sorted_audit_events(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        _clone(state["auditEvents"]),
        key=lambda item: str(item.get("occurredAt") or ""),
        reverse=True,
    )


def _four_light_summary(
    *,
    stores: Iterable[Mapping[str, Any]],
    issues: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    store_list = list(stores)
    issues_by_store: dict[str, list[Mapping[str, Any]]] = {}
    for issue in issues:
        issues_by_store.setdefault(str(issue.get("storeId")), []).append(issue)

    summary: list[dict[str, Any]] = []
    for dimension in _LIGHT_DIMENSIONS:
        counts = {status: 0 for status in _LIGHT_STATUSES}
        issue_counts = {status: 0 for status in _LIGHT_STATUSES}
        for store in store_list:
            light_status = str(store.get("lights", {}).get(dimension, "green"))
            if light_status not in counts:
                continue
            counts[light_status] += 1
            issue_counts[light_status] += len(issues_by_store.get(str(store.get("id")), []))
        summary.append(
            {
                "dimension": dimension,
                "label": {
                    "demand": "Demand",
                    "operations": "Operations",
                    "staffing": "Staffing",
                    "margin": "Margin",
                }[dimension],
                "counts": counts,
                "issueCounts": issue_counts,
            }
        )
    return summary


__all__ = [
    "DurableStoreOpsRepository",
    "InMemoryStoreOpsRepository",
    "StoreOpsConflict",
    "StoreOpsNotFound",
    "StoreOpsPolicyError",
    "StoreOpsService",
]
