from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

WorkspaceId = str
OperatorRoleId = str


WORKSPACES: list[dict[str, str]] = [
    {
        "id": "today",
        "label": "今日工作",
        "shortLabel": "Today",
        "description": "Today",
    },
    {
        "id": "store",
        "label": "門市營運",
        "shortLabel": "Store",
        "description": "Store Ops",
    },
    {
        "id": "growth",
        "label": "營收成長",
        "shortLabel": "Growth",
        "description": "Growth",
    },
    {
        "id": "network",
        "label": "展店與店網",
        "shortLabel": "Network",
        "description": "Network",
    },
    {
        "id": "govern",
        "label": "治理稽核",
        "shortLabel": "Govern",
        "description": "Govern",
    },
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
        "id": "pm-audit",
        "label": "PM／稽核",
        "subtitle": "模型、決策追蹤與稽核線索",
        "allowedWorkspaces": ["today", "store", "govern"],
        "heroName": "周子安",
    },
]


SYSTEM_ROLE_TO_OPERATOR_ROLE: dict[str, OperatorRoleId] = {
    "operations_manager": "ops-lead",
    "regional_supervisor": "field-lead",
    "marketing_manager": "marketing-manager",
    "expansion_user": "expansion-manager",
    "site_reviewer": "expansion-manager",
    "auditor": "pm-audit",
}


BASE_QUEUE: list[dict[str, Any]] = [
    {
        "id": "ISS-1024",
        "title": "支付失敗率異常升高",
        "description": "大安復興店 12 分鐘內連續 18 筆失敗，收銀機 A3 需 triage。",
        "meta": "Payment + Google review + ForecastOps 四燈號",
        "owner": "營運",
        "status": "SLA 1h",
        "time": "09:42",
        "tone": "danger",
        "workspace": "store",
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
        "tags": ["支付", "客服", "預測"],
        "target": {"workspace": "store", "entityId": "ISS-1024", "tab": "triage"},
    },
    {
        "id": "ISS-1021",
        "title": "Kiosk offline 影響午尖峰",
        "description": "板橋中山店設備離線 24 分鐘，工務主任可直接指派現場處理。",
        "meta": "IoT device state + CS cases",
        "owner": "工務",
        "status": "New",
        "time": "09:20",
        "tone": "warning",
        "workspace": "store",
        "roles": ["ops-lead", "field-lead", "pm-audit"],
        "tags": ["IoT", "設備"],
        "target": {"workspace": "store", "entityId": "ISS-1021", "tab": "assign"},
    },
    {
        "id": "CS-204",
        "title": "退款與一星評論需要一致回覆",
        "description": "同一名會員跨 Google review 與客服通道進線，需客服主管核對補償語氣。",
        "meta": "ReviewOps + Zendesk merged thread",
        "owner": "客服",
        "status": "Reply due",
        "time": "09:08",
        "tone": "danger",
        "workspace": "store",
        "roles": ["cs-lead", "ops-lead"],
        "tags": ["評論", "客服"],
        "target": {"workspace": "store", "entityId": "ISS-1017", "tab": "reply"},
    },
    {
        "id": "GRW-201",
        "title": "夜間會員回流活動建議",
        "description": "忠孝商圈夜間需求未滿足，建議 20:00-23:00 定向券。",
        "meta": "Segment fit 84 / conflict clear",
        "owner": "行銷",
        "status": "Draft",
        "time": "08:55",
        "tone": "success",
        "workspace": "growth",
        "roles": ["ops-lead", "marketing-manager"],
        "tags": ["成長", "活動"],
        "target": {"workspace": "growth", "entityId": "GRW-201", "tab": "campaign"},
    },
    {
        "id": "APR-501",
        "title": "CS-1002 SiteScore WAIT",
        "description": "候選點信心 76，需要營運主管判定是否進入複審。",
        "meta": "Model SiteScore v2.3 / snapshot FS-20260703-0600",
        "owner": "展店",
        "status": "Review",
        "time": "08:30",
        "tone": "info",
        "workspace": "govern",
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
        "tags": ["SiteScore", "核准"],
        "target": {"workspace": "govern", "entityId": "APR-501", "tab": "approvals"},
    },
    {
        "id": "RV-701",
        "title": "物件看板照片缺漏",
        "description": "Listing Radar 已完成去重，仍缺路口可視性佐證。",
        "meta": "Source compliance checked",
        "owner": "展店",
        "status": "Need data",
        "time": "08:18",
        "tone": "warning",
        "workspace": "network",
        "roles": ["ops-lead", "expansion-manager"],
        "tags": ["Listing", "佐證"],
        "target": {"workspace": "network", "entityId": "RV-701", "tab": "review"},
    },
    {
        "id": "NET-305",
        "title": "低效門市重配建議",
        "description": "西門小南門店進入 AVM request，NetPlan 三方案待比較。",
        "meta": "Rent pressure + cannibalization risk",
        "owner": "PM",
        "status": "Observe",
        "time": "07:54",
        "tone": "accent",
        "workspace": "network",
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
        "tags": ["NetPlan", "AVM"],
        "target": {"workspace": "network", "entityId": "NET-305", "tab": "rebalance"},
    },
    {
        "id": "AUD-044",
        "title": "模型快照與決策理由抽查",
        "description": "PM／稽核需抽查三筆高風險建議是否具備 prediction_run_id 與理由。",
        "meta": "Audit trail completeness",
        "owner": "PM／稽核",
        "status": "Audit",
        "time": "07:40",
        "tone": "info",
        "workspace": "govern",
        "roles": ["pm-audit", "ops-lead"],
        "tags": ["稽核", "模型"],
        "target": {"workspace": "govern", "entityId": "AUD-044", "tab": "audit"},
    },
]


