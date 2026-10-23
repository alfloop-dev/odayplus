"""Materialize the production Store Ops state from canonical data.

``DurableStoreOpsRepository`` fails closed on PostgreSQL until a tenant's
Store Ops state document exists, and every Store Ops write reads that document
first, so nothing in the request path can ever create it. This module is the
one writer that may: it projects the tenant's ``core.stores`` rows and
ForecastOps four-light alerts into the Store Ops view model and saves it into
that tenant's own partition through the repository, so the fixture-signature
write guard still applies and no tenant's state can reach another's.

The projection is honest by construction:

* no stores means an empty state, never seed data;
* absent health observations stay ``None`` with explicit slot availability;
  an empty issue queue is not measured equipment health;
* issues are derived only from open, non-green ForecastOps alerts.

Re-running is idempotent. Stores are re-projected every run, but an issue that
already exists is left exactly as the operator lifecycle last wrote it, and
evidence, audit events and attachments are carried forward untouched. When the
projected state equals the persisted one nothing is written.

The save is a versioned compare-and-swap: if an operator write lands between
the read and the save, the save is refused, the fresh state is re-read and the
projection re-applied on top of it, so a recurring run never rolls back
lifecycle transitions, audit events, attachments or the audit ordinal.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from modules.opsboard.application.store_ops import (
    _LIGHT_DIMENSIONS,
    _STORE_OPS_WRITE_ATTEMPTS,
    StoreOpsConcurrentWriteConflict,
    StoreOpsLiveDataUnavailable,
    project_store_ops_health,
)

#: Audit ids are ``AUD-OPS-<ordinal>``; the demo fixture owns 7001/7002, so a
#: materialized state starts well past them and can never mint a fixture id.
MATERIALIZED_AUDIT_ORDINAL_START = 10001

_ISSUE_PREFIX = "ISS-FC-"
_EVIDENCE_PREFIX = "EV-FC-"
_OPEN_ALERT_STATUSES = frozenset({"open", "acknowledged", "in_progress"})
_INACTIVE_STORE_STATUSES = frozenset({"closed", "transferred"})

# ForecastOps has four alert levels; the Store Ops light only has three.
_ALERT_LEVEL_TO_LIGHT = {"red": "red", "orange": "yellow", "yellow": "yellow", "green": "green"}
_ALERT_LEVEL_TO_SEVERITY = {"red": "critical", "orange": "high", "yellow": "medium"}
_SEVERITY_SLA_HOURS = {"critical": 4, "high": 8, "medium": 24, "low": 72}

#: Canonical source per four-light dimension; ``None`` = no canonical KPI yet.
_LIGHT_SOURCES: dict[str, str | None] = {
    "demand": "forecastops.alerts",
    "operations": None,
    "staffing": None,
    "margin": None,
}

_ALERT_TITLES = {
    "sitescore_gap": "預測營收低於選址預期",
    "data_quality_stale": "預測資料過期，告警信心降低",
}


@dataclass(frozen=True)
class StoreOpsMaterializationResult:
    status: str  # "written" | "unchanged" | "skipped"
    reason: str
    store_count: int = 0
    issue_count: int = 0
    new_issue_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "store_count": self.store_count,
            "issue_count": self.issue_count,
            "new_issue_count": self.new_issue_count,
        }


def _value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        value = record.get(name, default)
    else:
        value = getattr(record, name, default)
    if isinstance(value, Enum):
        return value.value
    return default if value is None else value


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def empty_store_ops_state() -> dict[str, Any]:
    """The honest Store Ops state for a tenant with no canonical stores."""
    return {
        "stores": [],
        "issues": [],
        "evidence": [],
        "auditEvents": [],
        "attachments": [],
        "nextAuditOrdinal": MATERIALIZED_AUDIT_ORDINAL_START,
    }


def _project_store(store: Any) -> dict[str, Any]:
    return {
        "id": str(_value(store, "store_id")),
        "name": str(_value(store, "store_name", "")),
        "district": str(_value(store, "region_code", "")),
        "city": "",
        "manager": "",
        "lights": {dimension: None for dimension in _LIGHT_DIMENSIONS},
        "lightSources": dict(_LIGHT_SOURCES),
        "riskScore": None,
    }


def _alert_issue(alert: Any, store: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    alert_id = str(_value(alert, "alert_id"))
    level = str(_value(alert, "alert_level", "")).lower()
    reason = str(_value(alert, "alert_reason_code", ""))
    severity = _ALERT_LEVEL_TO_SEVERITY[level]
    opened_raw = _value(alert, "opened_at")
    opened_at = _iso(opened_raw)
    opened_dt = _parse_datetime(opened_raw)
    sla_due_at = (
        (opened_dt + timedelta(hours=_SEVERITY_SLA_HOURS[severity])).isoformat()
        if opened_dt is not None
        else opened_at
    )
    policy_version = str(_value(alert, "policy_version", ""))
    issue_id = f"{_ISSUE_PREFIX}{alert_id}"
    evidence_id = f"{_EVIDENCE_PREFIX}{alert_id}"
    summary = (
        f"ForecastOps alert {alert_id} is {level} "
        f"(reason {reason or 'unspecified'}, policy {policy_version or 'unspecified'})."
    )
    issue = {
        "id": issue_id,
        "title": _ALERT_TITLES.get(reason, f"ForecastOps 告警：{reason or level}"),
        "storeId": store["id"],
        "storeName": store["name"],
        "status": "new",
        "severity": severity,
        "source": "forecastOps",
        "ownerRoleId": "opsLead",
        "ownerName": "營運主管",
        "slaDueAt": sla_due_at,
        "createdAt": opened_at,
        "updatedAt": opened_at,
        "evidenceIds": [evidence_id],
        "summary": summary,
        "sourceRef": {"kind": "forecastops.alert", "id": alert_id, "alertLevel": level},
    }
    evidence = {
        "id": evidence_id,
        "issueId": issue_id,
        "kind": "forecastOps",
        "title": "ForecastOps four-light alert",
        "sourceLabel": "ForecastOps",
        "summary": summary,
        "polarity": "supporting",
        "confidence": None,
        "occurredAt": opened_at,
        "sourceRef": {"kind": "forecastops.alert", "id": alert_id, "alertLevel": level},
    }
    return issue, evidence


def project_store_ops_state(
    *,
    stores: Iterable[Any],
    alerts_by_store: Mapping[str, Iterable[Any]],
    existing_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Pure projection: canonical rows + previous state -> next Store Ops state."""
    stores = list(stores)
    previous = copy.deepcopy(dict(existing_state)) if existing_state else empty_store_ops_state()
    issues: list[dict[str, Any]] = list(previous.get("issues", []))
    evidence: list[dict[str, Any]] = list(previous.get("evidence", []))
    known_issue_ids = {str(issue.get("id")) for issue in issues}
    known_evidence_ids = {str(item.get("id")) for item in evidence}

    projected_stores: list[dict[str, Any]] = []
    for store in sorted(stores, key=lambda row: str(_value(row, "store_id"))):
        if not bool(_value(store, "is_current", True)):
            continue
        if str(_value(store, "store_status", "")).lower() in _INACTIVE_STORE_STATUSES:
            continue
        projected = _project_store(store)
        for alert in sorted(
            alerts_by_store.get(projected["id"], ()),
            key=lambda row: (_iso(_value(row, "opened_at")), str(_value(row, "alert_id"))),
        ):
            if str(_value(alert, "status", "open")).lower() not in _OPEN_ALERT_STATUSES:
                continue
            level = str(_value(alert, "alert_level", "")).lower()
            if level not in _ALERT_LEVEL_TO_LIGHT:
                continue
            if level == "green":
                continue
            issue, item = _alert_issue(alert, projected)
            if issue["id"] not in known_issue_ids:
                issues.append(issue)
                known_issue_ids.add(issue["id"])
            if item["id"] not in known_evidence_ids:
                evidence.append(item)
                known_evidence_ids.add(item["id"])
        projected_stores.append(projected)

    store_names = {store["id"]: store["name"] for store in projected_stores}
    for issue in issues:
        store_id = str(issue.get("storeId"))
        if store_id in store_names:
            issue["storeName"] = store_names[store_id]
    return project_store_ops_health({
        "stores": projected_stores,
        "issues": issues,
        "evidence": evidence,
        "auditEvents": list(previous.get("auditEvents", [])),
        "attachments": list(previous.get("attachments", [])),
        "nextAuditOrdinal": max(
            int(previous.get("nextAuditOrdinal") or MATERIALIZED_AUDIT_ORDINAL_START),
            MATERIALIZED_AUDIT_ORDINAL_START,
        ),
    }, alerts_by_store=alerts_by_store, tenant_id=next(
        (str(_value(store, "tenant_id")) for store in stores), None,
    ))


