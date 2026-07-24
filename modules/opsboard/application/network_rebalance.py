"""Network rebalance workflow service for Operator Console R4.

Owns the task-scoped low-efficiency store rebalance state used by
``/api/v1/operator/network-rebalance``:

- AVM job request and service-produced valuation results.
- NetPlan three-scenario solve with model/snapshot metadata.
- Scenario selection with persisted evidence and owner.
- Govern approval creation boundary without marking relocation executed.

The service is deliberately in-memory for the Operator Console R4 product
slice. It follows the existing NetworkListingService pattern: deterministic
seed data, idempotent writes, and one service instance per API router lifetime.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


class NetworkRebalanceError(RuntimeError):
    """Base network rebalance service error."""


class NetworkRebalanceNotFound(NetworkRebalanceError):
    """Raised when a rebalance store or scenario id is unknown."""


class NetworkRebalanceConflict(NetworkRebalanceError):
    """Raised when a workflow mutation is invalid for current state."""


class NetworkRebalancePolicyError(NetworkRebalanceError):
    """Raised when an audited policy requirement is missing."""


class NetworkRebalanceRuntimeUnavailable(NetworkRebalanceError):
    """Raised when model/runtime execution is unavailable and retryable."""

    def __init__(self, *, model: str, store_id: str, retry_after_seconds: int = 300) -> None:
        self.model = model
        self.store_id = store_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{model} runtime unavailable for {store_id}; retryable")

    def to_detail(self) -> dict[str, Any]:
        return {
            "state": "retryable_unavailable",
            "retryable": True,
            "model": self.model,
            "storeId": self.store_id,
            "retryAfterSeconds": self.retry_after_seconds,
            "message": str(self),
        }


GovernApprovalWriter = Callable[[dict[str, Any]], dict[str, Any]]

_STATUS_LABELS = {
    "watching": "重配候選",
    "avmrequested": "AVM 估值中",
    "avmready": "AVM Ready",
    "netplanreview": "NetPlan 三案",
    "pendingapproval": "審核中",
    "approved": "已核准",
    "closed": "結案",
}

_AVM_MODEL = {
    "modelVersion": "avm-rebalance-income-market-v1.0.0",
    "snapshotId": "AVM-SNAP-20260714-0600",
    "featureSnapshotTime": "2026-07-14T06:00:00Z",
}

_NETPLAN_MODEL = {
    "modelVersion": "netplan-rebalance-three-case-v1.0.0",
    "snapshotId": "NP-SNAP-20260714-0615",
    "solverVersion": "netplan-exhaustive-cpsat-compatible-v1",
    "featureSnapshotTime": "2026-07-14T06:15:00Z",
}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _audit_id(prefix: str = "REB") -> str:
    return f"AUD-{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _evidence_id(prefix: str = "EV-RB") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _seed_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "id": "keep",
            "name": "Keep / Improve",
            "roi": "ROI 8%（改善後）",
            "roiPct": 0.08,
            "score": 42,
            "inv": "NT$450K（設備更新＋在地行銷）",
            "investmentTwd": 450000,
            "payback": "18 個月（增量）",
            "risk": "中",
            "time": "即刻起 90 天",
            "isSystemRecommendation": False,
            "rationale": "保留原址但需重新配置設備、在地行銷與 90 天營運觀察。",
        },
        {
            "id": "move",
            "name": "Move (移轉新址)",
            "roi": "ROI 18%（新址預估）",
            "roiPct": 0.18,
            "score": 71,
            "inv": "NT$1.9M（移轉＋裝修）",
            "investmentTwd": 1900000,
            "payback": "26 個月",
            "risk": "中高",
            "time": "Q3–Q4 執行",
            "isSystemRecommendation": True,
            "rationale": "Move 方案在需求缺口與租金帶權衡下最高分，但仍需 Govern 雙簽核。",
        },
        {
            "id": "exit",
            "name": "Exit (關店止損)",
            "roi": "年省 NT$1.1M",
            "roiPct": 0.0,
            "score": 55,
            "inv": "解約金 NT$180K＋設備移撥",
            "investmentTwd": 180000,
            "payback": "—",
            "risk": "低",
            "time": "60 天內",
            "isSystemRecommendation": False,
            "rationale": "止損風險最低，但會留下商圈需求缺口與設備調度成本。",
        },
    ]


def _seed_state() -> dict[str, Any]:
    return {
        "stores": [
            {
                "id": "RB-801",
                "storeId": "ST-021",
                "storeName": "新北板橋文化",
                "status": "watching",
                "ownerRoleId": "expansionManager",
                "ownerName": "王若寧",
                "summary": "連續 90 天紅燈，低利用率與租金壓力觸發 AVM／NetPlan 重配評估。",
                "healthNote": "連續 90 天紅燈 · 重配候選",
                "monthlyRevenueLabel": "NT$292K／月",
                "monthlyRevenueTwd": 292000,
                "utilizationLabel": "31%",
                "utilizationPct": 31,
                "sourceIssueId": "ISS-0992",
                "lightHistory": ["R", "R", "R", "R", "R", "R", "R", "R"],
                "trend": [58, 54, 50, 48, 45, 43, 40, 38, 36, 34, 32, 31],
                "evidence": [
                    {
                        "id": "EV-RB-801-90D",
                        "kind": "forecastops",
                        "label": "90 天紅燈與營收趨勢",
                        "source": "ForecastOps snapshot FS-20260714-0600",
                    },
                    {
                        "id": "EV-RB-801-UTIL",
                        "kind": "operations",
                        "label": "設備利用率 31%",
                        "source": "OpsBoard store-machine snapshot",
                    },
                ],
                "relocationExecuted": False,
                "executionBoundary": "Relocation cannot execute until Govern approval is approved and an execution plan is created.",
                "runtimeState": None,
            }
        ],
        "auditEvents": [],
        "governApprovals": [],
    }


class NetworkRebalanceService:
    """Application service for the R4 low-efficiency rebalance workflow."""

    def __init__(
        self,
        govern_approval_writer: GovernApprovalWriter | None = None,
        *,
        initial_state: dict[str, Any] | None = None,
        seed_fixtures: bool = True,
    ) -> None:
        self._seed_fixtures = seed_fixtures
        self._state = _copy(
            initial_state
            if initial_state is not None
            else _seed_state()
            if seed_fixtures
            else {
                "stores": [],
                "auditEvents": [],
                "governApprovals": [],
            }
        )
        self._idempotency_cache = _copy(
            (initial_state or {}).get("idempotencyCache", {})
        )
        self._state.pop("idempotencyCache", None)
        self._govern_approval_writer = govern_approval_writer

    def reset(self) -> dict[str, Any]:
        self._state = (
            _seed_state()
            if self._seed_fixtures
            else {
                "stores": [],
                "auditEvents": [],
                "governApprovals": [],
            }
        )
        self._idempotency_cache = {}
        return self.snapshot()

    def export_state(self) -> dict[str, Any]:
        return {
            **_copy(self._state),
            "idempotencyCache": _copy(self._idempotency_cache),
        }

    def snapshot(
        self,
        *,
        selected_store_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        selected_id = selected_store_id or (
            self._state["stores"][0]["id"] if self._state["stores"] else None
        )
        return {
            "source": "api",
            "stores": [_copy(self._view_store(store)) for store in self._state["stores"]],
            "selectedStoreId": selected_id,
            "metadata": {
                "serviceVersion": "operator-network-rebalance-r4",
                "canonicalPackage": "r4-20260707-package-6",
                "canonicalZipSha256": "db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76",
                "screenLabels": ["Network 展店與店網", "Network 低效重配", "Govern 治理稽核"],
                "avm": _copy(_AVM_MODEL),
                "netPlan": _copy(_NETPLAN_MODEL),
            },
            "governApprovals": _copy(self._state["governApprovals"]),
            "auditEvents": _copy(self._state["auditEvents"]),
            "counts": {
                "stores": len(self._state["stores"]),
                "pendingApprovals": sum(1 for store in self._state["stores"] if store.get("status") == "pendingapproval"),
            },
            "correlationId": correlation_id,
        }

    def request_avm(
        self,
        *,
        store_id: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        simulate_unavailable: bool = False,
    ) -> dict[str, Any]:
        cache_key = ("request_avm", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        store = self._store(store_id)
        if simulate_unavailable:
            self._record_runtime_unavailable(store, model="AVM")
            raise NetworkRebalanceRuntimeUnavailable(model="AVM", store_id=store_id)
        if store["status"] not in {"watching", "avmrequested"}:
            raise NetworkRebalanceConflict(f"{store_id} is already past AVM request")

        store["status"] = "avmrequested"
        store["avmRequestId"] = store.get("avmRequestId") or "AVM-611"
        store["avmJob"] = {
            "id": store["avmRequestId"],
            "status": "queued",
            "requestedAt": store.get("avmJob", {}).get("requestedAt") or _now(),
            "modelVersion": _AVM_MODEL["modelVersion"],
            "snapshotId": _AVM_MODEL["snapshotId"],
            "retryable": True,
        }
        store["runtimeState"] = None
        audit = self._audit(
            action="rebalance.avm.requested",
            target_id=store_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={"avmRequestId": store["avmRequestId"], **_AVM_MODEL},
        )
        result = {"store": _copy(self._view_store(store)), "auditEvent": audit, "correlationId": correlation_id}
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def complete_avm(
        self,
        *,
        store_id: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        simulate_unavailable: bool = False,
    ) -> dict[str, Any]:
        cache_key = ("complete_avm", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        store = self._store(store_id)
        if simulate_unavailable:
            self._record_runtime_unavailable(store, model="AVM")
            raise NetworkRebalanceRuntimeUnavailable(model="AVM", store_id=store_id)
        if store["status"] != "avmrequested":
            raise NetworkRebalanceConflict(f"{store_id} must be avmrequested before AVM completion")

        evidence_id = _evidence_id("EV-AVM")
        store["status"] = "avmready"
        store["avmJob"] = {
            **store.get("avmJob", {"id": "AVM-611"}),
            "status": "completed",
            "completedAt": _now(),
        }
        store["avm"] = {
            "requestId": store.get("avmRequestId", "AVM-611"),
            "p10": 2340000,
            "p50": 2860000,
            "p90": 3420000,
            "confidence": "中高（收益法＋市場比較）",
            "reserve": "保留價：待房東議價（服務估值）",
            "evidenceId": evidence_id,
            **_AVM_MODEL,
        }
        store.setdefault("evidence", []).append(
            {
                "id": evidence_id,
                "kind": "avm",
                "label": "AVM service valuation P10/P50/P90",
                "source": f"{_AVM_MODEL['modelVersion']} · {_AVM_MODEL['snapshotId']}",
            }
        )
        store["runtimeState"] = None
        audit = self._audit(
            action="rebalance.avm.completed",
            target_id=store_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={"evidenceId": evidence_id, "p50": store["avm"]["p50"], **_AVM_MODEL},
        )
        result = {"store": _copy(self._view_store(store)), "auditEvent": audit, "correlationId": correlation_id}
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def solve_netplan(
        self,
        *,
        store_id: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        simulate_unavailable: bool = False,
    ) -> dict[str, Any]:
        cache_key = ("solve_netplan", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        store = self._store(store_id)
        if simulate_unavailable:
            self._record_runtime_unavailable(store, model="NetPlan")
            raise NetworkRebalanceRuntimeUnavailable(model="NetPlan", store_id=store_id)
        if store["status"] != "avmready":
            raise NetworkRebalanceConflict(f"{store_id} must be avmready before NetPlan solve")

        evidence_id = _evidence_id("EV-NP")
        scenarios = []
        for scenario in _seed_scenarios():
            scenarios.append(
                {
                    **scenario,
                    "modelVersion": _NETPLAN_MODEL["modelVersion"],
                    "snapshotId": _NETPLAN_MODEL["snapshotId"],
                    "solverVersion": _NETPLAN_MODEL["solverVersion"],
                    "evidenceIds": [evidence_id],
                    "selected": False,
                }
            )
        store["status"] = "netplanreview"
        store["netPlanJob"] = {
            "id": "NP-801",
            "status": "solved",
            "completedAt": _now(),
            **_NETPLAN_MODEL,
        }
        store["netPlanScenarios"] = scenarios
        store.setdefault("evidence", []).append(
            {
                "id": evidence_id,
                "kind": "netplan",
                "label": "NetPlan three-case solver output",
                "source": f"{_NETPLAN_MODEL['modelVersion']} · {_NETPLAN_MODEL['snapshotId']}",
            }
        )
        store["runtimeState"] = None
        audit = self._audit(
            action="rebalance.netplan.solved",
            target_id=store_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={"evidenceId": evidence_id, "scenarioCount": len(scenarios), **_NETPLAN_MODEL},
        )
        result = {"store": _copy(self._view_store(store)), "auditEvent": audit, "correlationId": correlation_id}
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def select_scenario(
        self,
        *,
        store_id: str,
        scenario_id: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        cache_key = ("select_scenario", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        store = self._store(store_id)
        if store["status"] != "netplanreview":
            raise NetworkRebalanceConflict(f"{store_id} must be in NetPlan review before scenario selection")
        scenario = self._scenario(store, scenario_id)
        evidence_id = _evidence_id("EV-SEL")
        for item in store.get("netPlanScenarios", []):
            item["selected"] = item.get("id") == scenario_id
        store["selectedScenarioId"] = scenario_id
        store["netPlanOptionId"] = f"NPO-{scenario_id.upper()}"
        store["selectedScenarioOwner"] = {
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "selectedAt": _now(),
        }
        store["selectedScenarioEvidenceId"] = evidence_id
        store.setdefault("evidence", []).append(
            {
                "id": evidence_id,
                "kind": "netplan-selection",
                "label": f"Selected scenario: {scenario['name']}",
                "source": f"{actor_name or actor_role_id} · {_NETPLAN_MODEL['snapshotId']}",
            }
        )
        audit = self._audit(
            action="rebalance.scenario.selected",
            target_id=store_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "scenarioId": scenario_id,
                "scenarioName": scenario["name"],
                "evidenceId": evidence_id,
                **_NETPLAN_MODEL,
            },
        )
        result = {"store": _copy(self._view_store(store)), "auditEvent": audit, "correlationId": correlation_id}
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def submit_review(
        self,
        *,
        store_id: str,
        reason: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if not reason:
            raise NetworkRebalancePolicyError("submit review reason is required")

        cache_key = ("submit_review", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        store = self._store(store_id)
        if store["status"] != "netplanreview":
            raise NetworkRebalanceConflict(f"{store_id} must be in NetPlan review before submission")
        if not store.get("selectedScenarioId"):
            raise NetworkRebalancePolicyError("selected scenario is required before submission")

        scenario = self._scenario(store, str(store["selectedScenarioId"]))
        approval_id = store.get("relatedApprovalId") or f"APR-NET-{store_id}"
        approval = {
            "id": approval_id,
            "module": "Network",
            "kind": "netplan",
            "ref": store_id,
            "title": f"NetPlan 重配審核：{store['storeName']}（{scenario['name']}）",
            "meta": (
                f"{scenario['name']} score {scenario['score']} · "
                f"{_NETPLAN_MODEL['modelVersion']} · {_NETPLAN_MODEL['snapshotId']}"
            ),
            "status": "pending",
            "cta": "Review",
            "tone": "warning",
            "risk": "高",
            "requestedByRoleId": actor_role_id,
            "requestedBy": actor_name or "Expansion Manager",
            "requiredRoleIds": ["opsLead", "auditPm"],
            "evidenceIds": [
                str(store.get("avm", {}).get("evidenceId", "")),
                *list(scenario.get("evidenceIds", [])),
                str(store.get("selectedScenarioEvidenceId", "")),
            ],
            "reason": reason,
            "target": {"workspace": "govern", "entityId": approval_id, "tab": "approvals"},
        }
        approval["evidenceIds"] = [item for item in approval["evidenceIds"] if item]

        written_approval = (
            self._govern_approval_writer(_copy(approval))
            if self._govern_approval_writer is not None
            else _copy(approval)
        )
        self._upsert_local_approval(written_approval)

        store["status"] = "pendingapproval"
        store["relatedApprovalId"] = approval_id
        store["approvalStatus"] = "pending"
        store["relocationExecuted"] = False
        store["executionBoundary"] = "Govern approval was created; relocation remains unexecuted until a later approved execution plan."
        audit = self._audit(
            action="rebalance.review.submitted",
            target_id=store_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "approvalId": approval_id,
                "selectedScenarioId": store["selectedScenarioId"],
                "relocationExecuted": False,
            },
        )
        result = {
            "store": _copy(self._view_store(store)),
            "governApproval": _copy(written_approval),
            "auditEvent": audit,
            "executionBoundary": {
                "relocationExecuted": False,
                "message": store["executionBoundary"],
            },
            "correlationId": correlation_id,
        }
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def _store(self, store_id: str) -> dict[str, Any]:
        for store in self._state["stores"]:
            if store.get("id") == store_id or store.get("storeId") == store_id:
                return store
        raise NetworkRebalanceNotFound(f"rebalance store {store_id} not found")

    def _scenario(self, store: dict[str, Any], scenario_id: str) -> dict[str, Any]:
        for scenario in store.get("netPlanScenarios", []):
            if scenario.get("id") == scenario_id:
                return scenario
        raise NetworkRebalanceNotFound(f"scenario {scenario_id} not found for {store.get('id')}")

    def _record_runtime_unavailable(self, store: dict[str, Any], *, model: str) -> None:
        store["runtimeState"] = {
            "state": "retryable_unavailable",
            "model": model,
            "retryable": True,
            "retryAfterSeconds": 300,
            "recordedAt": _now(),
        }

    def _view_store(self, store: dict[str, Any]) -> dict[str, Any]:
        avm = store.get("avm") or {}
        scenarios = []
        for scenario in store.get("netPlanScenarios", []):
            scenarios.append(
                {
                    **_copy(scenario),
                    "selected": scenario.get("id") == store.get("selectedScenarioId"),
                }
            )
        return {
            **_copy(store),
            "statusLabel": _STATUS_LABELS.get(str(store.get("status")), str(store.get("status"))),
            "avmP10": avm.get("p10"),
            "avmP50": avm.get("p50"),
            "avmP90": avm.get("p90"),
            "avmConf": avm.get("confidence"),
            "avmReserve": avm.get("reserve"),
            "avmModelVersion": avm.get("modelVersion"),
            "avmSnapshotId": avm.get("snapshotId"),
            "avmEvidenceId": avm.get("evidenceId"),
            "netPlanScenarios": scenarios,
            "netPlanModelVersion": _NETPLAN_MODEL["modelVersion"] if scenarios else None,
            "netPlanSnapshotId": _NETPLAN_MODEL["snapshotId"] if scenarios else None,
        }

    def _upsert_local_approval(self, approval: dict[str, Any]) -> None:
        approvals = self._state.setdefault("governApprovals", [])
        for index, existing in enumerate(approvals):
            if existing.get("id") == approval.get("id"):
                approvals[index] = _copy(approval)
                return
        approvals.insert(0, _copy(approval))

    def _audit(
        self,
        *,
        action: str,
        target_id: str,
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "id": _audit_id(),
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "category": "workflow",
            "action": action,
            "targetType": "rebalanceStore",
            "targetId": target_id,
            "message": f"{action} recorded for {target_id}",
            "correlationId": correlation_id,
            "metadata": metadata,
        }
        self._state["auditEvents"].insert(0, event)
        return _copy(event)


__all__ = [
    "NetworkRebalanceConflict",
    "NetworkRebalanceNotFound",
    "NetworkRebalancePolicyError",
    "NetworkRebalanceRuntimeUnavailable",
    "NetworkRebalanceService",
]