BASE_APPROVALS: list[dict[str, Any]] = [
    {
        "id": "APR-501",
        "title": "SiteScore 複審",
        "meta": "CS-1002 WAIT 76，租金合理但競品密度偏高。",
        "status": "2h SLA",
        "state": "pending",
        "cta": "Open Govern",
        "tone": "warning",
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
        "target": {"workspace": "govern", "entityId": "APR-501", "tab": "approvals"},
    },
    {
        "id": "APR-487",
        "title": "Google review 回覆",
        "meta": "負評涉及付款失敗，客服主管已補充草稿。",
        "status": "Needs reason",
        "state": "pending",
        "cta": "Review",
        "tone": "danger",
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
        "target": {"workspace": "govern", "entityId": "APR-487", "tab": "approvals"},
    },
    {
        "id": "GRW-207",
        "title": "PriceOps 折扣上限",
        "meta": "模型建議 8%，需確認毛利保護線。",
        "status": "Policy",
        "state": "pending",
        "cta": "Compare",
        "tone": "info",
        "roles": ["ops-lead", "marketing-manager"],
        "target": {"workspace": "govern", "entityId": "GRW-207", "tab": "approvals"},
    },
    {
        "id": "APR-732",
        "title": "工務外包加急派工",
        "meta": "Kiosk offline 超過 20 分鐘，需主管核准加急費用。",
        "status": "Field SLA",
        "state": "pending",
        "cta": "Approve",
        "tone": "warning",
        "roles": ["ops-lead", "field-lead"],
        "target": {"workspace": "govern", "entityId": "APR-732", "tab": "approvals"},
    },
]


BASE_RISK_ROWS: list[dict[str, Any]] = [
    {
        "label": "大安復興店",
        "score": 92,
        "signal": "Payment failure + queue spike",
        "tone": "danger",
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
    },
    {
        "label": "板橋中山店",
        "score": 78,
        "signal": "Kiosk offline + CS wait",
        "tone": "warning",
        "roles": ["ops-lead", "field-lead", "pm-audit"],
    },
    {
        "label": "忠孝敦化店",
        "score": 64,
        "signal": "Demand gap with staff buffer",
        "tone": "accent",
        "roles": ["ops-lead", "marketing-manager"],
    },
    {
        "label": "台北車站店",
        "score": 38,
        "signal": "Recovered after remote restart",
        "tone": "success",
        "roles": ["ops-lead", "field-lead"],
    },
    {
        "label": "板橋府中候選點",
        "score": 76,
        "signal": "SiteScore WAIT + evidence gap",
        "tone": "info",
        "roles": ["expansion-manager", "pm-audit", "ops-lead"],
    },
]


