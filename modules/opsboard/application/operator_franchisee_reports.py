"""Read-only supervisor notification facts from actually persisted franchisee forms.

The personal inbox is a current-authorized view. A report/audit pair is not an
email or external delivery receipt and never invents a named recipient.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from typing import Any

from modules.opsboard.application.operator_pending_projection import _store_in_scope
from shared.auth import Principal, Role


def admit_supervisor_reports(
    reports: list[dict[str, Any]], audit_events: list[dict[str, Any]], *,
    principal: Principal, store_repository: Any, brand_repository: Any,
    can_read: Callable[[str], bool],
) -> dict[str, Any]:
    coverage = {"complete": False, "state": "available", "reasonCode": "CURRENT_AUTHORIZED_SUPERVISOR_REPORTS_ONLY"}
    if (not principal.authenticated or not principal.subject_id or not principal.tenant_id
        or Role.REGIONAL_SUPERVISOR not in principal.roles
        or not can_read("operator_console") or not can_read("intervention")):
        return {"records": [], "coverage": coverage}
    counts = Counter(row.get("reportId") for row in reports if isinstance(row, dict) and isinstance(row.get("reportId"), str))
    admitted = []
    incomplete = False
    for row in reports:
        if not isinstance(row, dict):
            incomplete = True
            continue
        identity, subject, store, created = (row.get(field) for field in ("reportId", "subjectId", "storeId", "createdAt"))
        if (not all(isinstance(value, str) and value.strip() for value in (identity, subject, store, created))
            or subject == "franchisee-unknown" or counts[identity] != 1
            or row.get("category") not in {"equipment", "staffing", "supply", "customer", "other"}
            or not isinstance(row.get("message"), str) or not row["message"].strip()
            or row.get("status") != "received"):
            incomplete = True
            continue
        try:
            instant = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            incomplete = True
            continue
        if instant.tzinfo is None:
            incomplete = True
            continue
        if not _store_in_scope(principal, store, store_repository, brand_repository):
            continue
        # The event corroborates the trusted saved-form actor/ID/store. It is
        # not itself the notification nor any assertion of email delivery.
        matches = [event for event in audit_events if isinstance(event, dict)
            and event.get("category") == "shell.franchisee" and event.get("action") == "report"
            and event.get("actorRoleId") == "franchisee" and event.get("actorSubjectId") == subject
            and event.get("targetType") == "report" and event.get("targetId") == identity
            and isinstance(event.get("metadata"), dict)
            and event["metadata"].get("reportId") == identity and event["metadata"].get("storeId") == store
            and event["metadata"].get("category") == row["category"]
            and event["metadata"].get("correlationId") == row.get("correlationId")]
        if len(matches) != 1:
            incomplete = True
            continue
        admitted.append({key: deepcopy(row.get(key)) for key in (
            "reportId", "subjectId", "storeId", "category", "message", "status", "createdAt", "correlationId")})
    if incomplete:
        coverage.update(state="partial", reasonCode="SOME_PERSISTED_REPORT_PROVENANCE_UNAVAILABLE")
    return {"records": admitted, "coverage": coverage}
