"""Request-local read projection of canonical and native pending review owners.

These rows are views, never ApprovalRecords. Native Intervention retains its
own full review command; a Govern read cannot create authority to decide it.
The caller supplies verified scope, canonical masters and tenant-bound owners.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from modules.opsboard.application.network_read_scope import candidate_scope_evidence
from modules.opsboard.application.operator_live_repository import OperatorLiveRepositoryError
from shared.auth import DataClassification, Principal


def _restricted(principal: Principal) -> bool:
    scope = principal.scope
    return bool(scope.store_ids or scope.brand_ids or scope.region_ids
                or scope.assigned_area_ids or scope.heat_zone_ids)


def _store_in_scope(principal: Principal, store_id: str, stores: Any, brands: Any) -> bool:
    # No invented brand/region evidence from display strings or client scope.
    if stores is None or brands is None:
        raise OperatorLiveRepositoryError("canonical pending store ownership is unavailable")
    store = stores.get_store(store_id)
    if store is None or not store.is_current or store.tenant_id != principal.tenant_id:
        return False
    brand = brands.get_brand(store.brand_id)
    return bool(brand is not None and brand.tenant_id == principal.tenant_id
                and principal.scope.permits_store(store.store_id)
                and principal.scope.permits_brand(store.brand_id)
                and principal.scope.permits_region(store.region_code or None)
                and principal.scope.permits_assigned_area(None)
                and principal.scope.permits_heat_zone(None))


def project_pending_snapshot(
    snapshot: dict[str, Any], native_state: dict[str, Any], *, principal: Principal,
    listing_snapshot_reader: Callable[[], dict[str, Any]], sitescore_owner: Any,
    avm_owner: Any, netplan_owner: Any,
    store_repository: Any, brand_repository: Any,
) -> dict[str, Any]:
    """Compose one scoped snapshot without changing either durable owner.

    Growth/PriceOps have already passed their existing all-item read owner.
    Other canonical owners are checked against their real parent identities.
    Native Intervention rows came from the live owner's canonical store query.
    Availability is checked before a genuinely empty list can mean zero.
    """
    if not principal.authenticated or not principal.tenant_id:
        raise OperatorLiveRepositoryError("verified pending read principal is required")
    availability = native_state.get("_meta", {}).get("sections", {}).get("approvals", {})
    if availability.get("state") != "available":
        raise OperatorLiveRepositoryError("native pending owners are unavailable")
    result = deepcopy(snapshot)
    scope = principal.scope
    permitted = (scope.permits_module("operator_console")
                 and scope.permits_classification(DataClassification.CONFIDENTIAL))
    restricted = _restricted(principal)
    candidates: dict[str, dict[str, Any]] | None = None

    def visible_candidates() -> dict[str, dict[str, Any]]:
        nonlocal candidates
        if candidates is None:
            candidates = candidate_scope_evidence(principal, listing_snapshot_reader())
        return candidates

    def candidate_visible(candidate_id: str) -> bool:
        return not restricted or candidate_id in visible_candidates()

    visible_cases: set[str] = set()
    visible_reports: set[str] = set()
    for case in avm_owner.list_cases():
        if _store_in_scope(principal, case.store_id, store_repository, brand_repository):
            visible_cases.add(case.case_id)
            report = avm_owner.latest_report(case.case_id)
            if report is not None:
                visible_reports.add(report.report_id)

    visible_scenarios: set[str] = set()

    def entity_visible(entity_id: str) -> bool:
        # A real store identity cannot escape a denied store binding through
        # a coincidentally equal candidate ID.
        if store_repository is not None and store_repository.get_store(entity_id) is not None:
            return _store_in_scope(principal, entity_id, store_repository, brand_repository)
        if entity_id in visible_candidates():
            return True
        if store_repository is None:
            raise OperatorLiveRepositoryError("canonical pending entity ownership is unavailable")
        return False

    for scenario in netplan_owner.list_scenarios():
        if scenario.tenant_id != principal.tenant_id:
            continue
        # build_scenario_options keys are canonical store or candidate IDs.
        # Every affected entity must be visible; an unbound restricted parent
        # cannot inherit tenant-wide visibility from a scenario name.
        if not restricted or (scenario.options_by_entity and all(
            entity_visible(entity_id) for entity_id in scenario.options_by_entity
        )):
            visible_scenarios.add(scenario.scenario_id)

    network_decisions = {decision.decision_id: decision for decision in sitescore_owner.list_decisions()}
    visible_network_decisions = {
        decision_id for decision_id, decision in network_decisions.items()
        if candidate_visible(decision.candidate_site_id)
    }
    visible_network_approvals = {
        row["id"] for row in result.get("approvals", [])
        if row.get("module") == "SiteScore" and row.get("decisionId") in visible_network_decisions
    }

    def visible(row: dict[str, Any], kind: str) -> bool:
        if not permitted:
            return False
        module = row.get("module")
        if module in {"Growth", "PriceOps"}:
            return True  # already projected by priceops_read_owner
        if not restricted:
            return True
        if module == "SiteScore":
            if kind == "approvals":
                return row.get("decisionId") in visible_network_decisions
            return (row.get("id") in visible_network_decisions
                    or row.get("entityRef") in visible_network_decisions
                    or row.get("approvalId") in visible_network_approvals)
        if module == "AVM":
            return (row.get("entityRef") in visible_cases
                    or row.get("approvalId") in visible_reports
                    or row.get("id") in visible_reports)
        if module == "NetPlan":
            return (row.get("entityRef") in visible_scenarios
                    or row.get("approvalId") in visible_scenarios
                    or row.get("id") in visible_scenarios)
        return False

    for kind in ("approvals", "decisions", "auditRows", "evidencePackages"):
        result[kind] = [row for row in result.get(kind, []) if visible(row, kind)]
    if restricted or not permitted:
        # Canonical status rows have names, not module/parent IDs. Recount
        # their actual scoped parents rather than retaining tenant totals.
        status_counts = {
            "SiteScore decisions": len(visible_network_decisions),
            "AVM cases": len(visible_cases), "NetPlan scenarios": len(visible_scenarios),
            "PriceOps plans": sum(row.get("module") == "PriceOps" for row in result["approvals"]),
        }
        result["statusBoard"] = [
            {**row, "count": status_counts[row["name"]]}
            for row in result.get("statusBoard", [])
            if permitted and row.get("name") in status_counts
        ]

    # Join SiteScore by its actual signature/decision relationship. In
    # particular, another review round for the same candidate stays distinct.
    signed_decisions = {
        row.get("decisionId"): row for row in result["approvals"]
        if row.get("module") == "SiteScore" and row.get("decisionId")
    }
    native_by_decision = {
        row.get("id"): row for row in native_state.get("decisions", [])
        if (row.get("target") or {}).get("workspace") == "network"
    }
    for decision_id, signed in signed_decisions.items():
        decision = network_decisions.get(decision_id)
        if decision is not None and signed.get("entityRef") == decision.candidate_site_id:
            signed["nativeTarget"] = {
                "workspace": "network", "tab": "review",
                "entityId": decision.candidate_site_id, "candidateId": decision.candidate_site_id,
                "reviewId": decision_id,
            }
            signed["actionAuthority"] = {"decide": False, "reason": "NATIVE_OWNER_REVIEW_REQUIRED"}

    # An unsigned model recommendation is a review task, not a submitted
    # Approval. Preserve its native identity/status/target and original roles.
    result["_nativeReviewTasks"] = [
        {**deepcopy(row), "owner": "SiteScore", "workspace": "network", "tags": ["review"]}
        for decision_id, row in native_by_decision.items()
        if permitted and decision_id not in signed_decisions
        and candidate_visible((row.get("target") or {}).get("entityId", ""))
    ]

    existing = {(row.get("module"), row.get("id")) for row in result["approvals"]}
    if permitted and scope.permits_assigned_area(None) and scope.permits_heat_zone(None):
        for native in native_state.get("decisions", []):
            target = native.get("target") or {}
            if target.get("workspace") != "today" or target.get("tab") != "interventions":
                continue
            if native.get("status") != "PENDING_APPROVAL":
                continue
            identity = native.get("id")
            if not identity or target.get("entityId") != identity or ("Store Ops", identity) in existing:
                continue
            result["approvals"].append({
                "id": identity, "module": "Store Ops", "title": native["title"],
                "meta": native.get("meta"), "status": "pending", "entityRef": identity,
                "nativeStatus": native["status"], "nativeTarget": deepcopy(target),
                "actionAuthority": {"decide": False, "reason": "NATIVE_OWNER_REVIEW_REQUIRED"},
            })
            existing.add(("Store Ops", identity))
    result["counts"] = {
        **result.get("counts", {}),
        "pendingApprovals": sum(row.get("status") == "pending" for row in result["approvals"]),
        "decisions": len(result["decisions"]), "auditRows": len(result["auditRows"]),
    }
    for name in ("evidencePackages", "statusBoard"):
        if name in result["counts"]:
            result["counts"][name] = len(result[name])
    return result


def project_pending_state(state: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Apply the shared collection before shell counts, search and Today KPIs."""
    result = deepcopy(state)
    pending = []
    for row in snapshot["approvals"]:
        if row.get("status") != "pending":
            continue
        target = row.get("nativeTarget") or row.get("target") or {
            "workspace": "govern", "tab": "approvals", "entityId": row["id"],
        }
        pending.append({
            "id": row["id"], "title": row["title"], "meta": row["module"],
            "status": "pending", "tone": "warning", "target": deepcopy(target),
            "roles": [],  # canonical oversight reads do not require APPROVE
            "cta": "開啟完整干預審查" if target.get("workspace") == "today" and target.get("tab") == "interventions" else "開啟完整審查",
        })
    result["decisions"] = pending
    task_ids = {row.get("id") for row in result.get("workQueue", [])}
    for row in snapshot.get("_nativeReviewTasks", []):
        if row.get("id") not in task_ids:
            result.setdefault("workQueue", []).append(deepcopy(row))
            task_ids.add(row.get("id"))
    meta = result.setdefault("_meta", {})
    meta.setdefault("recordCounts", {})["approvals"] = len(pending)
    if "workQueue" in meta.get("sections", {}):
        meta["sections"]["workQueue"]["recordCount"] = len(result["workQueue"])
        meta["recordCounts"]["workQueue"] = len(result["workQueue"])
    section = meta.setdefault("sections", {}).setdefault("approvals", {})
    section.update(state="available", available=True, complete=True,
                   recordCount=len(pending), source="canonical-native-pending-owners")
    for name in ("unavailableSections", "degradedSections"):
        if isinstance(meta.get(name), list):
            meta[name] = [value for value in meta[name] if value != "approvals"]
    return result