BASE_NOTIFICATIONS: list[dict[str, Any]] = [
    {
        "id": "NTF-SLA-1024",
        "title": "SLA 即將到期",
        "detail": "ISS-1024 需在 58 分鐘內完成 Triage。",
        "tone": "danger",
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
        "target": {"workspace": "store", "entityId": "ISS-1024", "tab": "triage"},
    },
    {
        "id": "NTF-APR-501",
        "title": "核准中心新增",
        "detail": "SiteScore APR-501 已送出複審。",
        "tone": "warning",
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
        "target": {"workspace": "govern", "entityId": "APR-501", "tab": "approvals"},
    },
    {
        "id": "NTF-MODEL-0600",
        "title": "模型快照更新",
        "detail": "ForecastOps v2.6 完成 06:00 refresh。",
        "tone": "info",
        "roles": ["ops-lead", "marketing-manager", "expansion-manager", "pm-audit"],
        "target": {"workspace": "govern", "entityId": "AUD-044", "tab": "audit"},
    },
    {
        "id": "NTF-FIELD-1021",
        "title": "設備派工待回報",
        "detail": "ISS-1021 現場處理需在午尖峰前回報。",
        "tone": "warning",
        "roles": ["field-lead", "ops-lead"],
        "target": {"workspace": "store", "entityId": "ISS-1021", "tab": "field-report"},
    },
]


BASE_AUDIT_FEED: list[dict[str, Any]] = [
    {
        "actor": "system / ForecastOps",
        "category": "Model snapshot",
        "detail": "Updated four-light evidence for ISS-1024 with payment confidence 0.91.",
        "time": "09:46",
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
    },
    {
        "actor": "客服主管",
        "category": "Decision log",
        "detail": "Returned APR-487 reply draft for clearer compensation reason.",
        "time": "09:33",
        "roles": ["ops-lead", "cs-lead", "pm-audit"],
    },
    {
        "actor": "展店經理",
        "category": "Network review",
        "detail": "Marked RV-701 as pending street-front visibility evidence.",
        "time": "09:12",
        "roles": ["ops-lead", "expansion-manager", "pm-audit"],
    },
    {
        "actor": "PM／稽核",
        "category": "Audit trail",
        "detail": "Exported approval packet for CS-1002 SiteScore comparison.",
        "time": "08:41",
        "roles": ["ops-lead", "pm-audit"],
    },
    {
        "actor": "工務主任",
        "category": "Field workflow",
        "detail": "Assigned Kiosk offline check to onsite contractor.",
        "time": "08:19",
        "roles": ["field-lead", "ops-lead"],
    },
]


