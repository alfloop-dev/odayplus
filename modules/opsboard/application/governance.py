"""Govern workspace application service (ODP-OC-R4-009).

Aggregates the complete Govern (治理稽核) surface into a single snapshot and
owns the write paths the workspace needs:

  • snapshot()                → approvals, decisions, audit trail, status board
                                 (Data Quality / Model / Connector / SLA / Users)
                                 and evidence-package history — every governance
                                 value builder reachable from one payload.
  • decide()                  → approve / return / reject an approval with the
                                 return/reject-requires-reason policy enforced
                                 server-side; persists a Decision Log row and an
                                 Audit Trail event so the outcome survives reload.
  • export_evidence_package() → records scope, range, format, actor, correlation
                                 and retention policy; appends an Audit Trail
                                 event and a history entry.

Aggregation contract
--------------------
Local/test mode retains the deterministic package fixture. Production mode
aggregates SiteScore decisions, AVM reports, NetPlan approvals, and PriceOps
plans from tenant-scoped canonical durable repositories.

Design source: canonical package 6 (r4-20260707-package-6),
data-screen-label "Govern 治理稽核".
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "GovernanceService",
    "GovernanceNotFound",
    "GovernancePolicyError",
    "GovernanceConflict",
    "DECISION_ACTIONS",
]

# The three decision verbs the Approval Center exposes (package-6 buttons
# 核准 / 退回修改 / 駁回).  "approve" is optional-reason; the other two require one.
DECISION_ACTIONS = ("approve", "return", "reject")
_REASON_REQUIRED = {"return", "reject"}
_MIN_REASON_LEN = 10

_FINAL_DECISION_LABEL = {
    "approve": "Approved",
    "return": "Returned",
    "reject": "Rejected",
}
_APPROVAL_STATUS = {
    "approve": "approved",
    "return": "returned",
    "reject": "rejected",
}


class GovernanceNotFound(Exception):
    """Raised when an approval id is not present in the governance ledger."""


class GovernancePolicyError(Exception):
    """Raised when a decision violates a server-side approval policy.

    The canonical case is a return/reject submitted without the required
    reason; the message is safe to surface to the client (HTTP 422).
    """


class GovernanceConflict(Exception):
    """Raised when a decision targets an approval that is no longer pending."""


def _now_iso_minute() -> str:
    """Return an ISO-ish 'YYYY-MM-DD HH:MM' stamp (Decision Log / Audit format)."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M")


def _clone(value: Any) -> Any:
    return deepcopy(value)


