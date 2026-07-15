"""Product shell application service (ODP-PGAP-SHELL-001).

Owns: the durable cross-module shell state that the product shell needs but no
single domain module owns —

- task centre assignment + SLA state
- notification inbox state (severity, acknowledgement) and delivery preferences
- role/workspace administration grants
- workspace settings
- franchisee acknowledgement + field reports

Not changing: OperatorStateService's seeded read model (this service composes
with it rather than re-implementing it), auth/RBAC policy, other opsboard
modules.

Composes with: create_shell_sub_router() in
apps/api/app/routes/operator_modules/shell.py.

Durability follows the ODP-OC-R5-011 assisted-intake pattern: the application
layer depends only on the ShellRepository Protocol, an in-memory implementation
lives here, and the SqliteDocumentStore-backed implementation lives in
shared/infrastructure/persistence/operator_shell.py.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from modules.opsboard.application.operator_state import (
    ROLES,
    WORKSPACES,
    OperatorStateService,
)

# ----------------------------------------------------------------------
# Collections
# ----------------------------------------------------------------------

TASK_ASSIGNMENTS = "operator.shell_task_assignments"
NOTIFICATION_STATES = "operator.shell_notification_states"
NOTIFICATION_PREFERENCES = "operator.shell_notification_preferences"
ADMIN_GRANTS = "operator.shell_admin_grants"
SETTINGS = "operator.shell_settings"
FRANCHISEE_ACKS = "operator.shell_franchisee_acks"
FRANCHISEE_REPORTS = "operator.shell_franchisee_reports"

SHELL_COLLECTIONS = (
    TASK_ASSIGNMENTS,
    NOTIFICATION_STATES,
    NOTIFICATION_PREFERENCES,
    ADMIN_GRANTS,
    SETTINGS,
    FRANCHISEE_ACKS,
    FRANCHISEE_REPORTS,
)


# ----------------------------------------------------------------------
# Repository contract
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ShellIdempotencyRecord:
    """A replayable write response keyed by ``(action, key)``.

    Keyed by action as well as key so the same client-minted key cannot
    collide across two different shell write endpoints.
    """

    action: str
    key: str
    response: dict[str, Any]


class ShellRepository(Protocol):
    """Public persistence contract for durable product-shell state.

    The service depends on this contract only, so a durable implementation can
    be substituted without the application layer reaching into a document store
    or any other backing detail.
    """

    def list_records(self, collection: str) -> list[dict[str, Any]]: ...

    def save_record(self, collection: str, doc_id: str, record: dict[str, Any]) -> None: ...

    def list_idempotency_records(self) -> list[ShellIdempotencyRecord]: ...

    def save_idempotency_record(self, record: ShellIdempotencyRecord) -> None: ...

    def clear(self) -> None: ...


@dataclass
class InMemoryShellRepository:
    """Non-durable ShellRepository used when no document store is wired."""

    _records: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    _idempotency: dict[tuple[str, str], ShellIdempotencyRecord] = field(default_factory=dict)

    def list_records(self, collection: str) -> list[dict[str, Any]]:
        return [deepcopy(value) for value in self._records.get(collection, {}).values()]

    def save_record(self, collection: str, doc_id: str, record: dict[str, Any]) -> None:
        self._records.setdefault(collection, {})[doc_id] = deepcopy(record)

    def list_idempotency_records(self) -> list[ShellIdempotencyRecord]:
        return [
            ShellIdempotencyRecord(
                action=record.action, key=record.key, response=deepcopy(record.response)
            )
            for record in self._idempotency.values()
        ]

    def save_idempotency_record(self, record: ShellIdempotencyRecord) -> None:
        self._idempotency[(record.action, record.key)] = ShellIdempotencyRecord(
            action=record.action, key=record.key, response=deepcopy(record.response)
        )

    def clear(self) -> None:
        self._records.clear()
        self._idempotency.clear()


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


class ShellNotFound(Exception):
    """Raised when a shell entity id does not resolve."""


class ShellConflict(Exception):
    """Raised when a shell write conflicts with current state."""


class ShellPolicyError(Exception):
    """Raised when a shell write violates a governance precondition."""


class ShellForbidden(Exception):
    """Raised when the acting role may not perform a shell action.

    Distinct from ShellPolicyError so the transport layer can answer 403
    (authorization) rather than 422 (malformed request). RBAC at the route
    still guards the coarse resource; this is the product-rule layer on top.
    """


# ----------------------------------------------------------------------
# Static policy tables
# ----------------------------------------------------------------------

ALL_ROLE_IDS: list[str] = [str(role["id"]) for role in ROLES]

#: Severity ladder. Ordered from most to least urgent; the inbox sorts on this
#: and preferences declare a floor below which a channel stays silent.
SEVERITY_ORDER: list[str] = ["critical", "warning", "info"]

#: Tone (the seed's display vocabulary) → severity (the inbox's vocabulary).
TONE_SEVERITY: dict[str, str] = {
    "danger": "critical",
    "warning": "warning",
    "info": "info",
    "success": "info",
    "neutral": "info",
    "accent": "info",
}

#: Roles allowed to administer roles/workspaces and platform settings. Kept in
#: the application layer because it is a product rule; the transport layer
#: additionally enforces RBAC (operator_console UPDATE) fail-closed.
ADMIN_ROLE_IDS: frozenset[str] = frozenset({"ops-lead"})

#: Default delivery preferences applied to a user who has never saved any.
DEFAULT_PREFERENCES: dict[str, Any] = {
    "channels": {"inApp": True, "email": True, "push": False},
    "severityFloor": "info",
    "digest": "immediate",
}

#: The shell entry points, with the capability each one needs. Home renders
#: only the entries whose workspace the active role is granted.
ENTRY_POINTS: list[dict[str, Any]] = [
    {
        "key": "tasks",
        "label": "Task Center",
        "href": "/tasks",
        "workspace": "today",
        "description": "待處理決策任務、指派與 SLA",
    },
    {
        "key": "notifications",
        "label": "通知收件匣",
        "href": "/notifications",
        "workspace": "today",
        "description": "嚴重度分級、確認與來源連結",
    },
    {
        "key": "search",
        "label": "全域搜尋",
        "href": "/search",
        "workspace": "today",
        "description": "跨模組實體搜尋與鍵盤導覽",
    },
    {
        "key": "store",
        "label": "門市營運",
        "href": "/operations",
        "workspace": "store",
        "description": "門市案件、設備與現場回報",
    },
    {
        "key": "growth",
        "label": "營收成長",
        "href": "/pricing",
        "workspace": "growth",
        "description": "定價、活動與會員成長",
    },
    {
        "key": "network",
        "label": "展店與店網",
        "href": "/expansion",
        "workspace": "network",
        "description": "HeatZone、候選點與 SiteScore",
    },
    {
        "key": "govern",
        "label": "治理稽核",
        "href": "/audit",
        "workspace": "govern",
        "description": "決策追蹤、模型與稽核線索",
    },
    {
        "key": "admin",
        "label": "平台管理",
        "href": "/admin",
        "workspace": "govern",
        "description": "角色、工作區與平台設定",
        "requiresAdmin": True,
    },
]

#: Task actions and the capability each requires. The FE greys out actions the
#: role lacks; the API still re-checks on write (fail-closed).
TASK_ACTIONS: list[dict[str, str]] = [
    {"key": "task.assign", "label": "指派", "requiredRole": "ops-lead"},
    {"key": "task.open", "label": "開啟來源", "requiredRole": ""},
]

#: The only workspace a franchisee is scoped to. Anything targeting govern /
#: growth / network is operator-only.
FRANCHISEE_WORKSPACE = "store"

#: The only task fields a franchisee may see. Enforced by projection
#: (allow-list), not by deletion, so a new seed field cannot silently leak —
#: notably `owner`, `meta` and `description` carry operator-internal detail.
FRANCHISEE_TASK_FIELDS: tuple[str, ...] = ("id", "title", "status", "time")

FRANCHISEE_REPORT_CATEGORIES: frozenset[str] = frozenset(
    {"equipment", "staffing", "supply", "customer", "other"}
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(value)


def _severity_rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return len(SEVERITY_ORDER)


def _sla_state(due_at: str | None, *, now: datetime) -> str:
    """Classify an SLA due timestamp into breached / at-risk / on-track."""
    if not due_at:
        return "none"
    try:
        due = datetime.fromisoformat(due_at)
    except ValueError:
        return "none"
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    if due <= now:
        return "breached"
    if due - now <= timedelta(hours=4):
        return "at-risk"
    return "on-track"


class ShellService:
    """Application service for the product shell.

    Read paths compose OperatorStateService's role-filtered seed envelope with
    the durable overlay owned here. Write paths are idempotent, audited, and
    persist through the ShellRepository contract.
    """

    def __init__(
        self,
        state_service: OperatorStateService | None = None,
        *,
        repository: ShellRepository | None = None,
    ) -> None:
        self._state = state_service or OperatorStateService()
        self._repo: ShellRepository = (
            repository if repository is not None else InMemoryShellRepository()
        )
        self._idempotency_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._audit_feed: list[dict[str, Any]] = []
        self._load_idempotency_cache()

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    def _load_idempotency_cache(self) -> None:
        self._idempotency_cache = {
            (record.action, record.key): record.response
            for record in self._repo.list_idempotency_records()
        }

    def _replay(self, action: str, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        cached = self._idempotency_cache.get((action, key))
        if cached is None:
            return None
        replayed = _copy(cached)
        replayed["idempotentReplay"] = True
        return replayed

    def _remember(self, action: str, key: str | None, response: dict[str, Any]) -> None:
        if not key:
            return
        self._idempotency_cache[(action, key)] = _copy(response)
        self._repo.save_idempotency_record(
            ShellIdempotencyRecord(action=action, key=key, response=_copy(response))
        )

    def _records_by_id(self, collection: str, id_field: str) -> dict[str, dict[str, Any]]:
        return {
            str(record[id_field]): record
            for record in self._repo.list_records(collection)
            if id_field in record
        }

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def _append_audit(
        self,
        *,
        category: str,
        action: str,
        actor_role_id: str,
        actor_subject_id: str | None,
        target_type: str,
        target_id: str,
        message: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "id": f"AUD-SHELL-{len(self._audit_feed) + 1:04d}",
            "auditEventId": str(uuid4()),
            "occurredAt": _now_iso(),
            "actorRoleId": actor_role_id,
            "actorSubjectId": actor_subject_id,
            "category": category,
            "action": action,
            "targetType": target_type,
            "targetId": target_id,
            "message": message,
            "metadata": _copy(metadata),
        }
        self._audit_feed.insert(0, event)
        return _copy(event)

    def list_audit_feed(self) -> list[dict[str, Any]]:
        """Return the shell's own domain audit feed (newest first)."""
        return [_copy(event) for event in self._audit_feed]

    # ------------------------------------------------------------------
    # Role resolution
    # ------------------------------------------------------------------

    def _role(
        self,
        *,
        role_id: str | None,
        subject_id: str | None,
        system_roles: str | None,
    ) -> dict[str, Any]:
        return self._state.resolve_role(
            operator_role_id=role_id,
            subject_id=subject_id,
            system_roles=system_roles,
        )

    def _allowed_workspaces(self, role: dict[str, Any]) -> list[str]:
        """Return the role's workspaces, honouring any durable admin override."""
        grants = self._records_by_id(ADMIN_GRANTS, "roleId")
        override = grants.get(str(role["id"]))
        if override and isinstance(override.get("allowedWorkspaces"), list):
            return [str(item) for item in override["allowedWorkspaces"]]
        return [str(item) for item in role["allowedWorkspaces"]]

    def _is_admin(self, role: dict[str, Any]) -> bool:
        return str(role["id"]) in ADMIN_ROLE_IDS

    def _actor_key(self, *, subject_id: str | None, role: dict[str, Any]) -> str:
        """Identify the *person* a piece of personal state belongs to.

        Notification acknowledgement, delivery preferences and settings are
        personal, not role-wide: if one ops-lead acknowledges a critical SLA
        alert it must not vanish from another ops-lead's inbox, and one
        operator's digest choice must not silently rewrite a colleague's.
        Role stays the filter for *which* rows you can see; this decides whose
        state is being read or written.

        Falls back to the role id only when no subject is available (the legacy
        header-trust path), which keeps behaviour defined rather than crashing.
        """
        return (subject_id or "").strip() or str(role["id"])

    # ------------------------------------------------------------------
    # Home
    # ------------------------------------------------------------------

    def get_home(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the aggregated, role-aware first screen.

        Aggregates live status, tasks, approvals, decisions, data freshness and
        the entry points the role may actually reach.
        """
        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        envelope = self._state.get_today(
            role_id=role["id"],
            subject_id=subject_id,
            system_roles=system_roles,
            correlation_id=correlation_id,
        )
        allowed = self._allowed_workspaces(role)
        tasks = self._tasks_for(role=role, subject_id=subject_id)
        notifications = self._notifications_for(role=role, subject_id=subject_id)
        approvals = envelope["approvals"]
        unacknowledged = [item for item in notifications if not item["acknowledged"]]
        breached = [item for item in tasks if item["slaState"] == "breached"]
        at_risk = [item for item in tasks if item["slaState"] == "at-risk"]

        entry_points = [
            {k: v for k, v in entry.items() if k != "requiresAdmin"}
            for entry in ENTRY_POINTS
            if entry["workspace"] in allowed
            and (not entry.get("requiresAdmin") or self._is_admin(role))
        ]

        generated_at = envelope["meta"]["generatedAt"]
        return {
            "meta": {
                **envelope["meta"],
                "source": "operator-shell-home",
                "allowedWorkspaces": allowed,
                "isAdmin": self._is_admin(role),
            },
            "status": {
                "headline": f"{role['label']}・{len(tasks)} 件待處理",
                "openTasks": len(tasks),
                "slaBreached": len(breached),
                "slaAtRisk": len(at_risk),
                "pendingApprovals": len(approvals),
                "unacknowledgedNotifications": len(unacknowledged),
                "tone": "danger" if breached else ("warning" if at_risk else "success"),
            },
            "tasks": tasks[:5],
            "approvals": approvals[:5],
            "decisions": envelope["today"]["decisions"][:5],
            "freshness": self._freshness(generated_at=generated_at, envelope=envelope),
            "entryPoints": entry_points,
            "notifications": notifications[:5],
            "kpis": envelope["kpis"],
        }

    def _freshness(self, *, generated_at: str, envelope: dict[str, Any]) -> list[dict[str, Any]]:
        """Report per-source freshness so the first screen never implies data is
        newer than its slowest upstream."""
        counts = envelope["meta"]["counts"]
        return [
            {
                "source": "operator-state",
                "label": "營運隊列與核准",
                "generatedAt": generated_at,
                "records": counts["taskCenter"] + counts["approvals"],
                "state": "live",
            },
            {
                "source": "shell-overlay",
                "label": "指派、通知與設定",
                "generatedAt": _now_iso(),
                "records": len(self._repo.list_records(TASK_ASSIGNMENTS))
                + len(self._repo.list_records(NOTIFICATION_STATES)),
                "state": "live",
            },
        ]

    # ------------------------------------------------------------------
    # Task Center
    # ------------------------------------------------------------------

    def _tasks_for(
        self,
        *,
        role: dict[str, Any],
        subject_id: str | None,
    ) -> list[dict[str, Any]]:
        """Return the role's queue overlaid with durable assignment + SLA state."""
        queue = self._state.get_work_queue(role_id=role["id"])
        assignments = self._records_by_id(TASK_ASSIGNMENTS, "taskId")
        now = datetime.now(UTC)
        allowed = self._allowed_workspaces(role)
        tasks: list[dict[str, Any]] = []
        for item in queue:
            if str(item.get("workspace", "today")) not in allowed:
                continue
            task_id = str(item["id"])
            assignment = assignments.get(task_id, {})
            due_at = assignment.get("slaDueAt")
            assignee_id = assignment.get("assigneeId")
            tasks.append(
                {
                    **item,
                    "taskId": task_id,
                    "assigneeId": assignee_id,
                    "assigneeName": assignment.get("assigneeName"),
                    "assignedAt": assignment.get("updatedAt"),
                    "assignedToMe": bool(assignee_id) and assignee_id == subject_id,
                    "slaDueAt": due_at,
                    "slaState": _sla_state(due_at, now=now),
                    "severity": TONE_SEVERITY.get(str(item.get("tone")), "info"),
                    "deepLink": self._deep_link(item),
                    "sourceHref": self._source_href(item),
                }
            )
        tasks.sort(
            key=lambda item: (
                _severity_rank(item["severity"]),
                {"breached": 0, "at-risk": 1, "on-track": 2, "none": 3}[item["slaState"]],
                item["taskId"],
            )
        )
        return tasks

    def _deep_link(self, item: dict[str, Any]) -> dict[str, Any]:
        target = item.get("target") or {}
        return {
            "workspace": target.get("workspace", item.get("workspace", "today")),
            "entityId": target.get("entityId", item.get("id")),
            "tab": target.get("tab", "overview"),
        }

    def _source_href(self, item: dict[str, Any]) -> str:
        """Stable in-app URL for a task, so a deep link survives a reload."""
        link = self._deep_link(item)
        return f"/tasks?taskId={link['entityId']}&workspace={link['workspace']}"

    def _task_actions(self, *, role: dict[str, Any]) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for action in TASK_ACTIONS:
            required = action["requiredRole"]
            allowed = not required or str(role["id"]) == required
            actions.append(
                {
                    "key": action["key"],
                    "label": action["label"],
                    "allowed": allowed,
                    "reason": None if allowed else f"需要 {required} 角色才能執行此動作。",
                }
            )
        return actions

    def get_tasks(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
        sla: str | None = None,
        assignee: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the Task Center list with filters, facets and deep links."""
        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        tasks = self._tasks_for(role=role, subject_id=subject_id)

        facets = {
            "sla": {
                state: sum(1 for item in tasks if item["slaState"] == state)
                for state in ("breached", "at-risk", "on-track", "none")
            },
            "status": {},
            "assignee": {"me": sum(1 for item in tasks if item["assignedToMe"])},
        }
        for item in tasks:
            key = str(item.get("status", "unknown"))
            facets["status"][key] = facets["status"].get(key, 0) + 1

        filtered = tasks
        if sla:
            filtered = [item for item in filtered if item["slaState"] == sla]
        if assignee == "me":
            filtered = [item for item in filtered if item["assignedToMe"]]
        elif assignee == "unassigned":
            filtered = [item for item in filtered if not item["assigneeId"]]
        elif assignee:
            filtered = [item for item in filtered if item["assigneeId"] == assignee]
        if status:
            filtered = [item for item in filtered if str(item.get("status")) == status]
        if task_id:
            filtered = [item for item in filtered if item["taskId"] == task_id]

        return {
            "meta": {
                "generatedAt": _now_iso(),
                "correlationId": correlation_id,
                "role": {"id": role["id"], "label": role["label"]},
                "source": "operator-shell-tasks",
                "filters": {
                    "sla": sla,
                    "assignee": assignee,
                    "status": status,
                    "taskId": task_id,
                },
            },
            "items": filtered,
            "count": len(filtered),
            "total": len(tasks),
            "facets": facets,
            "actions": self._task_actions(role=role),
            "assignableRoles": [
                {"id": str(item["id"]), "label": str(item["label"])} for item in ROLES
            ],
        }

    def assign_task(
        self,
        *,
        task_id: str,
        assignee_id: str,
        assignee_name: str | None = None,
        sla_due_at: str | None = None,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Durably assign a task. Governed, audited, idempotent."""
        replay = self._replay("assign_task", idempotency_key)
        if replay is not None:
            return replay

        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        if not self._is_admin(role):
            raise ShellForbidden("目前角色無法指派任務；需要營運主管權限。")
        if not (assignee_id or "").strip():
            raise ShellPolicyError("assignee id is required to assign a task")

        known = {str(item["id"]) for item in self._state.get_work_queue(role_id=role["id"])}
        if task_id not in known:
            raise ShellNotFound(f"task {task_id} not found")

        if sla_due_at:
            try:
                datetime.fromisoformat(sla_due_at)
            except ValueError as exc:
                raise ShellPolicyError("sla due date must be an ISO-8601 timestamp") from exc

        record = {
            "taskId": task_id,
            "assigneeId": assignee_id,
            "assigneeName": assignee_name or assignee_id,
            "slaDueAt": sla_due_at,
            "updatedAt": _now_iso(),
            "updatedBy": subject_id or role["id"],
            "correlationId": correlation_id,
        }
        self._repo.save_record(TASK_ASSIGNMENTS, task_id, record)

        audit = self._append_audit(
            category="shell.task",
            action="assign",
            actor_role_id=str(role["id"]),
            actor_subject_id=subject_id,
            target_type="task",
            target_id=task_id,
            message=f"{role['label']} 將 {task_id} 指派給 {record['assigneeName']}",
            metadata={
                "taskId": task_id,
                "assigneeId": assignee_id,
                "slaDueAt": sla_due_at,
                "idempotencyKey": idempotency_key,
                "correlationId": correlation_id,
            },
        )

        response = {
            "assignment": record,
            "auditEvent": audit,
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        self._remember("assign_task", idempotency_key, response)
        return response

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _notifications_for(
        self,
        *,
        role: dict[str, Any],
        subject_id: str | None,
    ) -> list[dict[str, Any]]:
        envelope = self._state.get_today(role_id=role["id"], subject_id=subject_id)
        states = self._records_by_id(NOTIFICATION_STATES, "stateId")
        actor = self._actor_key(subject_id=subject_id, role=role)
        rows: list[dict[str, Any]] = []
        for item in envelope["notifications"]:
            notification_id = str(item.get("id") or item.get("title"))
            state_id = f"{actor}:{notification_id}"
            state = states.get(state_id, {})
            severity = TONE_SEVERITY.get(str(item.get("tone")), "info")
            rows.append(
                {
                    **item,
                    "notificationId": notification_id,
                    "severity": severity,
                    "acknowledged": bool(state.get("acknowledged")),
                    "acknowledgedAt": state.get("acknowledgedAt"),
                    "acknowledgedBy": state.get("acknowledgedBy"),
                    "sourceHref": self._notification_source_href(item),
                }
            )
        rows.sort(key=lambda item: (item["acknowledged"], _severity_rank(item["severity"])))
        return rows

    def _notification_source_href(self, item: dict[str, Any]) -> str:
        target = item.get("target") or {}
        entity_id = target.get("entityId")
        if entity_id:
            return f"/tasks?taskId={entity_id}&workspace={target.get('workspace', 'today')}"
        return "/notifications"

    def get_notifications(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
        severity: str | None = None,
        acknowledged: bool | None = None,
    ) -> dict[str, Any]:
        """Return the durable, role-filtered notification inbox."""
        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        rows = self._notifications_for(role=role, subject_id=subject_id)
        filtered = rows
        if severity:
            filtered = [item for item in filtered if item["severity"] == severity]
        if acknowledged is not None:
            filtered = [item for item in filtered if item["acknowledged"] is acknowledged]
        return {
            "meta": {
                "generatedAt": _now_iso(),
                "correlationId": correlation_id,
                "role": {"id": role["id"], "label": role["label"]},
                "source": "operator-shell-notifications",
            },
            "items": filtered,
            "count": len(filtered),
            "unacknowledged": sum(1 for item in rows if not item["acknowledged"]),
            "facets": {
                "severity": {
                    level: sum(1 for item in rows if item["severity"] == level)
                    for level in SEVERITY_ORDER
                }
            },
            # subject_id must be threaded through: preferences are personal, so
            # resolving them by role alone would show one operator another's.
            "preferences": self.get_notification_preferences(
                role_id=role["id"], subject_id=subject_id
            )["preferences"],
        }

    def acknowledge_notification(
        self,
        *,
        notification_id: str,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Durably acknowledge one notification for the acting user."""
        replay = self._replay("acknowledge_notification", idempotency_key)
        if replay is not None:
            return replay

        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        rows = {
            item["notificationId"]: item
            for item in self._notifications_for(role=role, subject_id=subject_id)
        }
        if notification_id not in rows:
            raise ShellNotFound(f"notification {notification_id} not found")

        actor = self._actor_key(subject_id=subject_id, role=role)
        state_id = f"{actor}:{notification_id}"
        record = {
            "stateId": state_id,
            "notificationId": notification_id,
            "subjectId": actor,
            "roleId": str(role["id"]),
            "acknowledged": True,
            "acknowledgedAt": _now_iso(),
            "acknowledgedBy": subject_id or str(role["id"]),
            "severity": rows[notification_id]["severity"],
            "correlationId": correlation_id,
        }
        self._repo.save_record(NOTIFICATION_STATES, state_id, record)

        audit = self._append_audit(
            category="shell.notification",
            action="acknowledge",
            actor_role_id=str(role["id"]),
            actor_subject_id=subject_id,
            target_type="notification",
            target_id=notification_id,
            message=f"{role['label']} 已確認通知 {notification_id}",
            metadata={
                "notificationId": notification_id,
                "severity": record["severity"],
                "idempotencyKey": idempotency_key,
                "correlationId": correlation_id,
            },
        )
        response = {
            "notification": record,
            "auditEvent": audit,
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        self._remember("acknowledge_notification", idempotency_key, response)
        return response

    def get_notification_preferences(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
    ) -> dict[str, Any]:
        """Return the acting user's durable delivery preferences."""
        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        actor = self._actor_key(subject_id=subject_id, role=role)
        stored = self._records_by_id(NOTIFICATION_PREFERENCES, "subjectId").get(actor)
        preferences = _copy(stored["preferences"]) if stored else _copy(DEFAULT_PREFERENCES)
        return {
            "roleId": str(role["id"]),
            "preferences": preferences,
            "isDefault": stored is None,
            "severityLevels": list(SEVERITY_ORDER),
        }

    def update_notification_preferences(
        self,
        *,
        preferences: dict[str, Any],
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Durably persist the acting user's notification preferences."""
        replay = self._replay("update_notification_preferences", idempotency_key)
        if replay is not None:
            return replay

        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        floor = str(preferences.get("severityFloor", DEFAULT_PREFERENCES["severityFloor"]))
        if floor not in SEVERITY_ORDER:
            raise ShellPolicyError(f"severity floor must be one of {', '.join(SEVERITY_ORDER)}")
        channels = preferences.get("channels")
        if not isinstance(channels, dict) or not channels:
            raise ShellPolicyError("at least one delivery channel must be declared")

        resolved = {
            "channels": {key: bool(value) for key, value in channels.items()},
            "severityFloor": floor,
            "digest": str(preferences.get("digest", DEFAULT_PREFERENCES["digest"])),
        }
        actor = self._actor_key(subject_id=subject_id, role=role)
        record = {
            "subjectId": actor,
            "roleId": str(role["id"]),
            "preferences": resolved,
            "updatedAt": _now_iso(),
            "updatedBy": actor,
            "correlationId": correlation_id,
        }
        self._repo.save_record(NOTIFICATION_PREFERENCES, actor, record)

        audit = self._append_audit(
            category="shell.notification",
            action="update_preferences",
            actor_role_id=str(role["id"]),
            actor_subject_id=subject_id,
            target_type="notification_preferences",
            target_id=str(role["id"]),
            message=f"{role['label']} 更新通知偏好（floor={floor}）",
            metadata={
                "preferences": resolved,
                "idempotencyKey": idempotency_key,
                "correlationId": correlation_id,
            },
        )
        response = {
            "roleId": str(role["id"]),
            "preferences": resolved,
            "auditEvent": audit,
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        self._remember("update_notification_preferences", idempotency_key, response)
        return response

    # ------------------------------------------------------------------
    # Global search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return authorized cross-domain results plus keyboard commands.

        Results are built from the role-filtered envelope and then re-checked
        against the role's allowed workspaces, so a task belonging to a
        workspace the role cannot enter never appears — including as a title.
        """
        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        allowed = set(self._allowed_workspaces(role))
        base = self._state.search(
            query,
            role_id=role["id"],
            subject_id=subject_id,
            system_roles=system_roles,
            correlation_id=correlation_id,
        )
        results: list[dict[str, Any]] = []
        for item in base["items"]:
            workspace = str((item.get("target") or {}).get("workspace", "today"))
            if workspace not in allowed:
                continue
            results.append(
                {
                    **item,
                    "workspace": workspace,
                    "kind": "workspace" if item["id"].startswith("workspace-") else "entity",
                    "href": (
                        f"/tasks?taskId={item['entityId']}&workspace={workspace}"
                        if not item["id"].startswith("workspace-")
                        else f"/?workspace={workspace}"
                    ),
                }
            )

        commands = [
            {
                "id": f"command-{entry['key']}",
                "label": entry["label"],
                "description": entry["description"],
                "href": entry["href"],
                "kind": "command",
                "shortcut": None,
            }
            for entry in ENTRY_POINTS
            if entry["workspace"] in allowed
            and (not entry.get("requiresAdmin") or self._is_admin(role))
        ]
        normalized = query.strip().casefold()
        if normalized:
            commands = [
                command
                for command in commands
                if normalized in f"{command['label']} {command['description']}".casefold()
            ]

        return {
            "meta": {
                "generatedAt": _now_iso(),
                "correlationId": correlation_id,
                "role": {"id": role["id"], "label": role["label"]},
                "source": "operator-shell-search",
                "query": query,
                "allowedWorkspaces": sorted(allowed),
            },
            "items": results[:limit],
            "count": len(results[:limit]),
            "commands": commands,
            "total": len(results),
        }

    # ------------------------------------------------------------------
    # Administration
    # ------------------------------------------------------------------

    def get_admin(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the role/workspace administration view (admin roles only)."""
        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        if not self._is_admin(role):
            raise ShellForbidden("目前角色無法檢視平台管理；需要營運主管權限。")
        grants = self._records_by_id(ADMIN_GRANTS, "roleId")
        rows = []
        for item in ROLES:
            grant = grants.get(str(item["id"]))
            rows.append(
                {
                    "roleId": str(item["id"]),
                    "label": str(item["label"]),
                    "subtitle": str(item.get("subtitle", "")),
                    "allowedWorkspaces": (
                        [str(w) for w in grant["allowedWorkspaces"]]
                        if grant
                        else [str(w) for w in item["allowedWorkspaces"]]
                    ),
                    "overridden": grant is not None,
                    "updatedAt": grant.get("updatedAt") if grant else None,
                    "updatedBy": grant.get("updatedBy") if grant else None,
                }
            )
        return {
            "meta": {
                "generatedAt": _now_iso(),
                "correlationId": correlation_id,
                "role": {"id": role["id"], "label": role["label"]},
                "source": "operator-shell-admin",
            },
            "roles": rows,
            "workspaces": [
                {"id": str(item["id"]), "label": str(item["label"])} for item in WORKSPACES
            ],
            "auditFeed": self.list_audit_feed()[:10],
        }

    def update_role_workspaces(
        self,
        *,
        target_role_id: str,
        allowed_workspaces: list[str],
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Durably override a role's workspace grants. High-risk, always audited."""
        replay = self._replay("update_role_workspaces", idempotency_key)
        if replay is not None:
            return replay

        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        if not self._is_admin(role):
            raise ShellForbidden("目前角色無法變更權限；需要營運主管權限。")

        known_roles = {str(item["id"]) for item in ROLES}
        if target_role_id not in known_roles:
            raise ShellNotFound(f"role {target_role_id} not found")

        known_workspaces = {str(item["id"]) for item in WORKSPACES}
        unknown = [item for item in allowed_workspaces if item not in known_workspaces]
        if unknown:
            raise ShellPolicyError(f"unknown workspace(s): {', '.join(sorted(unknown))}")
        if "today" not in allowed_workspaces:
            raise ShellPolicyError("每個角色都必須保留「今日工作」工作區。")
        if target_role_id in ADMIN_ROLE_IDS and "govern" not in allowed_workspaces:
            raise ShellConflict("不可移除營運主管的治理稽核工作區，否則將無人可還原權限。")

        record = {
            "roleId": target_role_id,
            "allowedWorkspaces": [str(item) for item in allowed_workspaces],
            "updatedAt": _now_iso(),
            "updatedBy": subject_id or str(role["id"]),
            "correlationId": correlation_id,
        }
        self._repo.save_record(ADMIN_GRANTS, target_role_id, record)

        audit = self._append_audit(
            category="shell.admin",
            action="update_role_workspaces",
            actor_role_id=str(role["id"]),
            actor_subject_id=subject_id,
            target_type="role",
            target_id=target_role_id,
            message=(
                f"{role['label']} 將 {target_role_id} 的工作區設為 "
                f"{', '.join(record['allowedWorkspaces'])}"
            ),
            metadata={
                "targetRoleId": target_role_id,
                "allowedWorkspaces": record["allowedWorkspaces"],
                "highRisk": True,
                "idempotencyKey": idempotency_key,
                "correlationId": correlation_id,
            },
        )
        response = {
            "grant": record,
            "auditEvent": audit,
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        self._remember("update_role_workspaces", idempotency_key, response)
        return response

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_settings(
        self,
        *,
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return workspace settings for the acting role."""
        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        actor = self._actor_key(subject_id=subject_id, role=role)
        stored = self._records_by_id(SETTINGS, "scope").get(actor)
        values = (
            _copy(stored["values"])
            if stored
            else {"locale": "zh-TW", "timezone": "Asia/Taipei", "density": "comfortable"}
        )
        return {
            "meta": {
                "generatedAt": _now_iso(),
                "correlationId": correlation_id,
                "role": {"id": role["id"], "label": role["label"]},
                "source": "operator-shell-settings",
            },
            "scope": actor,
            "values": values,
            "isDefault": stored is None,
            "updatedAt": stored.get("updatedAt") if stored else None,
            "updatedBy": stored.get("updatedBy") if stored else None,
            "options": {
                "locale": ["zh-TW", "en-US"],
                "timezone": ["Asia/Taipei", "UTC"],
                "density": ["comfortable", "compact"],
            },
        }

    def update_settings(
        self,
        *,
        values: dict[str, Any],
        role_id: str | None = None,
        subject_id: str | None = None,
        system_roles: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Durably persist workspace settings. Governed and audited."""
        replay = self._replay("update_settings", idempotency_key)
        if replay is not None:
            return replay

        role = self._role(role_id=role_id, subject_id=subject_id, system_roles=system_roles)
        options = self.get_settings(role_id=role["id"], subject_id=subject_id)["options"]
        resolved: dict[str, Any] = {}
        for key, allowed_values in options.items():
            if key not in values:
                continue
            candidate = str(values[key])
            if candidate not in allowed_values:
                raise ShellPolicyError(
                    f"{key} must be one of {', '.join(allowed_values)}; got {candidate}"
                )
            resolved[key] = candidate
        if not resolved:
            raise ShellPolicyError("no known setting was supplied")

        current = self.get_settings(role_id=role["id"], subject_id=subject_id)["values"]
        merged = {**current, **resolved}
        actor = self._actor_key(subject_id=subject_id, role=role)
        record = {
            "scope": actor,
            "values": merged,
            "updatedAt": _now_iso(),
            "updatedBy": actor,
            "correlationId": correlation_id,
        }
        self._repo.save_record(SETTINGS, actor, record)

        audit = self._append_audit(
            category="shell.settings",
            action="update",
            actor_role_id=str(role["id"]),
            actor_subject_id=subject_id,
            target_type="settings",
            target_id=str(role["id"]),
            message=f"{role['label']} 更新工作區設定",
            metadata={
                "changed": resolved,
                "idempotencyKey": idempotency_key,
                "correlationId": correlation_id,
            },
        )
        response = {
            "scope": actor,
            "values": merged,
            "auditEvent": audit,
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        self._remember("update_settings", idempotency_key, response)
        return response

    # ------------------------------------------------------------------
    # Franchisee
    # ------------------------------------------------------------------

    def get_franchisee_view(
        self,
        *,
        subject_id: str | None = None,
        store_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the franchisee-scoped view.

        Scoping is by store workspace, not by borrowing an operator role: a
        franchisee is not an operator, so anything targeting the govern/growth/
        network workspaces (approvals, model snapshots, network plans) is
        operator-only and never reaches here.

        Both the task and the notification projections are allow-lists and
        fail closed — a row with no declared target workspace is dropped rather
        than shown, so a future seed row cannot leak by defaulting open.
        """
        subject = subject_id or "franchisee-unknown"
        store = store_id or "STORE-001"
        # Read the widest envelope available, then apply the franchisee scope
        # here. The operator role is only a source of rows; none of its
        # role-scoped presentation (KPIs, risk rows, audit feed) is projected.
        envelope = self._state.get_today(role_id="ops-lead", subject_id=subject)
        acked_ids = {
            str(record["notificationId"])
            for record in self._repo.list_records(FRANCHISEE_ACKS)
            if str(record.get("subjectId")) == subject
        }

        tasks = [
            {key: item[key] for key in FRANCHISEE_TASK_FIELDS if key in item}
            for item in envelope["workQueue"]
            if str(item.get("workspace")) == FRANCHISEE_WORKSPACE
        ]
        notifications = [
            {
                "notificationId": str(item.get("id") or item.get("title")),
                "title": item.get("title"),
                "detail": item.get("detail"),
                "severity": TONE_SEVERITY.get(str(item.get("tone")), "info"),
                "acknowledged": str(item.get("id") or item.get("title")) in acked_ids,
            }
            for item in envelope["notifications"]
            if str((item.get("target") or {}).get("workspace", "")) == FRANCHISEE_WORKSPACE
        ]
        reports = [
            _copy(record)
            for record in self._repo.list_records(FRANCHISEE_REPORTS)
            if str(record.get("subjectId")) == subject
        ]
        reports.sort(key=lambda record: str(record.get("createdAt")), reverse=True)

        return {
            "meta": {
                "generatedAt": _now_iso(),
                "correlationId": correlation_id,
                "source": "operator-shell-franchisee",
                "scope": {"subjectId": subject, "storeId": store},
                "viewport": "mobile-first",
            },
            "store": {"id": store, "label": f"門市 {store}"},
            "tasks": tasks,
            "notifications": notifications,
            "reports": reports,
            "reportCategories": sorted(FRANCHISEE_REPORT_CATEGORIES),
        }

    def franchisee_acknowledge(
        self,
        *,
        notification_id: str,
        subject_id: str | None = None,
        store_id: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record a franchisee's acknowledgement of a notification."""
        replay = self._replay("franchisee_acknowledge", idempotency_key)
        if replay is not None:
            return replay

        subject = subject_id or "franchisee-unknown"
        view = self.get_franchisee_view(subject_id=subject, store_id=store_id)
        known = {str(item["notificationId"]) for item in view["notifications"]}
        if notification_id not in known:
            raise ShellNotFound(f"notification {notification_id} not found")

        ack_id = f"{subject}:{notification_id}"
        record = {
            "ackId": ack_id,
            "notificationId": notification_id,
            "subjectId": subject,
            "storeId": view["store"]["id"],
            "acknowledgedAt": _now_iso(),
            "correlationId": correlation_id,
        }
        self._repo.save_record(FRANCHISEE_ACKS, ack_id, record)

        audit = self._append_audit(
            category="shell.franchisee",
            action="acknowledge",
            actor_role_id="franchisee",
            actor_subject_id=subject,
            target_type="notification",
            target_id=notification_id,
            message=f"加盟主 {subject} 已確認 {notification_id}",
            metadata={
                "notificationId": notification_id,
                "storeId": record["storeId"],
                "idempotencyKey": idempotency_key,
                "correlationId": correlation_id,
            },
        )
        response = {
            "acknowledgement": record,
            "auditEvent": audit,
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        self._remember("franchisee_acknowledge", idempotency_key, response)
        return response

    def franchisee_report(
        self,
        *,
        category: str,
        message: str,
        subject_id: str | None = None,
        store_id: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record a franchisee field report."""
        replay = self._replay("franchisee_report", idempotency_key)
        if replay is not None:
            return replay

        if category not in FRANCHISEE_REPORT_CATEGORIES:
            raise ShellPolicyError(
                f"category must be one of {', '.join(sorted(FRANCHISEE_REPORT_CATEGORIES))}"
            )
        body = (message or "").strip()
        if not body:
            raise ShellPolicyError("回報內容不可為空白。")

        subject = subject_id or "franchisee-unknown"
        report_id = f"FR-{uuid4().hex[:8].upper()}"
        record = {
            "reportId": report_id,
            "subjectId": subject,
            "storeId": store_id or "STORE-001",
            "category": category,
            "message": body,
            "status": "received",
            "createdAt": _now_iso(),
            "correlationId": correlation_id,
        }
        self._repo.save_record(FRANCHISEE_REPORTS, report_id, record)

        audit = self._append_audit(
            category="shell.franchisee",
            action="report",
            actor_role_id="franchisee",
            actor_subject_id=subject,
            target_type="report",
            target_id=report_id,
            message=f"加盟主 {subject} 回報 {category}",
            metadata={
                "reportId": report_id,
                "category": category,
                "storeId": record["storeId"],
                "idempotencyKey": idempotency_key,
                "correlationId": correlation_id,
            },
        )
        response = {
            "report": record,
            "auditEvent": audit,
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        self._remember("franchisee_report", idempotency_key, response)
        return response

    # ------------------------------------------------------------------
    # Test / dev support
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all durable shell state. Dev/test only."""
        self._repo.clear()
        self._idempotency_cache.clear()
        self._audit_feed.clear()


__all__ = [
    "ADMIN_GRANTS",
    "DEFAULT_PREFERENCES",
    "ENTRY_POINTS",
    "FRANCHISEE_ACKS",
    "FRANCHISEE_REPORTS",
    "NOTIFICATION_PREFERENCES",
    "NOTIFICATION_STATES",
    "SETTINGS",
    "SEVERITY_ORDER",
    "SHELL_COLLECTIONS",
    "TASK_ASSIGNMENTS",
    "InMemoryShellRepository",
    "ShellConflict",
    "ShellIdempotencyRecord",
    "ShellForbidden",
    "ShellNotFound",
    "ShellPolicyError",
    "ShellRepository",
    "ShellService",
]