def _strip_roles(item: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(item)
    clean.pop("roles", None)
    return clean


def _has_role(item: dict[str, Any], role_id: OperatorRoleId) -> bool:
    roles = item.get("roles")
    return not roles or role_id in roles


def _now_hhmm() -> str:
    return datetime.now(UTC).strftime("%H:%M")


class OperatorShellService:
    """Stateful application service for the productized Operator shell.

    The current implementation is intentionally in-memory, matching the rest of
    the R4 productization surface, but every response is shaped as a durable API
    envelope so the web layer does not derive counts or deep links from local
    fixtures.
    """

    def __init__(self) -> None:
        self._queue = deepcopy(BASE_QUEUE)
        self._approvals = deepcopy(BASE_APPROVALS)
        self._notifications = deepcopy(BASE_NOTIFICATIONS)
        self._audit_feed = deepcopy(BASE_AUDIT_FEED)

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

    def bootstrap(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        role = self.resolve_role(
            operator_role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
        )
        return self._build_envelope(role=role, correlation_id=correlation_id)

    def search(
        self,
        query: str,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        envelope = self.bootstrap(
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
                if normalized in " ".join(
                    [
                        item.get("id", ""),
                        item.get("label", ""),
                        item.get("description", ""),
                        item.get("keywords", ""),
                    ]
                ).casefold()
            ]
        return {
            "meta": envelope["meta"],
            "items": items,
            "count": len(items),
        }

    def decide_approval(
        self,
        approval_id: str,
        *,
        status: str,
        reason: str | None = None,
        actor_name: str | None = None,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = status.strip().lower()
        if normalized not in {"approved", "rejected", "returned"}:
            normalized = "returned"

        for approval in self._approvals:
            if approval["id"] == approval_id:
                approval["state"] = normalized
                approval["status"] = normalized.title()
                break
        else:
            self._approvals.append(
                {
                    "id": approval_id,
                    "title": approval_id,
                    "meta": reason or "Decision recorded from Operator shell.",
                    "status": normalized.title(),
                    "state": normalized,
                    "cta": "Open",
                    "tone": "neutral",
                    "roles": [role_id or "ops-lead"],
                    "target": {
                        "workspace": "govern",
                        "entityId": approval_id,
                        "tab": "approvals",
                    },
                }
            )

        self._audit_feed.insert(
            0,
            {
                "actor": actor_name or "Operator",
                "category": "Decision log",
                "detail": f"Approval {approval_id} decided: {normalized}. Reason: {reason or 'n/a'}",
                "time": _now_hhmm(),
                "roles": ["ops-lead", "cs-lead", "field-lead", "marketing-manager", "expansion-manager", "pm-audit"],
            },
        )
        self._notifications = [
            notification
            for notification in self._notifications
            if notification.get("target", {}).get("entityId") != approval_id
        ]
        return self.bootstrap(
            role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
            correlation_id=correlation_id,
        )

    def transition_issue(
        self,
        issue_id: str,
        *,
        action_type: str,
        note: str | None = None,
        actor_name: str | None = None,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        status_by_action = {
            "triage": "Triaged",
            "assign": "Assigned",
            "actions": "In Progress",
            "field-report": "Field Reported",
            "outcome": "Closed",
            "escalate": "Escalated",
        }
        for item in self._queue:
            if item["id"] == issue_id:
                item["status"] = status_by_action.get(action_type, "Updated")
                item["time"] = _now_hhmm()
                break

        self._audit_feed.insert(
            0,
            {
                "actor": actor_name or "Operator",
                "category": "Workflow",
                "detail": f"Issue {issue_id} transition via {action_type}. {note or ''}".strip(),
                "time": _now_hhmm(),
                "roles": ["ops-lead", "cs-lead", "field-lead", "pm-audit"],
            },
        )
        return self.bootstrap(
            role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
            correlation_id=correlation_id,
        )

    def confirm_evidence_purpose(
        self,
        evidence_id: str,
        *,
        purpose: str,
        actor_name: str | None = None,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        self._audit_feed.insert(
            0,
            {
                "actor": actor_name or "Operator",
                "category": "Audit trail",
                "detail": f"Unlocked evidence {evidence_id} with purpose: {purpose}",
                "time": _now_hhmm(),
                "roles": ["ops-lead", "cs-lead", "field-lead", "pm-audit"],
            },
        )
        return self.bootstrap(
            role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
            correlation_id=correlation_id,
        )

    def _build_envelope(self, *, role: dict[str, Any], correlation_id: str | None) -> dict[str, Any]:
        role_id = role["id"]
        queue = [_strip_roles(item) for item in self._queue if _has_role(item, role_id)]
        approvals = [
            _strip_roles(item)
            for item in self._approvals
            if _has_role(item, role_id) and item.get("state") in {"pending", "returned"}
        ]
        risk_rows = [_strip_roles(item) for item in BASE_RISK_ROWS if _has_role(item, role_id)]
        notifications = [
            _strip_roles(item) for item in self._notifications if _has_role(item, role_id)
        ]
        audit_feed = [_strip_roles(item) for item in self._audit_feed if _has_role(item, role_id)][:6]
        search_items = self._build_search_items(role=role, queue=queue, approvals=approvals)
        counts = {
            "notifications": len(notifications),
            "approvals": len(approvals),
            "taskCenter": len(queue),
            "critical": sum(1 for item in queue if item.get("tone") == "danger"),
            "search": len(search_items),
        }
        generated_at = datetime.now(UTC).isoformat()
        kpis = self._build_kpis(role=role, counts=counts, queue=queue)

        envelope = {
            "meta": {
                "generatedAt": generated_at,
                "correlationId": correlation_id,
                "role": role,
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
                "taskCenter": {
                    "label": "Task Center",
                    "count": counts["taskCenter"],
                },
            },
            "today": {
                "hero": {
                    "name": role["heroName"],
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

    def _build_kpis(
        self,
        *,
        role: dict[str, Any],
        counts: dict[str, int],
        queue: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        workspace_count = len(role["allowedWorkspaces"])
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
                "delta": f"{role_focus[1]}",
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
                "value": str(workspace_count),
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
        for row in queue:
            items.append(
                {
                    "id": f"search-{row['id']}",
                    "entityId": row["id"],
                    "label": f"{row['id']} {row['title']}",
                    "description": f"{row['owner']} / {row['meta']}",
                    "keywords": " ".join([row["workspace"], row["status"], *row.get("tags", [])]),
                    "target": deepcopy(row["target"]),
                }
            )
        for approval in approvals:
            if approval["id"] not in {item["entityId"] for item in items}:
                items.append(
                    {
                        "id": f"search-{approval['id']}",
                        "entityId": approval["id"],
                        "label": f"{approval['id']} {approval['title']}",
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
                    "target": {"workspace": workspace_id, "entityId": workspace_id, "tab": "overview"},
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


__all__ = [
    "OperatorShellService",
    "ROLES",
    "WORKSPACES",
]