class GovernanceService:
    """In-memory Govern workspace aggregation + write service.

    One instance per API-router lifetime (mirrors ``OperatorStateService`` and
    ``GrowthService``) so writes are visible to subsequent reads.  Optionally
    merges a shared ``GrowthService`` for live Growth decisions/approvals.
    """

    def __init__(
        self,
        *,
        growth_service: Any | None = None,
        initial_state: dict[str, Any] | None = None,
        seed_fixtures: bool = True,
        sitescore_decision_repository: Any | None = None,
        avm_repository: Any | None = None,
        netplan_repository: Any | None = None,
        priceops_repository: Any | None = None,
        tenant_id: str | None = None,
        require_canonical: bool = False,
    ) -> None:
        self._growth = growth_service
        self._sitescore_decision_repository = sitescore_decision_repository
        self._avm_repository = avm_repository
        self._netplan_repository = netplan_repository
        self._priceops_repository = priceops_repository
        self._tenant_id = tenant_id
        self._require_canonical = require_canonical
        self._state = _clone(
            initial_state
            if initial_state is not None
            else _seed_state()
            if seed_fixtures
            else _empty_state()
        )
        self._idempotency = _clone(self._state.pop("idempotency", {}))
        if self._require_canonical:
            self._refresh_canonical()

    def export_state(self) -> dict[str, Any]:
        return {
            **_clone(self._state),
            "idempotency": _clone(self._idempotency),
        }

    def export_growth_state(self) -> dict[str, Any] | None:
        if self._growth is None:
            return None
        exporter = getattr(self._growth, "export_state", None)
        return exporter() if exporter is not None else None

    def upsert_approval(self, approval: dict[str, Any]) -> dict[str, Any]:
        row = _clone(approval)
        for index, existing in enumerate(self._state["approvals"]):
            if existing.get("id") == row.get("id"):
                self._state["approvals"][index] = row
                return _clone(row)
        self._state["approvals"].append(row)
        return _clone(row)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def snapshot(
        self,
        *,
        role_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the full Govern workspace snapshot.

        ``role_id`` is accepted for parity with the role-aware shell; the Govern
        workspace is an oversight surface, so the snapshot is not row-filtered by
        role here (visibility is enforced by workspace navigation policy).
        """
        if self._require_canonical:
            self._refresh_canonical()
        approvals = self._merged_approvals()
        decisions = self._merged_decisions()
        return {
            "approvals": approvals,
            "decisions": decisions,
            "auditRows": _clone(self._state["auditRows"]),
            "statusBoard": _clone(self._state["statusBoard"]),
            "evidencePackages": _clone(self._state["evidencePackages"]),
            "counts": {
                "pendingApprovals": sum(1 for a in approvals if a["status"] == "pending"),
                "decisions": len(decisions),
                "auditRows": len(self._state["auditRows"]),
            },
            "role_id": role_id,
            "correlation_id": correlation_id,
            "source": "canonical" if self._require_canonical else "api",
        }

    def _merged_approvals(self) -> list[dict[str, Any]]:
        """Own approvals + live Growth pending approvals (deduped by id)."""
        rows = _clone(self._state["approvals"])
        seen = {row["id"] for row in rows}
        for growth_row in self._growth_approvals():
            if growth_row["id"] not in seen:
                rows.append(growth_row)
                seen.add(growth_row["id"])
        return rows

    def _merged_decisions(self) -> list[dict[str, Any]]:
        """Own Decision Log + live Growth decisions (deduped, newest first)."""
        rows = _clone(self._state["decisions"])
        seen = {row["id"] for row in rows}
        for growth_row in self._growth_decisions():
            if growth_row["id"] not in seen:
                rows.append(growth_row)
                seen.add(growth_row["id"])
        return rows

    def _growth_approvals(self) -> list[dict[str, Any]]:
        if self._growth is None:
            return []
        out: list[dict[str, Any]] = []
        for item in self._growth.list_approvals():
            if item.get("status") != "pending":
                continue
            out.append(
                {
                    "id": item["id"],
                    "module": "Growth",
                    "title": item.get("title", item["id"]),
                    "requestor": item.get("requester", "Growth Manager"),
                    "submittedAt": item.get("createdAt", ""),
                    "status": "pending",
                    "priority": "medium",
                    "owner": item.get("approver", "行銷經理"),
                    "sla": item.get("due", ""),
                    "entityRef": item.get("ref", item["id"]),
                    "summary": item.get("title", ""),
                    "systemRecommendation": item.get("risk", "Return unless evidence complete."),
                    "risk": item.get("risk", "Growth policy"),
                    "roleNote": "行銷經理 can decide after reviewing evidence.",
                    "evidence": [
                        {"id": f"ev-{item['id']}-{n}", "label": label, "type": "growth", "state": "ready"}
                        for n, label in enumerate(item.get("evidence", []) or [])
                    ],
                }
            )
        return out

    def _growth_decisions(self) -> list[dict[str, Any]]:
        if self._growth is None:
            return []
        freshness = self._growth.get_freshness()
        out: list[dict[str, Any]] = []
        for item in self._growth.list_decisions():
            verdict = str(item.get("verdict", ""))
            final = (
                "Approved"
                if verdict in {"核准", "approved", "Approved"}
                else "Rejected"
                if verdict in {"駁回", "rejected", "Rejected"}
                else "Returned"
            )
            out.append(
                {
                    "id": item["id"],
                    "module": item.get("module", "Growth"),
                    "item": f"{item.get('ref', '')} {item.get('title', '')}".strip(),
                    "systemRecommendation": item.get("recommendation", "—"),
                    "finalDecision": final,
                    "reason": item.get("reason", ""),
                    "actor": item.get("decidedBy", "行銷經理"),
                    "decidedAt": item.get("occurredAt", ""),
                    "model": freshness.get("modelVersion"),
                    "datasetSnapshot": freshness.get("sourceSnapshotId"),
                    "approvalId": item.get("ref", ""),
                }
            )
        return out

    def _refresh_canonical(self) -> None:
        dependencies = {
            "sitescore_decision_repository": self._sitescore_decision_repository,
            "avm_repository": self._avm_repository,
            "netplan_repository": self._netplan_repository,
            "priceops_repository": self._priceops_repository,
            "tenant_id": self._tenant_id,
        }
        missing = [name for name, value in dependencies.items() if not value]
        if missing:
            raise GovernancePolicyError(
                "canonical governance dependencies are unavailable: "
                + ", ".join(missing)
            )

        local_approvals = [
            row for row in self._state["approvals"] if not row.get("_canonical")
        ]
        local_decisions = [
            row for row in self._state["decisions"] if not row.get("_canonical")
        ]
        approvals: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        audit_rows: list[dict[str, Any]] = []

        for decision in self._sitescore_decision_repository.list_decisions():
            row = decision.to_dict()
            approval = {
                "id": decision.decision_id,
                "module": "SiteScore",
                "title": decision.candidate_site_id,
                "status": (
                    "pending"
                    if decision.status.value in {
                        "SYSTEM_RECOMMENDED",
                        "PENDING_REVIEW",
                    }
                    else decision.status.value.lower()
                ),
                "entityRef": decision.candidate_site_id,
                "systemRecommendation": decision.recommendation.value,
                "modelVersion": decision.model_version,
                "policyVersion": decision.policy_version,
                "submittedAt": decision.created_at.isoformat(),
                "_canonical": True,
            }
            approvals.append(approval)
            if decision.is_terminal:
                decisions.append(
                    {
                        "id": decision.decision_id,
                        "module": "SiteScore",
                        "item": decision.candidate_site_id,
                        "systemRecommendation": decision.recommendation.value,
                        "finalDecision": decision.status.value,
                        "reason": (
                            decision.history[-1].reason
                            if decision.history
                            else ""
                        ),
                        "actor": (
                            decision.history[-1].actor
                            if decision.history
                            else decision.created_by
                        ),
                        "decidedAt": (
                            decision.history[-1].at.isoformat()
                            if decision.history
                            else decision.created_at.isoformat()
                        ),
                        "model": decision.model_version,
                        "datasetSnapshot": None,
                        "approvalId": decision.decision_id,
                        "_canonical": True,
                    }
                )
            for transition in row["history"]:
                audit_rows.append(
                    {
                        "id": (
                            f"{decision.decision_id}:"
                            f"{transition['at']}:{transition['action']}"
                        ),
                        "module": "SiteScore",
                        "action": transition["action"],
                        "actor": transition["actor"],
                        "occurredAt": transition["at"],
                        "entityRef": decision.decision_id,
                        "reason": transition["reason"],
                        "_canonical": True,
                    }
                )

        for case in self._avm_repository.list_cases():
            report = self._avm_repository.latest_report(case.case_id)
            if report is None:
                continue
            approval = report.finance_approval
            approvals.append(
                {
                    "id": report.report_id,
                    "module": "AVM",
                    "title": case.store_id,
                    "status": "approved" if approval is not None else "pending",
                    "entityRef": case.case_id,
                    "systemRecommendation": report.fair_price.to_dict(),
                    "modelVersion": report.model_version,
                    "featureVersion": report.feature_version,
                    "submittedAt": report.valued_at.isoformat(),
                    "_canonical": True,
                }
            )
            if approval is not None:
                decisions.append(
                    {
                        "id": approval.decision_id,
                        "module": "AVM",
                        "item": case.store_id,
                        "systemRecommendation": report.fair_price.to_dict(),
                        "finalDecision": "APPROVED",
                        "reason": approval.decision_reason,
                        "actor": approval.actor_id,
                        "decidedAt": approval.approved_at.isoformat(),
                        "model": report.model_version,
                        "datasetSnapshot": list(
                            case.valuation_input.source_snapshot_ids
                        ),
                        "approvalId": report.report_id,
                        "_canonical": True,
                    }
                )

        for scenario in self._netplan_repository.list_scenarios():
            if scenario.tenant_id != self._tenant_id:
                continue
            scenario_approvals = self._netplan_repository.list_approvals(
                scenario.scenario_id
            )
            approvals.append(
                {
                    "id": scenario.scenario_id,
                    "module": "NetPlan",
                    "title": scenario.scenario_name,
                    "status": (
                        "pending"
                        if scenario.status.value == "pending_approval"
                        else scenario.status.value
                    ),
                    "entityRef": scenario.scenario_id,
                    "modelVersion": scenario.model_version,
                    "featureVersion": scenario.feature_version,
                    "solverVersion": scenario.solver_version,
                    "submittedAt": scenario.created_at.isoformat(),
                    "_canonical": True,
                }
            )
            for approval in scenario_approvals:
                decisions.append(
                    {
                        "id": approval.approval_id,
                        "module": "NetPlan",
                        "item": scenario.scenario_name,
                        "systemRecommendation": scenario.status.value,
                        "finalDecision": approval.decision.upper(),
                        "reason": approval.reason,
                        "actor": approval.actor_id,
                        "decidedAt": approval.decided_at.isoformat(),
                        "model": scenario.model_version,
                        "datasetSnapshot": None,
                        "approvalId": scenario.scenario_id,
                        "_canonical": True,
                    }
                )

        plans = [
            plan
            for plan in self._priceops_repository.list_plans()
            if plan.tenant_id == self._tenant_id
        ]
        for plan in plans:
            plan_approvals = self._priceops_repository.list_approvals(plan.plan_id)
            approvals.append(
                {
                    "id": plan.plan_id,
                    "module": "PriceOps",
                    "title": plan.plan_id,
                    "status": (
                        plan_approvals[-1].decision
                        if plan_approvals
                        else "pending"
                    ),
                    "entityRef": plan.plan_id,
                    "submittedAt": plan.created_at.isoformat(),
                    "_canonical": True,
                }
            )
            for approval in plan_approvals:
                decisions.append(
                    {
                        "id": approval.decision_id,
                        "module": "PriceOps",
                        "item": plan.plan_id,
                        "systemRecommendation": plan.status.value,
                        "finalDecision": approval.decision.upper(),
                        "reason": approval.decision_reason,
                        "actor": approval.actor_id,
                        "decidedAt": approval.approved_at.isoformat(),
                        "model": None,
                        "datasetSnapshot": plan.plan_id,
                        "approvalId": plan.plan_id,
                        "_canonical": True,
                    }
                )

        self._state["approvals"] = local_approvals + approvals
        self._state["decisions"] = local_decisions + decisions
        self._state["auditRows"] = [
            row for row in self._state["auditRows"] if not row.get("_canonical")
        ] + audit_rows
        self._state["statusBoard"] = [
            {
                "name": "SiteScore decisions",
                "status": "live",
                "count": len(
                    self._sitescore_decision_repository.list_decisions()
                ),
            },
            {
                "name": "AVM cases",
                "status": "live",
                "count": len(self._avm_repository.list_cases()),
            },
            {
                "name": "NetPlan scenarios",
                "status": "live",
                "count": len(
                    [
                        scenario
                        for scenario in self._netplan_repository.list_scenarios()
                        if scenario.tenant_id == self._tenant_id
                    ]
                ),
            },
            {
                "name": "PriceOps plans",
                "status": "live",
                "count": len(plans),
            },
        ]

    # ------------------------------------------------------------------
    # Write path — decisions
    # ------------------------------------------------------------------

    def decide(
        self,
        *,
        approval_id: str,
        action: str,
        reason: str = "",
        role: str = "營運主管",
        actor_name: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Approve / return / reject an approval, enforcing policy server-side.

        Policy (canonical package-6 rule): return and reject require a reason of
        at least ``_MIN_REASON_LEN`` non-whitespace characters.  The check runs
        here — never client-only — so an API caller cannot bypass it.
        """
        if idempotency_key and idempotency_key in self._idempotency:
            return {**_clone(self._idempotency[idempotency_key]), "idempotentReplay": True}

        normalized = action.strip().lower()
        if normalized not in DECISION_ACTIONS:
            raise GovernancePolicyError(
                f"decision action must be one of {', '.join(DECISION_ACTIONS)}; got {action!r}"
            )

        trimmed_reason = (reason or "").strip()
        if normalized in _REASON_REQUIRED and len(trimmed_reason) < _MIN_REASON_LEN:
            raise GovernancePolicyError(
                "退回或駁回理由需至少 10 個字 (return/reject requires a reason of at "
                "least 10 characters)"
            )

        # Growth approvals are owned by GrowthService — delegate so the linked
        # Growth Action advances too.  Return maps to a Growth rejection (the
        # action returns to DRAFT for revision).
        if self._growth is not None and self._growth_owns(approval_id):
            self._decide_growth(
                approval_id=approval_id,
                normalized=normalized,
                reason=trimmed_reason,
                actor_name=actor_name or role,
                correlation_id=correlation_id or "",
            )

        approval = self._find_local_approval(approval_id)
        if approval is not None:
            if self._require_canonical and approval.get("_canonical"):
                raise GovernanceConflict(
                    f"approval {approval_id} is owned by "
                    f"{approval.get('module', 'canonical domain')}; "
                    "use its canonical decision endpoint"
                )
            if approval["status"] != "pending":
                raise GovernanceConflict(
                    f"approval {approval_id} already decided: {approval['status']}"
                )
            approval["status"] = _APPROVAL_STATUS[normalized]
            approval["reason"] = trimmed_reason
        elif self._growth is None or not self._growth_owns(approval_id):
            raise GovernanceNotFound(f"approval {approval_id} not found")

        actor = actor_name or role
        decision = self._append_decision(
            approval=approval,
            approval_id=approval_id,
            action=normalized,
            reason=trimmed_reason,
            actor=actor,
        )
        self._append_audit(
            category="approval",
            actor=actor,
            action={"approve": "決策核准", "return": "決策退回", "reject": "決策駁回"}[normalized],
            module=(approval or {}).get("module", "Govern"),
            entity_ref=(approval or {}).get("entityRef", approval_id),
            summary=(
                f"核准中心審查決策：{(approval or {}).get('title', approval_id)}，"
                f"狀態變更為 {_FINAL_DECISION_LABEL[normalized]}。"
            ),
            reason=trimmed_reason,
            correlation_id=f"corr-{approval_id}",
        )

        result = {
            "approvalId": approval_id,
            "action": normalized,
            "finalDecision": _FINAL_DECISION_LABEL[normalized],
            "status": _APPROVAL_STATUS[normalized],
            "reason": trimmed_reason,
            "decision": decision,
            "correlation_id": correlation_id,
        }
        if idempotency_key:
            self._idempotency[idempotency_key] = result
        return result

    def _growth_owns(self, approval_id: str) -> bool:
        if self._growth is None:
            return False
        return any(item.get("id") == approval_id for item in self._growth.list_approvals())

    def _decide_growth(
        self,
        *,
        approval_id: str,
        normalized: str,
        reason: str,
        actor_name: str,
        correlation_id: str,
    ) -> None:
        # GrowthService only understands approved / rejected; treat "return" as
        # a rejection that sends the action back to DRAFT for revision.
        decision = "approved" if normalized == "approve" else "rejected"
        self._growth.resolve_approval(
            approval_id=approval_id,
            decision=decision,
            reason=reason,
            actor_name=actor_name,
            correlation_id=correlation_id,
        )

    def _find_local_approval(self, approval_id: str) -> dict[str, Any] | None:
        for row in self._state["approvals"]:
            if row["id"] == approval_id:
                return row
        return None

    def _append_decision(
        self,
        *,
        approval: dict[str, Any] | None,
        approval_id: str,
        action: str,
        reason: str,
        actor: str,
    ) -> dict[str, Any]:
        ordinal = self._state.get("nextDecisionOrdinal", 8900)
        module = (approval or {}).get("module", "Govern")
        entity = (approval or {}).get("entityRef", approval_id)
        title = (approval or {}).get("title", approval_id)
        entry = {
            "id": f"dec-{ordinal}",
            "module": module,
            "item": f"{entity} {title}".strip(),
            "systemRecommendation": (approval or {}).get("systemRecommendation", "—"),
            "finalDecision": _FINAL_DECISION_LABEL[action],
            "reason": reason or "符合風險與預算規範",
            "actor": actor,
            "decidedAt": _now_iso_minute(),
            "model": (
                (approval or {}).get("modelVersion")
                if self._require_canonical
                else _module_model(module)
            ),
            "datasetSnapshot": (
                (approval or {}).get("datasetSnapshot")
                if self._require_canonical
                else _module_dataset(module)
            ),
            "approvalId": approval_id,
        }
        self._state["decisions"].insert(0, entry)
        self._state["nextDecisionOrdinal"] = ordinal + 1
        return entry

    # ------------------------------------------------------------------
    # Write path — evidence package export
    # ------------------------------------------------------------------

    def export_evidence_package(
        self,
        *,
        date_from: str,
        date_to: str,
        modules: list[str],
        contents: list[str],
        fmt: str = "PDF",
        role: str = "營運主管",
        actor_name: str | None = None,
        retention_policy: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Produce an Evidence Package export record.

        Records — per task acceptance — scope (modules + contents), range
        (date_from/date_to), format, actor, correlation id and retention policy.
        Appends an Audit Trail event and a history entry.
        """
        if idempotency_key and idempotency_key in self._idempotency:
            return {**_clone(self._idempotency[idempotency_key]), "idempotentReplay": True}

        selected_modules = [m for m in modules if m] or ["全模組"]
        selected_contents = [c for c in contents if c] or ["Audit Trail", "Decision Log"]
        retention = retention_policy or "7 天簽章 URL，actor 欄位遮罩"
        actor = actor_name or role

        ordinal = self._state.get("nextEvidenceOrdinal", 3)
        file_ext = "zip" if fmt.upper().startswith("CSV") else "pdf"
        package_id = f"EVD-2026-0705-{ordinal:02d}"
        file_name = f"{package_id}.{file_ext}"
        scope_range = f"{date_from} – {date_to}"
        correlation = correlation_id or f"corr-exp-{ordinal:02d}"

        record = {
            "id": package_id,
            "file": file_name,
            "size": "4.2 MB",
            "range": scope_range,
            "scope": {
                "dateFrom": date_from,
                "dateTo": date_to,
                "modules": selected_modules,
                "contents": selected_contents,
            },
            "format": fmt,
            "actor": actor,
            "role": role,
            "correlationId": correlation,
            "retentionPolicy": retention,
            "generatedAt": _now_iso_minute(),
        }

        history_entry = {
            "id": package_id,
            "range": scope_range,
            "mod": "＋".join(selected_modules),
            "fmt": fmt,
            "t": record["generatedAt"],
            "by": actor,
        }
        self._state["evidencePackages"].insert(0, history_entry)
        self._state["nextEvidenceOrdinal"] = ordinal + 1

        self._append_audit(
            category="export",
            actor=actor,
            action="Export Evidence Package",
            module="Govern",
            entity_ref=package_id,
            summary=(
                f"匯出 Evidence Package：範圍 {scope_range} · 模組 "
                f"{'＋'.join(selected_modules)} · 格式 {fmt} · 內容 "
                f"{'／'.join(selected_contents)} · 保留策略 {retention}"
            ),
            reason=None,
            correlation_id=correlation,
        )

        result = {"package": record, "correlation_id": correlation}
        if idempotency_key:
            self._idempotency[idempotency_key] = result
        return result

    # ------------------------------------------------------------------
    # Audit helper
    # ------------------------------------------------------------------

    def _append_audit(
        self,
        *,
        category: str,
        actor: str,
        action: str,
        module: str,
        entity_ref: str,
        summary: str,
        reason: str | None,
        correlation_id: str,
    ) -> dict[str, Any]:
        ordinal = self._state.get("nextAuditOrdinal", 7200)
        row = {
            "id": f"aud-{ordinal}",
            "category": category,
            "timestamp": _now_iso_minute(),
            "actor": actor,
            "action": action,
            "module": module,
            "entityRef": entity_ref,
            "summary": summary,
            "correlationId": correlation_id,
        }
        if reason:
            row["reason"] = reason
        self._state["auditRows"].insert(0, row)
        self._state["nextAuditOrdinal"] = ordinal + 1
        return row


def _module_model(module: str) -> str:
    return {
        "Network": "sitescore-v4.8",
        "Growth": "PriceOps-v0.9",
        "Store Ops": "ops-risk-v2.2",
    }.get(module, "—")


def _module_dataset(module: str) -> str:
    return {
        "Network": "network-2026-W27",
        "Growth": "growth-2026-W27",
        "Store Ops": "ops-2026-W27",
    }.get(module, "—")


def _seed_state() -> dict[str, Any]:
    """Deterministic package-6 Govern seed.

    Includes Store Ops, Growth and Network pending approvals and Store/Growth/
    Network resolved decisions so the four navigation surfaces stay populated
    and consistent across reloads even before any live write.
    """
    approvals = [
        {
            "id": "ap-store-1042",
            "module": "Store Ops",
            "title": "Close escalated service issue",
            "requestor": "Store Ops Lead",
            "submittedAt": "2026-07-05 08:12",
            "status": "pending",
            "priority": "high",
            "owner": "營運主管",
            "sla": "42m",
            "entityRef": "ISS-1042",
            "summary": "Manager requests closure after staff resolution and customer callback.",
            "systemRecommendation": "Approve with customer follow-up audit retained.",
            "risk": "Customer-facing escalation",
            "roleNote": "營運主管 can decide after reviewing evidence package.",
            "evidence": [
                {"id": "ev-issue", "label": "Issue timeline", "type": "issue", "state": "ready"},
                {"id": "ev-call", "label": "Customer callback", "type": "note", "state": "ready"},
                {"id": "ev-photo", "label": "Counter photo", "type": "camera", "state": "ready"},
            ],
        },
        {
            "id": "ap-growth-2207",
            "module": "Growth",
            "title": "Schedule promo campaign",
            "requestor": "Growth Manager",
            "submittedAt": "2026-07-05 07:48",
            "status": "pending",
            "priority": "medium",
            "owner": "行銷經理",
            "sla": "2h 10m",
            "entityRef": "CMP-2207",
            "summary": "Campaign needs final governance approval before audience export.",
            "systemRecommendation": "Return unless audience mask proof is attached.",
            "risk": "Export and consent policy",
            "roleNote": "Return requires a reason for downstream Growth revision.",
            "evidence": [
                {"id": "ev-draft", "label": "Campaign draft", "type": "growth", "state": "ready"},
                {"id": "ev-mask", "label": "Masking proof", "type": "export", "state": "missing"},
            ],
        },
        {
            "id": "ap-network-3319",
            "module": "Network",
            "title": "Approve SiteScore override",
            "requestor": "Expansion Manager",
            "submittedAt": "2026-07-05 06:35",
            "status": "pending",
            "priority": "critical",
            "owner": "展店經理",
            "sla": "18m",
            "entityRef": "SITE-3319",
            "summary": "Team requests WAIT to GO override for a high-traffic corner candidate.",
            "systemRecommendation": "Reject override due to competitor density and lease risk.",
            "risk": "Model override",
            "roleNote": "展店經理 decision must include model and dataset snapshot context.",
            "evidence": [
                {"id": "ev-score", "label": "SiteScore v4.8", "type": "model", "state": "ready"},
                {"id": "ev-snapshot", "label": "Dataset 2026-W27", "type": "dataset", "state": "ready"},
                {"id": "ev-comp", "label": "Competitor scan", "type": "network", "state": "ready"},
            ],
        },
        {
            "id": "ap-govern-0903",
            "module": "Govern",
            "title": "Evidence package export",
            "requestor": "PM／稽核",
            "submittedAt": "2026-07-05 05:22",
            "status": "pending",
            "priority": "high",
            "owner": "PM／稽核",
            "sla": "1h 05m",
            "entityRef": "EXP-0903",
            "summary": "Auditor requests signed export for an external review packet.",
            "systemRecommendation": "Approve with seven-day retention and masked actor fields.",
            "risk": "Retention and signed URL policy",
            "roleNote": "PM／稽核 can approve export after retention policy review.",
            "evidence": [
                {"id": "ev-policy", "label": "Retention policy", "type": "system", "state": "ready"},
                {"id": "ev-mask-2", "label": "Actor masking", "type": "export", "state": "ready"},
                {"id": "ev-audit", "label": "Audit bundle", "type": "audit", "state": "ready"},
            ],
        },
    ]

    decisions = [
        {
            "id": "dec-8841",
            "module": "Store Ops",
            "item": "ISS-0994 resolution close",
            "systemRecommendation": "Approve",
            "finalDecision": "Approved",
            "reason": "Evidence package matched closure policy.",
            "actor": "營運主管",
            "decidedAt": "2026-07-05 04:51",
            "model": "ops-risk-v2.2",
            "datasetSnapshot": "ops-2026-W27",
            "approvalId": "ap-store-0994",
        },
        {
            "id": "dec-8840",
            "module": "Growth",
            "item": "CMP-2198 audience export",
            "systemRecommendation": "Return",
            "finalDecision": "Returned",
            "reason": "Audience masking proof was incomplete.",
            "actor": "PM／稽核",
            "decidedAt": "2026-07-04 19:18",
            "model": "campaign-guard-v1.9",
            "datasetSnapshot": "growth-2026-W27",
            "approvalId": "ap-growth-2198",
        },
        {
            "id": "dec-8839",
            "module": "Network",
            "item": "SITE-3308 WAIT override",
            "systemRecommendation": "Reject",
            "finalDecision": "Rejected",
            "reason": "Lease sensitivity exceeded override threshold.",
            "actor": "展店經理",
            "decidedAt": "2026-07-04 17:44",
            "model": "sitescore-v4.8",
            "datasetSnapshot": "network-2026-W27",
            "approvalId": "ap-network-3308",
        },
    ]

    audit_rows = [
        {
            "id": "aud-7101",
            "category": "approval",
            "timestamp": "2026-07-05 08:12",
            "actor": "Store Ops Lead",
            "action": "Approval requested",
            "module": "Store Ops",
            "entityRef": "ISS-1042",
            "summary": "Issue closure approval entered queue.",
            "correlationId": "corr-iss-1042",
        },
        {
            "id": "aud-7100",
            "category": "camera",
            "timestamp": "2026-07-05 08:08",
            "actor": "Camera service",
            "action": "Evidence attached",
            "module": "Store Ops",
            "entityRef": "ISS-1042",
            "summary": "Counter photo linked to closure packet.",
            "correlationId": "corr-iss-1042",
        },
        {
            "id": "aud-7099",
            "category": "growth",
            "timestamp": "2026-07-05 07:48",
            "actor": "Growth Manager",
            "action": "Campaign submitted",
            "module": "Growth",
            "entityRef": "CMP-2207",
            "summary": "Promo campaign submitted for governance review.",
            "correlationId": "corr-cmp-2207",
        },
        {
            "id": "aud-7098",
            "category": "network",
            "timestamp": "2026-07-05 06:35",
            "actor": "Expansion Manager",
            "action": "Override requested",
            "module": "Network",
            "entityRef": "SITE-3319",
            "summary": "SiteScore WAIT to GO override requested.",
            "correlationId": "corr-site-3319",
        },
        {
            "id": "aud-7097",
            "category": "export",
            "timestamp": "2026-07-05 05:22",
            "actor": "PM／稽核",
            "action": "Export approval requested",
            "module": "Govern",
            "entityRef": "EXP-0903",
            "summary": "Evidence Package export queued for approval.",
            "correlationId": "corr-exp-0903",
        },
        {
            "id": "aud-7096",
            "category": "system",
            "timestamp": "2026-07-05 05:10",
            "actor": "Policy engine",
            "action": "Retention rule evaluated",
            "module": "Govern",
            "entityRef": "EXP-0903",
            "summary": "Seven-day signed URL retention selected.",
            "correlationId": "corr-exp-0903",
        },
    ]

    status_board = {
        "dataQuality": [
            {"source": "Google Reviews Connector", "status": "正常", "good": True, "note": "15 分鐘前同步 · 覆蓋 12/12 門市"},
            {"source": "Camera Events", "status": "延遲", "good": False, "note": "事件延遲 12 分鐘 · 影響即時性"},
            {"source": "POS／支付交易", "status": "正常", "good": True, "note": "即時串流 · 缺漏 0.2%"},
            {"source": "591 物件源", "status": "正常", "good": True, "note": "每日 06:00 匯入 · 昨日新增 14 筆"},
            {"source": "IoT 心跳", "status": "注意", "good": False, "note": "1 台設備 >3h 未回報（ISS-1021）"},
        ],
        "models": [
            {"name": "SiteScore", "version": "v2.3", "status": "上線", "good": True, "note": "選址評分 · 每週再訓練 · 用於 Network"},
            {"name": "CS Intent", "version": "v1.8", "status": "上線", "good": True, "note": "客服意圖分類 · 準確率 91%"},
            {"name": "PriceOps", "version": "v0.9", "status": "Shadow", "good": False, "note": "動態定價 · 影子模式驗證中"},
            {"name": "Camera Event", "version": "v1.2", "status": "上線", "good": True, "note": "場域事件偵測 · 不含人臉"},
        ],
        "connectors": [
            {"name": "Google Business Profile", "status": "已連接", "good": True, "note": "評價／回覆 API"},
            {"name": "LINE 官方帳號", "status": "已連接", "good": True, "note": "客服＋推播"},
            {"name": "591 租屋網", "status": "已連接", "good": True, "note": "每日物件匯入"},
            {"name": "TapPay 金流閘道", "status": "已連接", "good": True, "note": "交易／退款 webhook"},
        ],
        "sla": [
            {"name": "核准處理 SLA", "status": "達成", "good": True, "note": "P95 42m · 目標 <2h"},
            {"name": "事件升級 SLA", "status": "達成", "good": True, "note": "橘/紅燈 30m 內升級"},
            {"name": "證據匯出 SLA", "status": "注意", "good": False, "note": "1 筆匯出等待簽章 >1h"},
        ],
        "users": [
            {"name": "營運主管", "status": "啟用", "good": True, "note": "全域監控、跨域指派與核准"},
            {"name": "PM／稽核", "status": "啟用", "good": True, "note": "模型、決策追蹤與稽核線索"},
            {"name": "行銷經理", "status": "啟用", "good": True, "note": "活動、分群、定價建議"},
            {"name": "展店經理", "status": "啟用", "good": True, "note": "HeatZone、候選點與 SiteScore"},
        ],
        "runbooks": [
            {"name": "災備演練 (Disaster Recovery)", "status": "正常", "good": True, "note": "Completed 2026-07-01 · 復原時間 18m"},
            {"name": "資料備份與還原 (Backup & Restore)", "status": "正常", "good": True, "note": "每日 03:00 自動備份 · 驗證成功"},
            {"name": "事件管理與升級 (Incident Management)", "status": "運作中", "good": True, "note": "SLA 升級規則已啟用 · 監控端點正常"},
            {"name": "系統觀測性 (Observability)", "status": "正常", "good": True, "note": "Prometheus/Grafana 指標正常 · 心跳正常"},
        ],
    }

    evidence_packages = [
        {"id": "EVD-2026-0701-01", "range": "2026-06-01 – 2026-06-30", "mod": "Store Ops＋Growth＋Network", "fmt": "PDF", "t": "2026-07-01 10:15", "by": "周明德"},
        {"id": "EVD-2026-0615-02", "range": "2026-05-01 – 2026-05-31", "mod": "Store Ops＋Network", "fmt": "CSV", "t": "2026-06-15 14:22", "by": "周明德"},
    ]

    return {
        "approvals": approvals,
        "decisions": decisions,
        "auditRows": audit_rows,
        "statusBoard": status_board,
        "evidencePackages": evidence_packages,
        "nextDecisionOrdinal": 8900,
        "nextAuditOrdinal": 7200,
        "nextEvidenceOrdinal": 3,
    }


def _empty_state() -> dict[str, Any]:
    return {
        "approvals": [],
        "decisions": [],
        "auditRows": [],
        "statusBoard": [],
        "evidencePackages": [],
        "nextDecisionOrdinal": 1,
        "nextAuditOrdinal": 1,
        "nextEvidenceOrdinal": 1,
        "idempotency": {},
    }
