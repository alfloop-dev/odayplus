"""OperatorStateService — application-layer facade for operator console state.

This service mediates between the HTTP route sub-modules (shell, issues,
approvals, evidence, seed) and the in-memory R4 seed store.  It owns:

- get_today() → bootstrap / today payload
- get_work_queue() → current issues list
- get_approvals() → current approval decisions list
- transition_issue() → issue lifecycle write + audit
- decide_approval() → approval decision write + audit
- confirm_evidence_purpose() → evidence unlock write + audit
- reset_to_seed() → deterministic seed reset

Not changing: persistence layer, auth/RBAC, external DB adapters.
Composes with: operator_modules/* sub-routers via create_operator_router().
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from modules.opsboard.domain.r4_dtos import (
    ISSUE_STATUS_BY_ACTION,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    EvidencePurposeRequest,
    EvidencePurposeResponse,
    IssueTransitionRequest,
    IssueTransitionResponse,
)
from modules.opsboard.infrastructure.seed_data import load_r4_seed

WorkspaceId = str
OperatorRoleId = str


WORKSPACES: list[dict[str, str]] = [
    {"id": "today", "label": "今日工作", "shortLabel": "Today", "description": "Today"},
    {"id": "store", "label": "門市營運", "shortLabel": "Store", "description": "Store Ops"},
    {"id": "growth", "label": "營收成長", "shortLabel": "Growth", "description": "Growth"},
    {"id": "network", "label": "展店與店網", "shortLabel": "Network", "description": "Network"},
    {"id": "govern", "label": "治理稽核", "shortLabel": "Govern", "description": "Govern"},
]


ROLES: list[dict[str, Any]] = [
    {
        "id": "ops-lead",
        "label": "營運主管",
        "subtitle": "全域監控、跨域指派與核准",
        "allowedWorkspaces": ["today", "store", "growth", "network", "govern"],
        "heroName": "林承翰",
    },
    {
        "id": "cs-lead",
        "label": "客服主管",
        "subtitle": "評論、客服案件與門市回覆",
        "allowedWorkspaces": ["today", "store", "govern"],
        "heroName": "張珮珊",
    },
    {
        "id": "field-lead",
        "label": "工務主任",
        "subtitle": "設備、現場維修與執行回報",
        "allowedWorkspaces": ["today", "store"],
        "heroName": "陳建宏",
    },
    {
        "id": "marketing-manager",
        "label": "行銷經理",
        "subtitle": "活動、分群、定價建議",
        "allowedWorkspaces": ["today", "growth", "govern"],
        "heroName": "黃仕杰",
    },
    {
        "id": "expansion-manager",
        "label": "展店經理",
        "subtitle": "HeatZone、候選點與 SiteScore",
        "allowedWorkspaces": ["today", "network", "govern"],
        "heroName": "王若寧",
    },
    {
        "id": "expansion-staff",
        "label": "展店專員",
        "subtitle": "送件、資料補正與自有案件追蹤",
        "allowedWorkspaces": ["today", "network"],
        "heroName": "林曉青",
    },
    {
        "id": "data-steward",
        "label": "資料管理員",
        "subtitle": "來源、解析與身分資料治理",
        "allowedWorkspaces": ["today", "network", "govern"],
        "heroName": "資料管理員",
    },
    {
        "id": "governance-reviewer",
        "label": "治理審查員",
        "subtitle": "唯讀檢查政策、決策與稽核證據",
        "allowedWorkspaces": ["today", "network", "govern"],
        "heroName": "治理審查員",
    },
    {
        "id": "privacy-officer",
        "label": "隱私管理員",
        "subtitle": "目的綁定的敏感證據與隱私審查",
        "allowedWorkspaces": ["today", "network", "govern"],
        "heroName": "隱私管理員",
    },
    {
        "id": "permission-limited",
        "label": "受限檢視者",
        "subtitle": "唯讀且遮罩的案件檢視",
        "allowedWorkspaces": ["today", "network"],
        "heroName": "受限檢視者",
    },
    {
        "id": "pm-audit",
        "label": "PM／稽核",
        "subtitle": "模型、決策追蹤與稽核線索",
        "allowedWorkspaces": ["today", "store", "network", "govern"],
        "heroName": "周子安",
    },
]


SYSTEM_ROLE_TO_OPERATOR_ROLE: dict[str, OperatorRoleId] = {
    "operations_manager": "ops-lead",
    "regional_supervisor": "field-lead",
    "marketing_manager": "marketing-manager",
    "expansion_user": "expansion-staff",
    "site_reviewer": "expansion-manager",
    "data_owner": "data-steward",
    "auditor": "governance-reviewer",
    "architecture_owner": "governance-reviewer",
    "finance_legal": "privacy-officer",
    "expansion-staff": "expansion-staff",
    "expansion-manager": "expansion-manager",
    "data-steward": "data-steward",
    "governance-reviewer": "governance-reviewer",
    "privacy-officer": "privacy-officer",
    "permission-limited": "permission-limited",
}


ALL_ROLE_IDS = [role["id"] for role in ROLES]


QUEUE_METADATA: dict[str, dict[str, Any]] = {
    "ISS-1024": {
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
        "tags": ["支付", "客服", "預測"],
        "target": {"workspace": "store", "entityId": "ISS-1024", "tab": "triage"},
    },
    "ISS-1021": {
        "roles": ["ops-lead", "field-lead", "pm-audit"],
        "tags": ["IoT", "設備"],
        "target": {"workspace": "store", "entityId": "ISS-1021", "tab": "assign"},
    },
    "GRW-201": {
        "roles": ["ops-lead", "marketing-manager"],
        "tags": ["成長", "活動"],
        "target": {"workspace": "growth", "entityId": "GRW-201", "tab": "campaign"},
    },
    "APR-501": {
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
        "tags": ["SiteScore", "核准"],
        "target": {"workspace": "govern", "entityId": "APR-501", "tab": "approvals"},
    },
    "RV-701": {
        "roles": ["ops-lead", "expansion-manager"],
        "tags": ["Listing", "佐證"],
        "target": {"workspace": "network", "entityId": "RV-701", "tab": "review"},
    },
    "NET-305": {
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
        "tags": ["NetPlan", "AVM"],
        "target": {"workspace": "network", "entityId": "NET-305", "tab": "rebalance"},
    },
}


APPROVAL_METADATA: dict[str, dict[str, Any]] = {
    "APR-501": {
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
        "target": {"workspace": "govern", "entityId": "APR-501", "tab": "approvals"},
    },
    "APR-487": {
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
        "target": {"workspace": "govern", "entityId": "APR-487", "tab": "approvals"},
    },
    "GRW-207": {
        "roles": ["ops-lead", "marketing-manager"],
        "target": {"workspace": "govern", "entityId": "GRW-207", "tab": "approvals"},
    },
}


RISK_ROLES: dict[str, list[str]] = {
    "大安復興店": ["ops-lead", "cs-lead", "pm-audit"],
    "板橋中山店": ["ops-lead", "field-lead", "pm-audit"],
    "忠孝敦化店": ["ops-lead", "marketing-manager"],
    "台北車站店": ["ops-lead", "field-lead"],
    "板橋府中候選點": ["expansion-manager", "pm-audit", "ops-lead"],
}


NOTIFICATION_METADATA: dict[str, dict[str, Any]] = {
    "SLA 即將到期": {
        "id": "NTF-SLA-1024",
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
        "target": {"workspace": "store", "entityId": "ISS-1024", "tab": "triage"},
    },
    "核准中心新增": {
        "id": "NTF-APR-501",
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
        "target": {"workspace": "govern", "entityId": "APR-501", "tab": "approvals"},
    },
    "模型快照更新": {
        "id": "NTF-MODEL-0600",
        "roles": ["ops-lead", "marketing-manager", "expansion-manager", "pm-audit"],
        "target": {"workspace": "govern", "entityId": "AUD-044", "tab": "audit"},
    },
}


AUDIT_ROLES_BY_CATEGORY: dict[str, list[str]] = {
    "Model snapshot": ["ops-lead", "cs-lead", "pm-audit"],
    "Decision log": ["ops-lead", "cs-lead", "pm-audit"],
    "Network review": ["ops-lead", "expansion-manager", "pm-audit"],
    "Audit trail": ["ops-lead", "pm-audit"],
    "Workflow": ["ops-lead", "cs-lead", "field-lead", "pm-audit"],
}


def _strip_roles(item: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(item)
    clean.pop("roles", None)
    return clean


def _has_role(item: dict[str, Any], role_id: OperatorRoleId) -> bool:
    roles = item.get("roles")
    return not roles or role_id in roles


def _approval_is_visible(item: dict[str, Any]) -> bool:
    status = str(item.get("status", "")).strip().lower()
    return status not in {"approved", "rejected"}


class OperatorStateService:
    """In-memory operator console state service.

    Thread-safety: not guaranteed for concurrent writes in production.
    This service targets single-process FastAPI deployments with
    synchronous route handlers (no async_to_thread escalation needed
    for in-memory state).
    """

    def __init__(self) -> None:
        self._state: dict[str, Any] = load_r4_seed()
        self._idempotency_cache: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def resolve_role(
        self,
        *,
        operator_role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
    ) -> dict[str, Any]:
        role_ids = {role["id"] for role in ROLES}
        if operator_role_id in role_ids:
            return self.get_role(operator_role_id)

        if subject_id and subject_id.startswith("operator-"):
            candidate = subject_id.removeprefix("operator-")
            if candidate in role_ids:
                return self.get_role(candidate)

        for raw in (system_roles or "").split(","):
            candidate = SYSTEM_ROLE_TO_OPERATOR_ROLE.get(raw.strip())
            if candidate:
                return self.get_role(candidate)

        return self.get_role("ops-lead")

    def get_role(self, role_id: str | None) -> dict[str, Any]:
        return deepcopy(next((role for role in ROLES if role["id"] == role_id), ROLES[0]))

    def get_today(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a role-aware shell envelope for bootstrap/today."""
        role = self.resolve_role(
            operator_role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
        )
        return self._build_envelope(role=role, correlation_id=correlation_id)

    def get_work_queue(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a deep copy of the current work-queue items."""
        role = self.resolve_role(
            operator_role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
        )
        return self._filtered_queue(role_id=role["id"])

    def get_approvals(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a deep copy of the current approval decisions."""
        role = self.resolve_role(
            operator_role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
        )
        return self._filtered_approvals(role_id=role["id"])

    def search(
        self,
        query: str,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Search the role-aware shell index."""
        envelope = self.get_today(
            role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
            correlation_id=correlation_id,
        )
        normalized = query.strip().casefold()
        items = envelope["search"]["items"]
        if normalized:
            items = [
                item
                for item in items
                if normalized
                in " ".join(
                    [
                        item.get("id", ""),
                        item.get("label", ""),
                        item.get("description", ""),
                        item.get("keywords", ""),
                    ]
                ).casefold()
            ]
        return {"meta": envelope["meta"], "items": items, "count": len(items)}

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def transition_issue(
        self,
        *,
        issue_id: str,
        action_type: str,
        body: IssueTransitionRequest,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> IssueTransitionResponse:
        """Transition an issue through its lifecycle, appending an audit event."""
        if idempotency_key and idempotency_key in self._idempotency_cache:
            return self._idempotency_cache[idempotency_key]  # type: ignore[return-value]

        new_status = ISSUE_STATUS_BY_ACTION.get(action_type, "closed")
        for item in self._state.get("workQueue", []):
            if item.get("id") == issue_id:
                item["status"] = new_status
                break

        audit_id = str(uuid.uuid4())
        self._state.setdefault("auditFeed", []).insert(
            0,
            {
                "actor": body.actorName or "System",
                "category": "Workflow",
                "detail": (
                    f"Issue {issue_id} transitioned via '{action_type}' to '{new_status}'."
                    + (f" Note: {body.note}" if body.note else "")
                ),
                "time": datetime.now(UTC).strftime("%H:%M"),
                "auditEventId": audit_id,
            },
        )

        response = IssueTransitionResponse(
            issueId=issue_id,
            newStatus=new_status,
            auditEventId=audit_id,
            correlationId=correlation_id,
        )
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = response
        return response

    def decide_approval(
        self,
        *,
        approval_id: str,
        body: ApprovalDecisionRequest,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> ApprovalDecisionResponse:
        """Record an approval decision, appending an audit event."""
        if idempotency_key and idempotency_key in self._idempotency_cache:
            return self._idempotency_cache[idempotency_key]  # type: ignore[return-value]

        for dec in self._state.get("decisions", []):
            if dec.get("id") == approval_id:
                dec["status"] = body.status
                break

        audit_id = str(uuid.uuid4())
        self._state.setdefault("auditFeed", []).insert(
            0,
            {
                "actor": body.actorName or "System",
                "category": "Decision log",
                "detail": (
                    f"Approval {approval_id} decided: {body.status}. Reason: {body.reason}"
                ),
                "time": datetime.now(UTC).strftime("%H:%M"),
                "auditEventId": audit_id,
            },
        )

        response = ApprovalDecisionResponse(
            approvalId=approval_id,
            newStatus=body.status,
            auditEventId=audit_id,
            correlationId=correlation_id,
        )
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = response
        return response

    def upsert_network_rebalance_approval(self, approval: dict[str, Any]) -> dict[str, Any]:
        """Create or update a Govern approval row from Network Rebalance.

        The existing Govern list stores lightweight decision cards, so the
        rebalance service passes a card-shaped payload and keeps the full
        workflow state in its own service. This method only makes that pending
        approval visible through /operator/approvals and the shell envelope.
        """
        approval = deepcopy(approval)
        decisions = self._state.setdefault("decisions", [])
        for index, existing in enumerate(decisions):
            if existing.get("id") == approval.get("id"):
                decisions[index] = approval
                break
        else:
            decisions.insert(0, approval)

        self._state.setdefault("auditFeed", []).insert(
            0,
            {
                "actor": approval.get("requestedBy") or "Expansion Manager",
                "category": "Decision log",
                "detail": f"Created Network Rebalance approval {approval.get('id')}.",
                "time": datetime.now(UTC).strftime("%H:%M"),
                "auditEventId": str(uuid.uuid4()),
            },
        )
        return deepcopy(approval)

    def confirm_evidence_purpose(
        self,
        *,
        evidence_id: str,
        body: EvidencePurposeRequest,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> EvidencePurposeResponse:
        """Unlock a locked evidence item by purpose declaration."""
        if idempotency_key and idempotency_key in self._idempotency_cache:
            return self._idempotency_cache[idempotency_key]  # type: ignore[return-value]

        audit_id = str(uuid.uuid4())
        self._state.setdefault("auditFeed", []).insert(
            0,
            {
                "actor": body.actorName or "Operator",
                "category": "Audit trail",
                "detail": f"Unlocked evidence {evidence_id} with purpose: {body.purpose}",
                "time": datetime.now(UTC).strftime("%H:%M"),
                "auditEventId": audit_id,
            },
        )

        response = EvidencePurposeResponse(
            evidenceId=evidence_id,
            purpose=body.purpose,
            auditEventId=audit_id,
            correlationId=correlation_id,
        )
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = response
        return response

    def reset_to_seed(self) -> None:
        """Deterministically reset state to the canonical R4 seed.

        Clears the idempotency cache as well so tests get a clean slate.
        """
        self._state = load_r4_seed()
        self._idempotency_cache = {}

    # ------------------------------------------------------------------
    # Envelope builders
    # ------------------------------------------------------------------

    def _filtered_queue(self, *, role_id: OperatorRoleId) -> list[dict[str, Any]]:
        rows = [self._enrich_queue_item(item) for item in self._state.get("workQueue", [])]
        return [_strip_roles(item) for item in rows if _has_role(item, role_id)]

    def _filtered_approvals(self, *, role_id: OperatorRoleId) -> list[dict[str, Any]]:
        rows = [self._enrich_approval_item(item) for item in self._state.get("decisions", [])]
        return [
            _strip_roles(item)
            for item in rows
            if _has_role(item, role_id) and _approval_is_visible(item)
        ]

    def _build_envelope(
        self, *, role: dict[str, Any], correlation_id: str | None
    ) -> dict[str, Any]:
        role_id = role["id"]
        queue = self._filtered_queue(role_id=role_id)
        approvals = self._filtered_approvals(role_id=role_id)
        risk_rows = self._filtered_risk_rows(role_id=role_id)
        notifications = self._filtered_notifications(role_id=role_id)
        audit_feed = self._filtered_audit_feed(role_id=role_id)
        search_items = self._build_search_items(role=role, queue=queue, approvals=approvals)
        counts = {
            "notifications": len(notifications),
            "approvals": len(approvals),
            "taskCenter": len(queue),
            "critical": sum(1 for item in queue if item.get("tone") == "danger"),
            "search": len(search_items),
        }
        kpis = self._build_kpis(role=role, counts=counts, queue=queue)

        envelope = {
            "meta": {
                "generatedAt": datetime.now(UTC).isoformat(),
                "correlationId": correlation_id,
                "role": deepcopy(role),
                "counts": counts,
                "source": "operator-shell-api-envelope",
            },
            "navigation": {
                "roles": deepcopy(ROLES),
                "workspaces": [
                    {
                        **deepcopy(workspace),
                        "allowed": workspace["id"] in role["allowedWorkspaces"],
                    }
                    for workspace in WORKSPACES
                ],
                "allowedWorkspaces": list(role["allowedWorkspaces"]),
            },
            "header": {
                "counts": counts,
                "taskCenter": {"label": "Task Center", "count": counts["taskCenter"]},
            },
            "today": {
                "hero": {
                    "name": role.get("heroName", role["label"]),
                    "roleLabel": role["label"],
                    "scope": self._scope_label(role_id),
                    "dateLabel": "2026/07/05 ・週日",
                },
                "kpis": kpis,
                "queue": queue,
                "decisions": approvals,
                "riskRows": risk_rows,
                "auditFeed": audit_feed,
            },
            "search": {"items": search_items, "count": len(search_items)},
            "notifications": notifications,
            "approvals": approvals,
            "workQueue": queue,
            "kpis": kpis,
            "decisions": approvals,
            "riskRows": risk_rows,
            "auditFeed": audit_feed,
        }
        return deepcopy(envelope)

    def _enrich_queue_item(self, item: dict[str, Any]) -> dict[str, Any]:
        enriched = deepcopy(item)
        metadata = QUEUE_METADATA.get(str(enriched.get("id")), {})
        enriched["roles"] = list(metadata.get("roles", ALL_ROLE_IDS))
        enriched["tags"] = list(metadata.get("tags", []))
        enriched["target"] = deepcopy(
            metadata.get(
                "target",
                {
                    "workspace": enriched.get("workspace", "today"),
                    "entityId": enriched.get("id"),
                    "tab": "overview",
                },
            )
        )
        return enriched

    def _enrich_approval_item(self, item: dict[str, Any]) -> dict[str, Any]:
        enriched = deepcopy(item)
        metadata = APPROVAL_METADATA.get(str(enriched.get("id")), {})
        enriched["roles"] = list(metadata.get("roles", ALL_ROLE_IDS))
        enriched["target"] = deepcopy(
            metadata.get(
                "target",
                {"workspace": "govern", "entityId": enriched.get("id"), "tab": "approvals"},
            )
        )
        return enriched

    def _filtered_risk_rows(self, *, role_id: OperatorRoleId) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self._state.get("riskRows", []):
            enriched = deepcopy(item)
            enriched["roles"] = list(RISK_ROLES.get(str(enriched.get("label")), ALL_ROLE_IDS))
            if _has_role(enriched, role_id):
                rows.append(_strip_roles(enriched))
        return rows

    def _filtered_notifications(self, *, role_id: OperatorRoleId) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self._state.get("notifications", []):
            enriched = deepcopy(item)
            metadata = NOTIFICATION_METADATA.get(str(enriched.get("title")), {})
            enriched["id"] = metadata.get("id", enriched.get("title"))
            enriched["roles"] = list(metadata.get("roles", ALL_ROLE_IDS))
            if "target" in metadata:
                enriched["target"] = deepcopy(metadata["target"])
            if _has_role(enriched, role_id):
                rows.append(_strip_roles(enriched))
        return rows

    def _filtered_audit_feed(self, *, role_id: OperatorRoleId) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self._state.get("auditFeed", []):
            enriched = deepcopy(item)
            enriched["roles"] = list(
                AUDIT_ROLES_BY_CATEGORY.get(str(enriched.get("category")), ALL_ROLE_IDS)
            )
            if _has_role(enriched, role_id):
                rows.append(_strip_roles(enriched))
        return rows[:6]

    def _build_kpis(
        self,
        *,
        role: dict[str, Any],
        counts: dict[str, int],
        queue: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        role_focus = {
            "ops-lead": ("跨域 SLA", "全域隊列"),
            "cs-lead": ("客服回覆", "評論與退款"),
            "field-lead": ("現場派工", "設備與回報"),
            "marketing-manager": ("活動草稿", "會員成長"),
            "expansion-manager": ("SiteScore", "候選點佐證"),
            "pm-audit": ("稽核抽查", "模型追蹤"),
        }.get(role["id"], ("今日焦點", "角色隊列"))
        due_label = queue[0]["status"] if queue else "Clear"
        return [
            {
                "label": "今日待處理",
                "value": str(counts["taskCenter"]),
                "delta": role_focus[1],
                "meta": due_label,
                "tone": "info",
            },
            {
                "label": "Critical SLA",
                "value": str(counts["critical"]),
                "delta": role_focus[0],
                "meta": "API role-filtered",
                "tone": "danger" if counts["critical"] else "success",
            },
            {
                "label": "待核准",
                "value": str(counts["approvals"]),
                "delta": "Approval Center",
                "meta": "after writes refreshed",
                "tone": "warning" if counts["approvals"] else "success",
            },
            {
                "label": "可用工作區",
                "value": str(len(role["allowedWorkspaces"])),
                "delta": "role policy",
                "meta": "bootstrap envelope",
                "tone": "accent",
            },
            {
                "label": "通知",
                "value": str(counts["notifications"]),
                "delta": "unread",
                "meta": "header source",
                "tone": "neutral",
            },
            {
                "label": "搜尋索引",
                "value": str(counts["search"]),
                "delta": "entity deep links",
                "meta": "Ctrl/Cmd+K",
                "tone": "success",
            },
        ]

    def _build_search_items(
        self,
        *,
        role: dict[str, Any],
        queue: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        indexed_entity_ids: set[str] = set()
        for row in queue:
            entity_id = str(row["id"])
            indexed_entity_ids.add(entity_id)
            items.append(
                {
                    "id": f"search-{entity_id}",
                    "entityId": entity_id,
                    "label": f"{entity_id} {row['title']}",
                    "description": f"{row['owner']} / {row['meta']}",
                    "keywords": " ".join(
                        [str(row.get("workspace", "")), str(row.get("status", "")), *row.get("tags", [])]
                    ),
                    "target": deepcopy(row["target"]),
                }
            )
        for approval in approvals:
            entity_id = str(approval["id"])
            if entity_id in indexed_entity_ids:
                continue
            items.append(
                {
                    "id": f"search-{entity_id}",
                    "entityId": entity_id,
                    "label": f"{entity_id} {approval['title']}",
                    "description": approval["meta"],
                    "keywords": f"approval govern {approval['status']}",
                    "target": deepcopy(approval["target"]),
                }
            )
        for workspace_id in role["allowedWorkspaces"]:
            workspace = next(item for item in WORKSPACES if item["id"] == workspace_id)
            items.append(
                {
                    "id": f"workspace-{workspace_id}",
                    "entityId": workspace_id,
                    "label": workspace["label"],
                    "description": workspace["description"],
                    "keywords": f"workspace {workspace['shortLabel']}",
                    "target": {
                        "workspace": workspace_id,
                        "entityId": workspace_id,
                        "tab": "overview",
                    },
                }
            )
        return items

    def _scope_label(self, role_id: str) -> str:
        return {
            "ops-lead": "全品牌・12 門市・北北桃",
            "cs-lead": "客服與評論・高風險門市",
            "field-lead": "現場維修・設備 SLA",
            "marketing-manager": "會員分群・活動與定價",
            "expansion-manager": "候選點・HeatZone・SiteScore",
            "pm-audit": "模型稽核・決策追蹤",
        }.get(role_id, "Operator scope")


__all__ = ["OperatorStateService", "ROLES", "WORKSPACES"]
