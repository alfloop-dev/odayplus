"""Network rebalance workflow service for Operator Console R4.

Owns the task-scoped low-efficiency store rebalance state used by
``/api/v1/operator/network-rebalance``:

- AVM job request and service-produced valuation results.
- NetPlan three-scenario solve with model/snapshot metadata.
- Scenario selection with persisted evidence and owner.
- Govern approval creation boundary without marking relocation executed.

Local/test mode retains deterministic fixture data. Production mode discovers
tenant AVM cases and NetPlan scenarios from canonical durable repositories,
then delegates valuation and optimization to their application services.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.avm.application.valuation import AVMError, AVMService
from modules.netplan.application.planning import (
    NetPlanApprovalError,
    NetPlanConstraintDisclosureError,
    NetPlanService,
)
from modules.netplan.domain import InvalidNetPlanTransitionError, NetPlanScenarioStatus
from shared.governance.decision_policy import (
    DecisionPolicy,
    DecisionPolicyRepository,
    PolicyResolutionError,
    resolve_policy,
)
from shared.governance.netplan_disclosure import (
    NETPLAN_DISCLOSURE_POLICY_KIND,
    DisclosureEvaluation,
    NetPlanDisclosurePolicyError,
    authorized_roles,
    evaluate_disclosure,
)
from solver.netplan import ConstraintClass


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


@dataclass(frozen=True)
class _CanonicalRowBinding:
    """One Operator row reconciled against the canonical candidate it names.

    Every field here is read off the durable solve, never off the projection
    row. A caller that goes on to write a Govern approval therefore quotes what
    the solver produced rather than what the console handed back, so a tampered
    or stale projection cannot be laundered into the approval record by passing
    a reconciliation that only checked the row's identifiers.
    """

    scenario: Any
    solve: Any
    candidate_id: str
    modelled_classes: list[str]
    unmodelled_classes: list[str]
    actions: tuple[Any, ...]
    action_signature: tuple[tuple[str, str], ...]

    def action_payload(self) -> list[dict[str, Any]]:
        return [action.to_dict() for action in self.actions]


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
    # The fixture scenarios are not the output of a solve. They stand in for one
    # whose caller supplied only ``max_budget``, which is the single cap the
    # solver requires, so CAPITAL is the only class they may claim to have
    # bound. Widening this list would make the fixture Operator surface report
    # five classes as validated that nothing validated -- the exact misreading
    # ODP-FR-NET-002 disclosure exists to prevent -- and it is not made true by
    # the fact that it would let the fixture submit path reach Govern.
    default_modelled = ["CAPITAL"]
    default_unmodelled = [
        "LEASE",
        "CONSTRUCTION",
        "EQUIPMENT",
        "LABOUR",
        "COVERAGE",
        "DILUTION",
        "SEQUENCING",
    ]
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
            "isStale": False,
            "isInfeasible": False,
            "diagnostics": [],
            "bindingConstraints": ["max_budget"],
            "modelledConstraintClasses": list(default_modelled),
            "unmodelledConstraintClasses": list(default_unmodelled),
            "modelled_constraint_classes": list(default_modelled),
            "unmodelled_constraint_classes": list(default_unmodelled),
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
            "isStale": False,
            "isInfeasible": False,
            "diagnostics": [],
            "bindingConstraints": ["max_budget"],
            "modelledConstraintClasses": list(default_modelled),
            "unmodelledConstraintClasses": list(default_unmodelled),
            "modelled_constraint_classes": list(default_modelled),
            "unmodelled_constraint_classes": list(default_unmodelled),
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
            "isStale": False,
            "isInfeasible": False,
            "diagnostics": [],
            "bindingConstraints": [],
            "modelledConstraintClasses": list(default_modelled),
            "unmodelledConstraintClasses": list(default_unmodelled),
            "modelled_constraint_classes": list(default_modelled),
            "unmodelled_constraint_classes": list(default_unmodelled),
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
        avm_repository: Any | None = None,
        netplan_repository: Any | None = None,
        avm_production_executor: Any | None = None,
        netplan_production_executor: Any | None = None,
        netplan_policy_repository: DecisionPolicyRepository | None = None,
        netplan_approval_verifier: Any | None = None,
        runtime_mode: str | None = None,
        tenant_id: str | None = None,
        require_canonical: bool = False,
    ) -> None:
        self._seed_fixtures = seed_fixtures
        self._avm_repository = avm_repository
        self._netplan_repository = netplan_repository
        self._avm_production_executor = avm_production_executor
        self._netplan_production_executor = netplan_production_executor
        # Deliberately not defaulted to the seeded v1 policy. A submit path that
        # falls back to built-in rules when the registry is absent cannot say
        # which policy version let a plan through, which ODP-AC-BR-004 requires
        # of every historical approval. `_require_disclosure_policy` turns the
        # absence into a refusal at the submission boundary instead.
        self._netplan_policy_repository = netplan_policy_repository
        # Public, like `NetPlanService.approval_verifier`: the management
        # approval authority is injected by composition and may be installed
        # after the service is built, but it is never derived from a request.
        self.netplan_approval_verifier = netplan_approval_verifier
        self._runtime_mode = runtime_mode
        self._tenant_id = tenant_id
        self._require_canonical = require_canonical
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
        self._idempotency_cache = _copy((initial_state or {}).get("idempotencyCache", {}))
        self._state.pop("idempotencyCache", None)
        self._govern_approval_writer = govern_approval_writer
        if self._require_canonical:
            self._refresh_canonical_stores()

    def reset(self) -> dict[str, Any]:
        if self._require_canonical:
            self._state["auditEvents"] = []
            self._state["governApprovals"] = []
            self._idempotency_cache = {}
            self._refresh_canonical_stores()
            return self.snapshot()
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
        if self._require_canonical:
            self._refresh_canonical_stores()
        selected_id = selected_store_id or (
            self._state["stores"][0]["id"] if self._state["stores"] else None
        )
        return {
            "source": "canonical" if self._require_canonical else "api",
            "stores": [_copy(self._view_store(store)) for store in self._state["stores"]],
            "selectedStoreId": selected_id,
            "metadata": {
                "serviceVersion": "operator-network-rebalance-r4",
                "canonicalPackage": None if self._require_canonical else "r4-20260707-package-6",
                "canonicalZipSha256": None
                if self._require_canonical
                else "db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76",
                "screenLabels": ["Network 展店與店網", "Network 低效重配", "Govern 治理稽核"],
                "avm": self._canonical_avm_metadata(),
                "netPlan": self._canonical_netplan_metadata(),
            },
            "governApprovals": _copy(self._state["governApprovals"]),
            "auditEvents": _copy(self._state["auditEvents"]),
            "counts": {
                "stores": len(self._state["stores"]),
                "pendingApprovals": sum(
                    1 for store in self._state["stores"] if store.get("status") == "pendingapproval"
                ),
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
        if self._require_canonical:
            case_id = store.get("canonicalAvmCaseId")
            if not case_id:
                raise NetworkRebalanceRuntimeUnavailable(
                    model="AVM input",
                    store_id=store_id,
                )
            case = self._avm_repository.get_case(case_id)
            if case is None:
                raise NetworkRebalanceRuntimeUnavailable(
                    model="AVM input",
                    store_id=store_id,
                )
            store["avmRequestId"] = case.case_id
            store["avmJob"] = {
                "id": case.case_id,
                "status": case.status.value,
                "requestedAt": case.created_at.isoformat(),
                "sourceSnapshotIds": list(case.valuation_input.source_snapshot_ids),
                "retryable": True,
            }
            store["runtimeState"] = None
            audit = self._audit(
                action="rebalance.avm.requested",
                target_id=store_id,
                actor_role_id=actor_role_id,
                actor_name=actor_name,
                correlation_id=correlation_id,
                metadata={
                    "avmRequestId": case.case_id,
                    "sourceSnapshotIds": list(case.valuation_input.source_snapshot_ids),
                },
            )
            result = {
                "store": _copy(self._view_store(store)),
                "auditEvent": audit,
                "correlationId": correlation_id,
            }
            if idempotency_key:
                self._idempotency_cache[cache_key] = _copy(result)
            return result

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
        result = {
            "store": _copy(self._view_store(store)),
            "auditEvent": audit,
            "correlationId": correlation_id,
        }
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

        if self._require_canonical:
            case_id = store.get("canonicalAvmCaseId")
            if not case_id:
                raise NetworkRebalanceRuntimeUnavailable(
                    model="AVM input",
                    store_id=store_id,
                )
            try:
                report = AVMService(
                    repository=self._avm_repository,
                    production_executor=self._avm_production_executor,
                    runtime_mode=self._runtime_mode,
                ).value(
                    case_id,
                    actor=actor_name or actor_role_id,
                    correlation_id=correlation_id or "",
                )
            except AVMError as exc:
                raise NetworkRebalanceConflict(str(exc)) from exc
            store["status"] = "avmready"
            store["avmJob"] = {
                "id": report.case_id,
                "status": "completed",
                "completedAt": report.valued_at.isoformat(),
                "reportId": report.report_id,
                "valuationVersion": report.valuation_version,
            }
            store["avm"] = {
                "requestId": report.case_id,
                "reportId": report.report_id,
                "valuationVersion": report.valuation_version,
                "p10": report.fair_price.p10,
                "p50": report.fair_price.p50,
                "p90": report.fair_price.p90,
                "confidence": report.confidence,
                "reservePrice": report.reserve_price,
                "modelVersion": report.model_version,
                "featureVersion": report.feature_version,
                "predictionOriginTime": report.prediction_origin_time.isoformat(),
                "valuedAt": report.valued_at.isoformat(),
            }
            store["runtimeState"] = None
            audit = self._audit(
                action="rebalance.avm.completed",
                target_id=store_id,
                actor_role_id=actor_role_id,
                actor_name=actor_name,
                correlation_id=correlation_id,
                metadata={
                    "reportId": report.report_id,
                    "valuationVersion": report.valuation_version,
                    "p50": report.fair_price.p50,
                    "modelVersion": report.model_version,
                },
            )
            result = {
                "store": _copy(self._view_store(store)),
                "auditEvent": audit,
                "correlationId": correlation_id,
            }
            if idempotency_key:
                self._idempotency_cache[cache_key] = _copy(result)
            return result

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
        result = {
            "store": _copy(self._view_store(store)),
            "auditEvent": audit,
            "correlationId": correlation_id,
        }
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

        if self._require_canonical:
            scenario_ids = list(store.get("canonicalNetPlanScenarioIds", []))
            scenarios = [
                self._netplan_repository.get_scenario(scenario_id) for scenario_id in scenario_ids
            ]
            scenario = next(
                (item for item in scenarios if item is not None and item.status.value == "draft"),
                None,
            )
            if scenario is None:
                raise NetworkRebalanceRuntimeUnavailable(
                    model="NetPlan scenario input",
                    store_id=store_id,
                )
            solve = NetPlanService(
                repository=self._netplan_repository,
                production_executor=self._netplan_production_executor,
                runtime_mode=self._runtime_mode,
            ).solve(
                scenario.scenario_id,
                actor=actor_name or actor_role_id,
                reason="Operator network rebalance canonical solve",
            )
            result_payload = solve.result.to_dict()
            is_stale = solve.is_stale(scenario)
            is_infeasible = result_payload.get("infeasible", False) or (
                result_payload.get("solver_status") == "infeasible"
            )
            diagnostics = result_payload.get("diagnostics", [])
            if (
                "modelled_constraint_classes" not in result_payload
                or result_payload["modelled_constraint_classes"] is None
            ):
                raise ValueError("NetPlan solve result missing 'modelled_constraint_classes'")
            if (
                "unmodelled_constraint_classes" not in result_payload
                or result_payload["unmodelled_constraint_classes"] is None
            ):
                raise ValueError("NetPlan solve result missing 'unmodelled_constraint_classes'")

            modelled_classes = list(result_payload["modelled_constraint_classes"])
            unmodelled_classes = list(result_payload["unmodelled_constraint_classes"])
            plan_rows = [
                {
                    "id": scenario.scenario_id,
                    "name": scenario.scenario_name,
                    "score": result_payload["objective_value"],
                    "expectedGrossMargin": result_payload["expected_gross_margin"],
                    "investmentTwd": result_payload["budget_usage"],
                    "risk": result_payload["average_risk"],
                    "capacityDelta": result_payload["capacity_delta"],
                    "actions": result_payload["selected_actions"],
                    "bindingConstraints": result_payload["binding_constraints"],
                    "modelledConstraintClasses": modelled_classes,
                    "unmodelledConstraintClasses": unmodelled_classes,
                    "modelled_constraint_classes": modelled_classes,
                    "unmodelled_constraint_classes": unmodelled_classes,
                    "isSystemRecommendation": True,
                    "selected": False,
                    "solverStatus": result_payload["solver_status"],
                    "solverVersion": result_payload["solver_version"],
                    "evidenceIds": [scenario.scenario_id],
                    "isStale": is_stale,
                    "isInfeasible": is_infeasible,
                    "diagnostics": diagnostics,
                }
            ]
            for index, alternative in enumerate(
                result_payload.get("alternatives", []),
                start=1,
            ):
                if (
                    "modelled_constraint_classes" not in alternative
                    or alternative["modelled_constraint_classes"] is None
                ):
                    raise ValueError(f"Alternative {index} missing 'modelled_constraint_classes'")
                if (
                    "unmodelled_constraint_classes" not in alternative
                    or alternative["unmodelled_constraint_classes"] is None
                ):
                    raise ValueError(f"Alternative {index} missing 'unmodelled_constraint_classes'")

                alt_modelled = list(alternative["modelled_constraint_classes"])
                alt_unmodelled = list(alternative["unmodelled_constraint_classes"])
                plan_rows.append(
                    {
                        "id": f"{scenario.scenario_id}:alternative:{index}",
                        "name": f"{scenario.scenario_name} alternative {index}",
                        "score": alternative["objective_value"],
                        "expectedGrossMargin": alternative["expected_gross_margin"],
                        "investmentTwd": alternative["budget_usage"],
                        "risk": alternative["average_risk"],
                        "capacityDelta": alternative["capacity_delta"],
                        "actions": alternative["actions"],
                        "bindingConstraints": alternative["binding_constraints"],
                        "modelledConstraintClasses": alt_modelled,
                        "unmodelledConstraintClasses": alt_unmodelled,
                        "modelled_constraint_classes": alt_modelled,
                        "unmodelled_constraint_classes": alt_unmodelled,
                        "isSystemRecommendation": False,
                        "selected": False,
                        "solverStatus": result_payload["solver_status"],
                        "solverVersion": result_payload["solver_version"],
                        "evidenceIds": [scenario.scenario_id],
                        "isStale": is_stale,
                        "isInfeasible": False,
                        "diagnostics": [],
                    }
                )
            store["status"] = "netplanreview"
            store["netPlanJob"] = {
                "id": scenario.scenario_id,
                "status": result_payload["solver_status"],
                "completedAt": solve.solved_at.isoformat(),
                "modelVersion": scenario.model_version,
                "featureVersion": scenario.feature_version,
                "solverVersion": result_payload["solver_version"],
            }
            store["netPlanScenarios"] = plan_rows
            store["runtimeState"] = None
            audit = self._audit(
                action="rebalance.netplan.solved",
                target_id=store_id,
                actor_role_id=actor_role_id,
                actor_name=actor_name,
                correlation_id=correlation_id,
                metadata={
                    "scenarioId": scenario.scenario_id,
                    "solverStatus": result_payload["solver_status"],
                    "solverVersion": result_payload["solver_version"],
                    "alternativeCount": len(result_payload.get("alternatives", [])),
                },
            )
            result = {
                "store": _copy(self._view_store(store)),
                "auditEvent": audit,
                "correlationId": correlation_id,
            }
            if idempotency_key:
                self._idempotency_cache[cache_key] = _copy(result)
            return result

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
        result = {
            "store": _copy(self._view_store(store)),
            "auditEvent": audit,
            "correlationId": correlation_id,
        }
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
            raise NetworkRebalanceConflict(
                f"{store_id} must be in NetPlan review before scenario selection"
            )
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
                "source": (
                    f"{actor_name or actor_role_id} · "
                    f"{store.get('netPlanJob', {}).get('solverVersion', '')}"
                    if self._require_canonical
                    else f"{actor_name or actor_role_id} · {_NETPLAN_MODEL['snapshotId']}"
                ),
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
                **(
                    {
                        "modelVersion": store.get("netPlanJob", {}).get("modelVersion"),
                        "featureVersion": store.get("netPlanJob", {}).get("featureVersion"),
                        "solverVersion": store.get("netPlanJob", {}).get("solverVersion"),
                    }
                    if self._require_canonical
                    else _NETPLAN_MODEL
                ),
            },
        )
        result = {
            "store": _copy(self._view_store(store)),
            "auditEvent": audit,
            "correlationId": correlation_id,
        }
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
        acknowledged_classes: list[str] | tuple[str, ...] | None = None,
        acknowledgement_reason: str | None = None,
        acknowledgement_actor_id: str | None = None,
        approval_receipt_id: str | None = None,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if not reason:
            raise NetworkRebalancePolicyError("submit review reason is required")

        cache_key = ("submit_review", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        store = self._store(store_id)
        if store["status"] != "netplanreview":
            raise NetworkRebalanceConflict(
                f"{store_id} must be in NetPlan review before submission"
            )
        if not store.get("selectedScenarioId"):
            raise NetworkRebalancePolicyError("selected scenario is required before submission")

        scenario = self._scenario(store, str(store["selectedScenarioId"]))

        # ODP-FR-NET-002 disclosure gate. Which unmodelled classes block and
        # which a named authority may sign for is registry data, resolved
        # point-in-time, never a literal here: the two lists move on a
        # governance clock, and a copy in this module would keep approving on
        # rules the registry had already retired.
        canonical_scenario = None
        binding: _CanonicalRowBinding | None = None
        selected_candidate_id = str(scenario.get("id") or "").strip() or None
        if self._require_canonical:
            binding = self._canonical_disclosure_for_row(
                store, scenario, require_solved=True
            )
            canonical_scenario = binding.scenario
            modelled = binding.modelled_classes
            unmodelled = binding.unmodelled_classes
            selected_candidate_id = binding.candidate_id
        else:
            modelled, unmodelled = self._declared_disclosure(scenario)
        policy = self._require_disclosure_policy()
        evaluation = evaluate_disclosure(policy, unmodelled_classes=unmodelled)

        if evaluation.blocking:
            raise NetworkRebalancePolicyError(
                f"cannot submit {scenario.get('name', store.get('selectedScenarioId'))} for "
                f"review: {', '.join(evaluation.blocking)} "
                f"{'are' if len(evaluation.blocking) > 1 else 'is'} required by "
                f"ODP-FR-NET-002 and left unmodelled by this solve. Policy "
                f"{policy.policy_version_id} does not permit acknowledging "
                f"{'them' if len(evaluation.blocking) > 1 else 'it'}: supply the "
                f"missing cap and re-solve"
            )

        acknowledgement = None
        ack_classes: list[str] = []
        if not evaluation.acknowledgeable and acknowledged_classes:
            # Accepting the submission and dropping the named classes would
            # record a signature nobody can find later. Whatever the caller
            # believed they were signing for, this solve does not carry it.
            raise NetworkRebalancePolicyError(
                "this solve leaves no acknowledgeable constraint class unmodelled, so "
                f"{', '.join(str(item) for item in acknowledged_classes)} cannot be "
                "acknowledged against it"
            )
        if evaluation.acknowledgeable:
            acknowledgement = self._acknowledge_unmodelled_classes(
                store=store,
                policy=policy,
                evaluation=evaluation,
                acknowledged_classes=acknowledged_classes,
                acknowledgement_reason=acknowledgement_reason,
                acknowledgement_actor_id=acknowledgement_actor_id,
                approval_receipt_id=approval_receipt_id,
                selected_candidate_id=selected_candidate_id,
            )
            ack_classes = [str(item) for item in acknowledgement.acknowledged_classes]
            if binding is not None:
                self._require_acknowledgement_subject(acknowledgement, binding)

        # The Operator request is the production submission boundary. Do not
        # create a Govern approval while the canonical scenario is still merely
        # SOLVED: a later NetPlan decision must observe the same lifecycle state
        # that the Operator response claims to have submitted. The acknowledgement
        # above is intentionally written first, so every failed submission still
        # leaves no partial signature behind.
        if canonical_scenario is not None:
            try:
                self._canonical_netplan_service().submit_for_approval(
                    canonical_scenario.scenario_id,
                    actor=actor_name or actor_role_id,
                    reason=reason,
                    selected_candidate_id=selected_candidate_id,
                )
            except (InvalidNetPlanTransitionError, NetPlanApprovalError) as exc:
                raise NetworkRebalancePolicyError(
                    f"canonical NetPlan scenario could not be submitted for approval: {exc}"
                ) from exc

        approval_id = store.get("relatedApprovalId") or f"APR-NET-{store_id}"
        approval = {
            "id": approval_id,
            "module": "Network",
            "kind": "netplan",
            "ref": store_id,
            "title": f"NetPlan 重配審核：{store['storeName']}（{scenario['name']}）",
            "meta": (
                f"{scenario['name']} score {scenario['score']} · "
                + (
                    f"{store.get('netPlanJob', {}).get('modelVersion')} · "
                    f"{store.get('netPlanJob', {}).get('solverVersion')}"
                    if self._require_canonical
                    else f"{_NETPLAN_MODEL['modelVersion']} · {_NETPLAN_MODEL['snapshotId']}"
                )
            ),
            "status": "pending",
            "cta": "Review",
            "tone": "warning",
            "risk": "高",
            "requestedByRoleId": actor_role_id,
            "requestedBy": actor_name or "Expansion Manager",
            "requiredRoleIds": ["opsLead", "auditPm"],
            "selectedCandidateId": selected_candidate_id,
            # Quoted from the canonical candidate, not from the projection row.
            # The row has already been reconciled against it, so the two agree
            # here by construction -- writing the canonical side keeps that true
            # for any future row field the reconciliation has not yet learned to
            # check, instead of making the approval trust the console again.
            "selectedActions": (
                binding.action_payload()
                if binding is not None
                else _copy(scenario.get("actions", []))
            ),
            "selectedActionSignature": (
                [list(item) for item in binding.action_signature]
                if binding is not None
                else None
            ),
            "modelledConstraintClasses": modelled,
            "unmodelledConstraintClasses": unmodelled,
            "blockedConstraintClasses": list(evaluation.blocking),
            "acknowledgeableConstraintClasses": list(evaluation.acknowledgeable),
            "acknowledgedConstraintClasses": ack_classes,
            "disclosurePolicyVersionId": policy.policy_version_id,
            "disclosurePolicyLabel": policy.policy_label,
            "disclosurePolicyVersion": policy.policy_version,
            # Present only when the exposure was actually signed for. A caller
            # reading this approval can tell a signed acknowledgement from an
            # absent one without inferring it from the class list.
            "disclosureAcknowledgementId": (
                acknowledgement.acknowledgement_id if acknowledgement is not None else None
            ),
            "disclosureAcknowledgedBy": (
                acknowledgement.actor_id if acknowledgement is not None else None
            ),
            "disclosureAcknowledgedByRole": (
                acknowledgement.actor_role if acknowledgement is not None else None
            ),
            "disclosureApprovalReceiptId": (
                acknowledgement.approval_receipt_id if acknowledgement is not None else None
            ),
            "disclosureSolverProblemHash": (
                acknowledgement.solver_problem_hash if acknowledgement is not None else None
            ),
            "disclosureBaselineContentHash": (
                acknowledgement.selected_baseline_content_hash
                if acknowledgement is not None
                else None
            ),
            "evidenceIds": [
                str(store.get("avm", {}).get("evidenceId", "")),
                *list(scenario.get("evidenceIds", [])),
                str(store.get("selectedScenarioEvidenceId", "")),
            ],
            "reason": reason,
            # The submission reason is not a fallback for the acknowledgement
            # reason. They answer different questions -- why this plan is being
            # sent for approval, and why an unvalidated exposure is acceptable --
            # and reusing the first as the second produces a receipt that never
            # recorded an answer to the second.
            "acknowledgementReason": (
                acknowledgement.reason if acknowledgement is not None else None
            ),
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
        store["executionBoundary"] = (
            "Govern approval was created; relocation remains unexecuted until a later approved execution plan."
        )
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

    def _require_canonical_dependencies(self) -> None:
        missing = [
            name
            for name, value in (
                ("avm_repository", self._avm_repository),
                ("netplan_repository", self._netplan_repository),
                ("tenant_id", self._tenant_id),
            )
            if value is None or value == ""
        ]
        if missing:
            raise NetworkRebalanceRuntimeUnavailable(
                model="AVM/NetPlan",
                store_id="unscoped",
            )

    @staticmethod
    def _declared_constraint_classes(scenario: dict[str, Any], kind: str) -> list[str]:
        """The classes this scenario row says the solve did / did not bind.

        A missing key is a refusal rather than an empty list. A row that carries
        no unmodelled set has not disclosed that it bound everything -- it has
        failed to disclose anything, and reading a missing set as "nothing is
        unmodelled" is the fail-open the CP-SAT production path already shipped
        once (see the 2026-09-02 correction in
        docs/design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md).

        Present-but-empty is not decidable one half at a time: an empty
        unmodelled set is a real disclosure next to a populated modelled set,
        and an absence next to an empty one. That judgement needs both halves,
        so it lives in `_declared_disclosure` rather than here.
        """
        for key in (f"{kind}ConstraintClasses", f"{kind}_constraint_classes"):
            if key in scenario and scenario[key] is not None:
                return [str(item) for item in scenario[key]]
        raise NetworkRebalancePolicyError(
            f"selected scenario {scenario.get('id') or scenario.get('name')!r} declares no "
            f"{kind} constraint classes; refusing to submit a plan whose constraint "
            "disclosure is unknown"
        )

    @classmethod
    def _declared_disclosure(cls, scenario: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Both halves of this scenario's constraint disclosure, or a refusal.

        Read as a pair because the remaining fail-open lives in the pair rather
        than in either half. A row carrying ``[]`` for both halves has named no
        class it bound and no class it left unbound: it has disclosed nothing.
        Taken one key at a time both lists look well-formed, and the empty
        unmodelled set then reads as "the solve bound everything" -- the
        strongest possible claim, inferred from a row that made no claim at all.

        This is the same refusal ``NetPlanService._require_disclosed_classes``
        already makes over the solve record. It is restated here because the
        Operator submit path is a second entrance to the same Govern approval
        and does not go through that check: a gate only one entrance passes
        through is not a gate.
        """
        modelled = cls._declared_constraint_classes(scenario, "modelled")
        unmodelled = cls._declared_constraint_classes(scenario, "unmodelled")
        if not modelled and not unmodelled:
            raise NetworkRebalancePolicyError(
                f"selected scenario {scenario.get('id') or scenario.get('name')!r} declares "
                "neither modelled nor unmodelled constraint classes; an undisclosed solve "
                "cannot be submitted for approval"
            )
        return modelled, unmodelled

    @staticmethod
    def _normalise_constraint_classes(values: Any, *, source: str) -> list[str]:
        """Read class names without allowing an invalid or ambiguous partition."""
        if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
            raise NetworkRebalancePolicyError(
                f"{source} constraint classes must be a sequence"
            )
        normalised: list[str] = []
        for value in values:
            raw = value.value if isinstance(value, ConstraintClass) else str(value).strip().upper()
            try:
                constraint_class = ConstraintClass(raw)
            except ValueError as exc:
                raise NetworkRebalancePolicyError(
                    f"{source} contains unknown constraint class {raw!r}"
                ) from exc
            name = constraint_class.value
            if name in normalised:
                raise NetworkRebalancePolicyError(
                    f"{source} repeats constraint class {name}"
                )
            normalised.append(name)
        return normalised

    @classmethod
    def _validate_constraint_partition(
        cls,
        modelled: Any,
        unmodelled: Any,
        *,
        source: str,
    ) -> tuple[list[str], list[str]]:
        """Require a complete, disjoint partition of ODP-FR-NET-002 classes."""
        modelled_names = cls._normalise_constraint_classes(
            modelled, source=f"{source} modelled"
        )
        unmodelled_names = cls._normalise_constraint_classes(
            unmodelled, source=f"{source} unmodelled"
        )
        modelled_set = set(modelled_names)
        unmodelled_set = set(unmodelled_names)
        overlap = sorted(modelled_set & unmodelled_set)
        all_classes = {constraint_class.value for constraint_class in ConstraintClass}
        missing = sorted(all_classes - modelled_set - unmodelled_set)
        if overlap:
            raise NetworkRebalancePolicyError(
                f"{source} disclosure has overlap between modelled and unmodelled: {overlap}"
            )
        if not modelled_names and not unmodelled_names:
            raise NetworkRebalancePolicyError(
                f"{source} declares neither modelled nor unmodelled constraint classes; "
                "an undisclosed solve cannot be submitted for approval"
            )
        if missing:
            raise NetworkRebalancePolicyError(
                f"{source} disclosure omits required constraint classes: {missing}"
            )
        return modelled_names, unmodelled_names

    # Every economic field the solver attaches to an action. Comparing only the
    # (entity, action) signature would accept a row that keeps the right moves
    # but restates their margin, cost or risk -- which is what the operator is
    # actually reading when they decide to submit.
    _ACTION_NUMERIC_FIELDS = (
        "expected_gross_margin",
        "budget_cost",
        "risk_score",
        "construction_days",
        "equipment_units",
        "labour_headcount",
        "coverage_delta",
    )
    _ACTION_TEXT_FIELDS = ("dilution_zone_id", "period_key")
    _ACTION_SEQUENCE_FIELDS = ("source_snapshot_ids", "notes")

    # Row aggregate -> the canonical candidate attribute it claims to restate.
    _ROW_AGGREGATE_FIELDS = (
        ("score", "objective_value"),
        ("expectedGrossMargin", "expected_gross_margin"),
        ("investmentTwd", "budget_usage"),
        ("risk", "average_risk"),
        ("capacityDelta", "capacity_delta"),
    )

    @classmethod
    def _normalise_action(cls, raw: Any, *, source: str) -> tuple[Any, ...]:
        """One action reduced to a comparable value, or a refusal."""
        if hasattr(raw, "to_dict"):
            raw = raw.to_dict()
        if not isinstance(raw, Mapping):
            raise NetworkRebalancePolicyError(f"{source} is not an action record")
        entity_id = str(raw.get("entity_id", "") or "").strip()
        action = str(raw.get("action", "") or "").strip().upper()
        if not entity_id or not action:
            raise NetworkRebalancePolicyError(
                f"{source} does not name both an entity and an action"
            )
        values: list[Any] = [entity_id, action]
        for field_name in cls._ACTION_NUMERIC_FIELDS:
            value = raw.get(field_name)
            if value is None:
                values.append(None)
                continue
            try:
                values.append(round(float(value), 9))
            except (TypeError, ValueError) as exc:
                raise NetworkRebalancePolicyError(
                    f"{source}.{field_name} is not a number"
                ) from exc
        try:
            values.append(int(raw.get("capacity_delta", 0) or 0))
        except (TypeError, ValueError) as exc:
            raise NetworkRebalancePolicyError(
                f"{source}.capacity_delta is not an integer"
            ) from exc
        for field_name in cls._ACTION_TEXT_FIELDS:
            values.append(str(raw.get(field_name, "") or ""))
        for field_name in cls._ACTION_SEQUENCE_FIELDS:
            sequence = raw.get(field_name) or ()
            if isinstance(sequence, (str, bytes)) or not isinstance(sequence, Sequence):
                raise NetworkRebalancePolicyError(
                    f"{source}.{field_name} is not a sequence"
                )
            values.append(tuple(str(item) for item in sequence))
        return tuple(values)

    @classmethod
    def _normalise_actions(cls, raw: Any, *, source: str) -> tuple[tuple[Any, ...], ...]:
        """An action list reduced to an order-insensitive comparable value.

        ``None`` and a non-sequence are refusals rather than an empty plan: a
        row that carries no action list has not said this candidate does
        nothing, it has failed to say what the candidate does, and the two must
        not reconcile against a canonical candidate that happens to be empty.
        """
        if raw is None:
            raise NetworkRebalancePolicyError(f"{source} declares no actions")
        if isinstance(raw, (str, bytes, Mapping)) or not isinstance(raw, Sequence):
            raise NetworkRebalancePolicyError(f"{source} actions are not a sequence")
        normalised = [
            cls._normalise_action(item, source=f"{source} action {index}")
            for index, item in enumerate(raw, start=1)
        ]
        # Sorted by repr because the tuples mix floats with ``None`` for the
        # resource fields a caller did not declare, and those do not order
        # against each other.
        return tuple(sorted(normalised, key=repr))

    @staticmethod
    def _action_signature(actions: Sequence[Any]) -> tuple[tuple[str, str], ...]:
        """The (entity, action) pairs, in the spelling the NetPlan receipt uses."""
        return tuple(
            sorted(
                (str(action.entity_id), str(action.action.value)) for action in actions
            )
        )

    @classmethod
    def _reconcile_row_candidate(
        cls,
        row: Mapping[str, Any],
        candidate: Any,
        *,
        actions: Sequence[Any],
        expected_name: str,
        expected_evidence_ids: tuple[str, ...],
        source: str,
    ) -> None:
        """Refuse a row whose plan content differs from the candidate it names.

        The disclosure partition and the candidate id are only the row's claim
        about *which* plan it is. They stay identical under a projection edit
        that empties or rewrites the actions, so an approval built from a row
        checked on identity alone can carry a different plan than the durable
        NetPlan acknowledgement and ApprovalRecord bound to the same candidate.

        The name and evidence ids are reconciled for the same reason as the
        actions: both are copied verbatim onto the Govern approval, where they
        are what an approver reads to decide which plan they are approving and
        what to re-derive it from.
        """
        expected_actions = cls._normalise_actions(
            [action.to_dict() for action in actions],
            source=f"canonical {source}",
        )
        row_actions = cls._normalise_actions(row.get("actions"), source=source)
        if row_actions != expected_actions:
            raise NetworkRebalancePolicyError(
                f"{source} actions do not match the canonical candidate they name; "
                "the plan shown in the console is not the plan that would be approved"
            )

        for row_field, candidate_field in cls._ROW_AGGREGATE_FIELDS:
            if row_field not in row or row[row_field] is None:
                raise NetworkRebalancePolicyError(f"{source} is missing {row_field}")
            expected = getattr(candidate, candidate_field)
            try:
                actual = float(row[row_field])
            except (TypeError, ValueError) as exc:
                raise NetworkRebalancePolicyError(
                    f"{source}.{row_field} is not a number"
                ) from exc
            if round(actual, 9) != round(float(expected), 9):
                raise NetworkRebalancePolicyError(
                    f"{source}.{row_field} is {row[row_field]!r} but the canonical "
                    f"candidate reports {expected!r}"
                )

        expected_binding = tuple(
            str(item) for item in getattr(candidate, "binding_constraints", ())
        )
        raw_binding = row.get("bindingConstraints")
        if raw_binding is None or isinstance(raw_binding, (str, bytes, Mapping)):
            raise NetworkRebalancePolicyError(f"{source} is missing bindingConstraints")
        if tuple(str(item) for item in raw_binding) != expected_binding:
            raise NetworkRebalancePolicyError(
                f"{source} binding constraints do not match the canonical candidate"
            )

        if str(row.get("name") or "") != expected_name:
            raise NetworkRebalancePolicyError(
                f"{source} is named {row.get('name')!r} but the canonical candidate "
                f"is {expected_name!r}"
            )
        raw_evidence = row.get("evidenceIds")
        if raw_evidence is None or isinstance(raw_evidence, (str, bytes, Mapping)):
            raise NetworkRebalancePolicyError(f"{source} is missing evidenceIds")
        if tuple(str(item) for item in raw_evidence) != expected_evidence_ids:
            raise NetworkRebalancePolicyError(
                f"{source} evidence ids do not match the canonical solve"
            )

    @classmethod
    def _row_constraint_partition(
        cls, scenario: dict[str, Any], *, source: str
    ) -> tuple[list[str], list[str]]:
        """Require both transport spellings and ensure they cannot diverge."""
        values: dict[str, list[str]] = {}
        for kind in ("modelled", "unmodelled"):
            camel = f"{kind}ConstraintClasses"
            snake = f"{kind}_constraint_classes"
            if camel not in scenario or scenario[camel] is None:
                raise NetworkRebalancePolicyError(f"{source} is missing {camel}")
            if snake not in scenario or scenario[snake] is None:
                raise NetworkRebalancePolicyError(f"{source} is missing {snake}")
            camel_values = cls._normalise_constraint_classes(
                scenario[camel], source=f"{source}.{camel}"
            )
            snake_values = cls._normalise_constraint_classes(
                scenario[snake], source=f"{source}.{snake}"
            )
            if camel_values != snake_values:
                raise NetworkRebalancePolicyError(
                    f"{source} has divergent {camel} and {snake} values"
                )
            values[kind] = camel_values
        return cls._validate_constraint_partition(
            values["modelled"], values["unmodelled"], source=source
        )

    def _canonical_disclosure_for_row(
        self,
        store: dict[str, Any],
        row: dict[str, Any],
        *,
        require_solved: bool = False,
    ) -> _CanonicalRowBinding:
        """Bind one Operator row to the durable scenario and solve it represents.

        Projection rows are a convenience for the console, not an authority. In
        particular, an operator-domain document can be stale or corrupted while
        the canonical NetPlan repository remains correct. Submission therefore
        validates the selected row against the canonical solve and constraints,
        including alternatives, before policy evaluation or acknowledgement.

        The reconciliation covers the row's plan content -- its actions and the
        impact figures restated from them -- and not only its candidate id and
        disclosure partition. Those identifiers survive a projection edit that
        rewrites the actions, and the durable NetPlan acknowledgement and
        ApprovalRecord are bound to the canonical candidate's actions, so an
        identity-only check lets the Govern approval and the NetPlan record
        describe two different plans under one candidate id.
        """
        if self._netplan_repository is None:
            raise NetworkRebalancePolicyError(
                "canonical NetPlan repository is unavailable; disclosure cannot be verified"
            )
        canonical_id = str((store.get("netPlanJob") or {}).get("id") or "").strip()
        if not canonical_id:
            raise NetworkRebalancePolicyError(
                "no canonical NetPlan solve is recorded for this store"
            )
        canonical_scenario = self._netplan_repository.get_scenario(canonical_id)
        solve = self._netplan_repository.get_solve(canonical_id)
        if canonical_scenario is None or solve is None:
            raise NetworkRebalancePolicyError(
                f"canonical NetPlan scenario {canonical_id} has no durable solve record"
            )
        if canonical_scenario.scenario_id != solve.scenario_id:
            raise NetworkRebalancePolicyError(
                f"canonical NetPlan solve {canonical_id} is bound to a different scenario"
            )
        if canonical_scenario.tenant_id != self._tenant_id:
            raise NetworkRebalancePolicyError(
                f"canonical NetPlan scenario {canonical_id} is outside the active tenant"
            )
        if require_solved and canonical_scenario.status is not NetPlanScenarioStatus.SOLVED:
            raise NetworkRebalancePolicyError(
                f"canonical NetPlan scenario {canonical_id} must be SOLVED before Operator submission; "
                f"it is {canonical_scenario.status.value}"
            )
        if solve.is_stale(canonical_scenario):
            raise NetworkRebalancePolicyError(
                f"canonical NetPlan solve {canonical_id} is stale and cannot be submitted"
            )

        expected_modelled, expected_unmodelled = self._validate_constraint_partition(
            canonical_scenario.constraints.modelled_classes(),
            canonical_scenario.constraints.unmodelled_classes(),
            source=f"canonical scenario {canonical_id}",
        )
        actual_modelled, actual_unmodelled = self._validate_constraint_partition(
            getattr(solve.result, "modelled_constraint_classes", None),
            getattr(solve.result, "unmodelled_constraint_classes", None),
            source=f"canonical solve {canonical_id}",
        )
        if (actual_modelled, actual_unmodelled) != (
            expected_modelled,
            expected_unmodelled,
        ):
            raise NetworkRebalancePolicyError(
                f"canonical solve {canonical_id} disclosure does not match its constraints"
            )

        row_id = str(row.get("id") or "").strip()
        expected_row_modelled = actual_modelled
        expected_row_unmodelled = actual_unmodelled
        # The canonical candidate this row claims to be: the primary plan, or
        # one enumerated alternative. Everything the approval later quotes is
        # taken from here.
        candidate: Any = solve.result
        candidate_actions = tuple(getattr(solve.result, "selected_actions", ()))
        expected_name = str(canonical_scenario.scenario_name)
        if row_id != canonical_id:
            prefix = f"{canonical_id}:alternative:"
            if not row_id.startswith(prefix):
                raise NetworkRebalancePolicyError(
                    f"selected Operator scenario {row_id!r} is not part of canonical solve {canonical_id}"
                )
            raw_index = row_id[len(prefix) :]
            if not raw_index.isdigit() or int(raw_index) < 1:
                raise NetworkRebalancePolicyError(
                    f"selected Operator alternative {row_id!r} has an invalid index"
                )
            index = int(raw_index) - 1
            alternatives = getattr(solve.result, "alternatives", ())
            if index >= len(alternatives):
                raise NetworkRebalancePolicyError(
                    f"selected Operator alternative {row_id!r} is absent from canonical solve {canonical_id}"
                )
            alternative = alternatives[index]
            candidate = alternative
            candidate_actions = tuple(getattr(alternative, "actions", ()))
            expected_name = f"{canonical_scenario.scenario_name} alternative {index + 1}"
            expected_row_modelled, expected_row_unmodelled = (
                self._validate_constraint_partition(
                    getattr(alternative, "modelled_constraint_classes", None),
                    getattr(alternative, "unmodelled_constraint_classes", None),
                    source=f"canonical solve {canonical_id} alternative {index + 1}",
                )
            )
            if (expected_row_modelled, expected_row_unmodelled) != (
                actual_modelled,
                actual_unmodelled,
            ):
                raise NetworkRebalancePolicyError(
                    f"canonical solve {canonical_id} alternative {index + 1} has a divergent disclosure"
                )

        row_source = f"Operator scenario {row_id or '<missing>'}"
        row_modelled, row_unmodelled = self._row_constraint_partition(
            row, source=row_source
        )
        if (row_modelled, row_unmodelled) != (
            expected_row_modelled,
            expected_row_unmodelled,
        ):
            raise NetworkRebalancePolicyError(
                f"Operator scenario {row_id!r} disclosure does not match canonical solve {canonical_id}"
            )
        self._reconcile_row_candidate(
            row,
            candidate,
            actions=candidate_actions,
            expected_name=expected_name,
            expected_evidence_ids=(canonical_id,),
            source=row_source,
        )
        return _CanonicalRowBinding(
            scenario=canonical_scenario,
            solve=solve,
            candidate_id=row_id,
            modelled_classes=expected_row_modelled,
            unmodelled_classes=expected_row_unmodelled,
            actions=candidate_actions,
            action_signature=self._action_signature(candidate_actions),
        )

    def _canonical_netplan_service(self) -> NetPlanService:
        if self._netplan_repository is None:
            raise NetworkRebalancePolicyError(
                "canonical NetPlan repository is unavailable"
            )
        return NetPlanService(
            repository=self._netplan_repository,
            production_executor=self._netplan_production_executor,
            approval_verifier=self.netplan_approval_verifier,
            policy_repository=self._netplan_policy_repository,
            runtime_mode=self._runtime_mode,
        )

    def _require_disclosure_policy(self) -> DecisionPolicy:
        """Resolve the disclosure policy in force for this tenant, or refuse.

        Point-in-time and tenant-scoped, matching
        ``NetPlanService._require_disclosure_policy``: re-deriving why a plan was
        approved months later has to resolve to the rules that approved it.
        """
        if self._netplan_policy_repository is None or not self._tenant_id:
            raise NetworkRebalancePolicyError(
                "netplan constraint disclosure policy is not configured for this "
                "Operator surface; refusing to submit a plan without resolving which "
                "unmodelled constraint classes the policy blocks"
            )
        try:
            return resolve_policy(
                self._netplan_policy_repository,
                policy_kind=NETPLAN_DISCLOSURE_POLICY_KIND,
                tenant_id=self._tenant_id,
                at=datetime.now(UTC),
            )
        except (PolicyResolutionError, NetPlanDisclosurePolicyError) as exc:
            raise NetworkRebalancePolicyError(
                f"netplan constraint disclosure policy could not be resolved: {exc}"
            ) from exc

    @staticmethod
    def _require_acknowledgement_subject(
        acknowledgement: Any,
        binding: _CanonicalRowBinding,
    ) -> None:
        """Refuse to publish an approval the durable receipt does not cover.

        ``NetPlanService`` resolves the acknowledgement subject from the
        canonical solve independently of this module. Comparing the two here is
        the point at which the Govern approval about to be written is shown to
        describe the same candidate, the same actions and the same problem hash
        as the receipt a later ``decide`` will re-verify. Without it the two
        derivations can drift apart silently and the divergence only surfaces
        as an approval that cannot be executed.
        """
        recorded_candidate = str(
            getattr(acknowledgement, "selected_candidate_id", "") or ""
        )
        if recorded_candidate != binding.candidate_id:
            raise NetworkRebalancePolicyError(
                f"disclosure acknowledgement was recorded against candidate "
                f"{recorded_candidate!r}, not the submitted "
                f"{binding.candidate_id!r}"
            )
        recorded_signature = tuple(
            (str(entity_id), str(action))
            for entity_id, action in getattr(
                acknowledgement, "selected_action_signature", ()
            )
        )
        if recorded_signature != binding.action_signature:
            raise NetworkRebalancePolicyError(
                "disclosure acknowledgement was recorded against a different set of "
                "actions than the submitted candidate carries"
            )
        recorded_hash = str(getattr(acknowledgement, "solver_problem_hash", "") or "")
        expected_hash = str(getattr(binding.solve, "problem_hash", "") or "")
        if recorded_hash != expected_hash:
            raise NetworkRebalancePolicyError(
                "disclosure acknowledgement was recorded against a different solve "
                "than the one being submitted"
            )

    def _acknowledge_unmodelled_classes(
        self,
        *,
        store: dict[str, Any],
        policy: DecisionPolicy,
        evaluation: DisclosureEvaluation,
        acknowledged_classes: list[str] | tuple[str, ...] | None,
        acknowledgement_reason: str | None,
        acknowledgement_actor_id: str | None,
        approval_receipt_id: str | None,
        selected_candidate_id: str | None,
    ) -> Any:
        """Sign for this solve's acknowledgeable exposure, or refuse to submit.

        The signature is produced by ``NetPlanService``, not here, because that
        is where authority is taken from the verified management approval
        receipt rather than from the caller. An Operator request that could name
        its own authorising role would let an actor authorise themselves, which
        is the failure the whole acknowledgement path exists to prevent -- so
        ``actorRoleId`` on the submit payload is recorded as the requester and
        never consulted for authority.
        """
        named = [
            str(item).strip().upper() for item in (acknowledged_classes or []) if str(item).strip()
        ]
        if not named:
            raise NetworkRebalancePolicyError(
                "this plan leaves "
                f"{', '.join(evaluation.acknowledgeable)} unmodelled; submission requires "
                "naming each class being acknowledged. An 'acknowledge whatever is "
                "outstanding' submission would produce a receipt whose meaning changes "
                "with the scenario"
            )
        outstanding = [item for item in evaluation.acknowledgeable if item not in named]
        if outstanding:
            raise NetworkRebalancePolicyError(
                f"unmodelled constraint classes {', '.join(outstanding)} were disclosed but "
                "not acknowledged; every acknowledgeable class this solve left unmodelled "
                "must be signed for before the plan reaches Govern"
            )
        cleaned_reason = str(acknowledgement_reason or "").strip()
        if not cleaned_reason:
            raise NetworkRebalancePolicyError(
                "acknowledging an unmodelled constraint class requires its own reason: "
                "the receipt has to record why the exposure was accepted"
            )
        actor_id = str(acknowledgement_actor_id or "").strip()
        if not actor_id:
            raise NetworkRebalancePolicyError(
                "acknowledging an unmodelled constraint class requires the acknowledging "
                "principal id, matched against the verified management approval receipt"
            )
        receipt_id = str(approval_receipt_id or "").strip()
        if not receipt_id:
            raise NetworkRebalancePolicyError(
                "acknowledging an unmodelled constraint class requires the management "
                "approval receipt that establishes the signer's authority; roles authorised "
                f"by policy {policy.policy_version_id} are "
                f"{', '.join(authorized_roles(policy)) or '(none)'}"
            )
        if self._netplan_repository is None:
            raise NetworkRebalancePolicyError(
                "this Operator surface has no NetPlan repository, so an acknowledgement "
                "receipt cannot be made durable; a plan with unmodelled required classes "
                "cannot be submitted from here"
            )

        scenario_id = str((store.get("netPlanJob") or {}).get("id") or "")
        if not scenario_id:
            raise NetworkRebalancePolicyError(
                "no NetPlan solve is recorded for this store; nothing can be acknowledged"
            )
        service = self._canonical_netplan_service()
        try:
            return service.acknowledge_unmodelled_constraints(
                scenario_id=scenario_id,
                actor_id=actor_id,
                reason=cleaned_reason,
                acknowledged_classes=named,
                approval_receipt_id=receipt_id,
                selected_candidate_id=selected_candidate_id,
            )
        except NetPlanConstraintDisclosureError as exc:
            raise NetworkRebalancePolicyError(str(exc)) from exc

    def _optional_disclosure_policy(self) -> DecisionPolicy | None:
        """The policy if one resolves, else ``None``.

        Read paths must not fail because the registry is unreachable -- the
        snapshot is how an operator finds out a plan is blocked. The refusal
        belongs at submission, so this returns ``None`` and `_disclosure_view`
        renders the fail-closed classification.
        """
        try:
            return self._require_disclosure_policy()
        except NetworkRebalancePolicyError:
            return None

    def _disclosure_view(
        self,
        scenario: dict[str, Any],
        policy: DecisionPolicy | None,
        *,
        store: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """How the submit gate would classify this scenario, for the UI to render.

        Computed from the same policy the gate uses so the console cannot show a
        class as waivable that the server would block. When no policy resolves,
        every unmodelled class is reported as blocking: an unresolvable policy is
        indistinguishable from no policy, and the console must not offer a
        signature the server will refuse.

        The disclosure is read through `_declared_disclosure`, so a scenario the
        submit gate would refuse as undisclosed is reported `disclosureUndeclared`
        here too. The console and the gate have to agree about which scenarios
        are unverifiable; a read path that classified them as fully modelled
        would put a live submit button on a plan the server rejects.
        """
        if self._require_canonical:
            try:
                if store is None:
                    raise NetworkRebalancePolicyError(
                        "canonical disclosure view has no owning store"
                    )
                binding = self._canonical_disclosure_for_row(store, scenario)
                modelled = binding.modelled_classes
                unmodelled = binding.unmodelled_classes
            except NetworkRebalancePolicyError:
                # A projection row that cannot be reconciled with the canonical
                # solve is rendered as entirely unverifiable. Keeping even a
                # forged partial modelled list here would invite the operator to
                # read that subset as an authoritative verification claim.
                return {
                    "modelledConstraintClasses": [],
                    "unmodelledConstraintClasses": [],
                    "modelled_constraint_classes": [],
                    "unmodelled_constraint_classes": [],
                    "blockedConstraintClasses": [],
                    "acknowledgeableConstraintClasses": [],
                    "disclosurePolicyVersionId": None,
                    "disclosureUndeclared": True,
                }
        else:
            try:
                modelled, unmodelled = self._declared_disclosure(scenario)
            except NetworkRebalancePolicyError:
                return {
                    "blockedConstraintClasses": [],
                    "acknowledgeableConstraintClasses": [],
                    "disclosurePolicyVersionId": None,
                    "disclosureUndeclared": True,
                }

        try:
            if policy is None:
                raise NetPlanDisclosurePolicyError("no netplan disclosure policy resolved")
            evaluation = evaluate_disclosure(policy, unmodelled_classes=unmodelled)
        except NetPlanDisclosurePolicyError:
            return {
                **(
                    {
                        "modelledConstraintClasses": modelled,
                        "unmodelledConstraintClasses": unmodelled,
                        "modelled_constraint_classes": modelled,
                        "unmodelled_constraint_classes": unmodelled,
                    }
                    if self._require_canonical
                    else {}
                ),
                "blockedConstraintClasses": list(unmodelled),
                "acknowledgeableConstraintClasses": [],
                "disclosurePolicyVersionId": None,
                "disclosureUndeclared": False,
            }
        return {
            **(
                {
                    "modelledConstraintClasses": modelled,
                    "unmodelledConstraintClasses": unmodelled,
                    "modelled_constraint_classes": modelled,
                    "unmodelled_constraint_classes": unmodelled,
                }
                if self._require_canonical
                else {}
            ),
            "blockedConstraintClasses": list(evaluation.blocking),
            "acknowledgeableConstraintClasses": list(evaluation.acknowledgeable),
            "disclosurePolicyVersionId": evaluation.policy_version_id,
            "disclosureUndeclared": False,
        }

    def _refresh_canonical_stores(self) -> None:
        self._require_canonical_dependencies()
        existing = {
            str(row.get("storeId") or row.get("id")): row for row in self._state.get("stores", [])
        }
        cases = self._avm_repository.list_cases()
        scenarios = [
            scenario
            for scenario in self._netplan_repository.list_scenarios()
            if scenario.tenant_id == self._tenant_id
        ]
        store_ids = {case.store_id for case in cases}
        refreshed: list[dict[str, Any]] = []
        for store_id in sorted(store_ids):
            case = next(
                (item for item in cases if item.store_id == store_id),
                None,
            )
            linked_scenarios = [
                scenario for scenario in scenarios if store_id in scenario.options_by_entity
            ]
            base = {
                "id": store_id,
                "storeId": store_id,
                "storeName": store_id,
                "status": "watching",
                "ownerRoleId": None,
                "ownerName": None,
                "summary": "Canonical AVM / NetPlan inputs available",
                "healthNote": None,
                "evidence": [],
                "relocationExecuted": False,
                "runtimeState": None,
            }
            row = {**base, **_copy(existing.get(store_id, {}))}
            row["id"] = store_id
            row["storeId"] = store_id
            row["canonicalAvmCaseId"] = case.case_id if case is not None else None
            row["canonicalNetPlanScenarioIds"] = [
                scenario.scenario_id for scenario in linked_scenarios
            ]
            refreshed.append(row)
        self._state["stores"] = refreshed

    def _canonical_avm_metadata(self) -> dict[str, Any]:
        if not self._require_canonical:
            return _copy(_AVM_MODEL)
        reports = [
            self._avm_repository.latest_report(case.case_id)
            for case in self._avm_repository.list_cases()
        ]
        reports = [report for report in reports if report is not None]
        return {
            "modelVersions": sorted({report.model_version for report in reports}),
            "featureVersions": sorted({report.feature_version for report in reports}),
            "reportCount": len(reports),
        }

    def _canonical_netplan_metadata(self) -> dict[str, Any]:
        if not self._require_canonical:
            return _copy(_NETPLAN_MODEL)
        scenarios = [
            scenario
            for scenario in self._netplan_repository.list_scenarios()
            if scenario.tenant_id == self._tenant_id
        ]
        return {
            "modelVersions": sorted({scenario.model_version for scenario in scenarios}),
            "featureVersions": sorted({scenario.feature_version for scenario in scenarios}),
            "solverVersions": sorted({scenario.solver_version for scenario in scenarios}),
            "scenarioCount": len(scenarios),
        }

    def _store(self, store_id: str) -> dict[str, Any]:
        if self._require_canonical:
            self._refresh_canonical_stores()
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
        # Resolved once per store rather than once per scenario: the registry may
        # be a database, and the three cases of one solve are governed by the
        # same point-in-time policy.
        policy = self._optional_disclosure_policy() if store.get("netPlanScenarios") else None
        for scenario in store.get("netPlanScenarios", []):
            scenarios.append(
                {
                    **_copy(scenario),
                    **self._disclosure_view(scenario, policy, store=store),
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