def materialize_store_ops_state(
    *,
    repository: Any,
    store_repository: Any,
    forecastops_repository: Any,
    tenant_id: str,
) -> StoreOpsMaterializationResult:
    """Project the tenant's canonical data into its live Store Ops document.

    Only a live (PostgreSQL) durable repository is materialized; SQLite and
    in-memory repositories keep their seeded demo state. The document written
    is the one ``repository.for_tenant(tenant_id)`` owns, never a shared one.
    Any read failure raises :class:`StoreOpsLiveDataUnavailable` before
    anything is written, and a persisted state carrying fixture identifiers is
    refused rather than overwritten, so the read path keeps reporting it.
    """
    if not bool(getattr(repository, "is_live", False)):
        return StoreOpsMaterializationResult(status="skipped", reason="repository_not_live")
    tenant = str(tenant_id or "").strip()
    if not tenant:
        raise StoreOpsLiveDataUnavailable("Store Ops materialization requires a tenant scope")
    tenant_repository = repository.for_tenant(tenant)

    try:
        stores = [
            store
            for store in store_repository.list_stores(tenant_id=tenant)
            if _value(store, "tenant_id") == tenant
        ]
    except Exception as exc:
        raise StoreOpsLiveDataUnavailable(
            f"Store Ops materialization could not read stores: {type(exc).__name__}: {exc}"
        ) from exc
    alerts_by_store: dict[str, list[Any]] = {}
    for store in stores:
        store_id = str(_value(store, "store_id"))
        try:
            alerts_by_store[store_id] = [
                alert
                for alert in forecastops_repository.list_alerts_by_store(tenant, store_id)
                if _value(alert, "tenant_id") == tenant and _value(alert, "store_id") == store_id
            ]
        except Exception as exc:
            raise StoreOpsLiveDataUnavailable(
                "Store Ops materialization could not read ForecastOps alerts: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    for _attempt in range(_STORE_OPS_WRITE_ATTEMPTS):
        existing, version = tenant_repository.read_versioned_state()
        if existing is not None and tenant_repository.contains_fixture_signature(existing):
            raise StoreOpsLiveDataUnavailable(
                "Store Ops production state contains canonical fixture identifiers; "
                "refusing to materialize over it"
            )
        state = project_store_ops_state(
            stores=stores,
            alerts_by_store=alerts_by_store,
            existing_state=existing,
        )
        previous_issue_count = len(existing.get("issues", [])) if existing else 0
        result_fields = {
            "store_count": len(state["stores"]),
            "issue_count": len(state["issues"]),
            "new_issue_count": len(state["issues"]) - previous_issue_count,
        }
        if existing is not None and state == existing:
            return StoreOpsMaterializationResult(
                status="unchanged", reason="state_current", **result_fields
            )
        if tenant_repository.compare_and_save_state(state, expected_version=version):
            return StoreOpsMaterializationResult(
                status="written",
                reason="created" if existing is None else "refreshed",
                **result_fields,
            )
    raise StoreOpsConcurrentWriteConflict(
        "Store Ops state kept changing during materialization; nothing was written, retry"
    )


__all__ = [
    "MATERIALIZED_AUDIT_ORDINAL_START",
    "StoreOpsMaterializationResult",
    "empty_store_ops_state",
    "materialize_store_ops_state",
    "project_store_ops_state",
]
