"""Native identity proposals and independent reviews, before job admission.

The caller authorizes the fresh actor, Intake and every affected resource in
this serializable unit. Approval records a human decision; admission and the
existing executor separately own queue reservation and graph mutation.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from modules.listing.domain.identity_graph import IdentityConflictError
from shared.domain.events import DomainEvent
from shared.infrastructure.persistence.identity_cases_sql import (
    _version,
)


def _uuid(value: str) -> str:
    if not isinstance(value, (str, UUID)):
        raise ValueError("An explicit canonical UUID is required")
    return str(UUID(str(value)))


def _versions(values: dict) -> dict[str, int]:
    if not isinstance(values, dict):
        raise ValueError("Explicit native resource versions are required")
    result = {_uuid(key): _version(value) for key, value in values.items()}
    if len(result) != len(values):
        raise ValueError("Resource version identities must be unique")
    return result


def _identities(values: list) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("Explicit native resource identities are required")
    result = [_uuid(value) for value in values]
    if len(set(result)) != len(result):
        raise ValueError("Resource identities must be unique")
    return result


def canonical_identity_plan(kind: str, raw: dict) -> dict:
    """Whitelist the exact existing executor input; never infer a version."""
    common = {"expected_property_versions", "expected_listing_versions", "expected_edge_versions",
              "candidate_reassignment_plan"}
    specific = {"MERGE": {"source_property_ids", "target_property_id"},
                "SPLIT": {"source_property_id", "partitions"}}
    if not isinstance(kind, str) or kind not in specific or not isinstance(raw, dict) or set(raw) != common | specific[kind]:
        raise ValueError("A complete supported identity plan is required")
    plan = {name: _versions(raw[name]) for name in common if name != "candidate_reassignment_plan"}
    if kind == "MERGE":
        sources, target = _identities(raw["source_property_ids"]), _uuid(raw["target_property_id"])
        if target in sources:
            raise ValueError("An identity cannot be merged into itself")
        targets = {target}
        plan.update(source_property_ids=sources, target_property_id=target)
    else:
        sources = [_uuid(raw["source_property_id"])]
        if not isinstance(raw["partitions"], list) or len(raw["partitions"]) < 2:
            raise ValueError("At least two distinct split targets are required")
        partitions, targets, assigned = [], set(), set()
        for part in raw["partitions"]:
            if not isinstance(part, dict) or set(part) != {"target_property_id", "source_identity_edge_ids"}:
                raise ValueError("Explicit split partitions are required")
            target, edges = _uuid(part["target_property_id"]), _identities(part["source_identity_edge_ids"])
            if target in targets or target in sources or assigned.intersection(edges):
                raise ValueError("Split partitions require distinct targets and disjoint source edges")
            targets.add(target)
            assigned.update(edges)
            partitions.append({"target_property_id": target, "source_identity_edge_ids": edges})
        if assigned != set(plan["expected_edge_versions"]):
            raise ValueError("The split must explicitly assign every selected source edge")
        plan.update(source_property_id=sources[0], partitions=partitions)
    if set(plan["expected_property_versions"]) != set(sources) | targets:
        raise ValueError("The plan must bind every affected property version exactly")
    if not isinstance(raw["candidate_reassignment_plan"], list):
        raise ValueError("Explicit candidate dispositions are required")
    candidates, seen = [], set()
    for item in raw["candidate_reassignment_plan"]:
        if (not isinstance(item, dict) or not {"candidate_site_id", "expected_version", "disposition"} <= set(item)
                or set(item) - {"candidate_site_id", "expected_version", "disposition", "target_property_id"}):
            raise ValueError("Candidate identity, version and disposition are required")
        key = _uuid(item["candidate_site_id"])
        disposition = item["disposition"]
        target = _uuid(item["target_property_id"]) if item.get("target_property_id") is not None else None
        if (key in seen or not isinstance(disposition, str) or disposition not in {"REASSIGN", "KEEP_HISTORICAL", "REQUIRE_REVIEW"}
                or (target not in targets if disposition == "REASSIGN" else target is not None)):
            raise ValueError("The candidate disposition must name an eligible target exactly once")
        seen.add(key)
        candidates.append({"candidate_site_id": key, "expected_version": _version(item["expected_version"]),
                           "disposition": disposition, "target_property_id": target})
    plan["candidate_reassignment_plan"] = candidates
    return plan


class CanonicalIdentityDecisionRepository:
    def __init__(self, graph: Any) -> None:
        self.graph, self.engine = graph, graph.engine

    def _audit(self, tenant, intake, actor, key, before, after, action, reason, at, *, audit_id):
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": tenant, "resource_type": "identity_decision", "resource_id": key,
            "action": action, "result": "SUCCEEDED", "actor_subject_id": actor,
            "correlation_id": intake["correlation_id"], "occurred_at": at,
            "before_value": before, "after_value": after, "reason": reason,
        }, {name: intake[name] for name in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def get(self, tenant: str, decision_id: str) -> dict:
        with self.graph.transaction(tenant):
            decision = self.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=?",
                (tenant, _uuid(decision_id)))
            case, intake = self.graph._case(tenant, decision["match_case_id"])
            return {"decision": decision, "match_case": case, "intake": intake}

    def _context(self, tenant: str, case_id: str, expected_version: int) -> tuple[dict, dict]:
        case, intake = self.graph._case(tenant, _uuid(case_id))
        if case["version"] != _version(expected_version):
            raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
        if (case["status"] != "PENDING_REVIEW" or case["outcome"] not in {"POSSIBLE_MATCH", "QUARANTINED"}
                or intake["processing_state"] != "NEEDS_REVIEW" or intake["deleted_at"]):
            raise IdentityConflictError("IDENTITY_CASE_NOT_AWAITING_REVIEW")
        self.graph._no_hold(tenant, "intake", intake["intake_id"], held=intake["legal_hold"])
        return case, intake

    def _snapshot(self, tenant: str, case: dict, intake: dict, kind: str, plan: dict) -> dict:
        properties = self.graph._resources(tenant, plan, intake)
        sources = plan["source_property_ids"] if kind == "MERGE" else [plan["source_property_id"]]
        edges = self.graph._edges(tenant, sources, plan["expected_edge_versions"])
        anchors = self.engine.query("SELECT property_id,listing_id FROM identity.match_candidates "
            "WHERE tenant_id=? AND match_case_id=? ORDER BY rank", (tenant, case["match_case_id"]))
        anchored = set()
        for row in anchors:
            if row["listing_id"]:
                anchored.add(self.graph.resolve_listing(tenant, row["listing_id"])["effective_property_id"])
            elif row["property_id"]:
                anchored.add(row["property_id"])
        if not set(properties) <= anchored:
            raise IdentityConflictError("IDENTITY_PLAN_OUTSIDE_MATCH_CASE")
        candidates = self.engine.query("SELECT * FROM expansion.candidate_sites WHERE tenant_id=? "
            "AND property_id=ANY(?::uuid[]) ORDER BY candidate_site_id FOR UPDATE", (tenant, sources))
        dispositions = {row["candidate_site_id"]: row for row in plan["candidate_reassignment_plan"]}
        if {row["candidate_site_id"] for row in candidates} != set(dispositions):
            raise IdentityConflictError("CANDIDATE_DISPOSITION_REQUIRED")
        reassigned = set()
        for row in candidates:
            item = dispositions[row["candidate_site_id"]]
            if row["version"] != item["expected_version"]:
                raise IdentityConflictError("CANDIDATE_VERSION_CONFLICT")
            self.graph._no_hold(tenant, "candidate_site", row["candidate_site_id"])
            if item["disposition"] != "REASSIGN" or row["status"] in {"REJECTED", "OPENED"}:
                continue
            binding = (item["target_property_id"], row["target_format_code"])
            if binding in reassigned or self.engine.query_one("SELECT candidate_site_id FROM expansion.candidate_sites "
                "WHERE tenant_id=? AND property_id=? AND target_format_code=? AND status NOT IN ('REJECTED','OPENED') "
                "AND candidate_site_id<>? FOR UPDATE", (tenant, *binding, row["candidate_site_id"])):
                raise IdentityConflictError("CANDIDATE_PLAN_CONFLICT")
            reassigned.add(binding)
        listings = self.engine.query("SELECT * FROM expansion.listings WHERE tenant_id=? "
            "AND listing_id=ANY(?::uuid[]) ORDER BY listing_id", (tenant, list(plan["expected_listing_versions"])))
        return {"properties": list(properties.values()), "listings": listings, "source_edges": edges,
                "candidates": candidates, "match_case_version": case["version"], "intake_version": intake["version"]}

    def propose(self, tenant: str, case_id: str, *, kind: str, plan: dict, expected_case_version: int,
                actor_id: str, reason: str, risk_acknowledged: bool) -> dict:
        actor_id, case_id = _uuid(actor_id), _uuid(case_id)
        if risk_acknowledged is not True or len(reason.strip()) < 20:
            raise ValueError("Explicit identity risk acknowledgement and reason are required")
        plan = canonical_identity_plan(kind, deepcopy(plan))
        with self.graph.transaction(tenant):
            case, intake = self._context(tenant, case_id, expected_case_version)
            before = self._snapshot(tenant, case, intake, kind, plan)
            key, at = str(uuid4()), self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            audit_id = str(uuid4())
            after = {"plan": plan, "match_case_version": case["version"] + 1,
                     "intake_version": intake["version"], "proposal_audit_event_id": audit_id}
            self.engine.execute("INSERT INTO identity.match_decisions (match_decision_id,tenant_id,match_case_id,"
                "decision_type,status,proposer_subject_id,reason,risk_acknowledged,before_graph,after_graph) "
                "VALUES (?,?,?,?,'DRAFT',?,?,true,?::jsonb,?::jsonb)",
                (key, tenant, case_id, kind, actor_id, reason, json.dumps(before), json.dumps(after)))
            draft = {"decision_id": key, "match_case_id": case_id, "intake_id": intake["intake_id"],
                     "match_case_version": case["version"], "status": "DRAFT", "version": 1, "plan": plan}
            self._audit(tenant, intake, actor_id, key, None, draft,
                "identity.decision_created.v1", reason, at, audit_id=str(uuid4()))
            if self.engine.execute("UPDATE identity.match_decisions SET status='PENDING_REVIEW',version=2 "
                "WHERE tenant_id=? AND match_decision_id=? AND status='DRAFT' AND version=1", (tenant, key)).rowcount != 1:
                raise IdentityConflictError("IDENTITY_DECISION_VERSION_CONFLICT")
            if self.engine.execute("UPDATE identity.match_cases SET version=version+1,updated_at=? WHERE tenant_id=? "
                "AND match_case_id=? AND status='PENDING_REVIEW' AND version=?", (at, tenant, case_id, case["version"])).rowcount != 1:
                raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
            pending = {**draft, "status": "PENDING_REVIEW", "version": 2, "match_case_version": case["version"]+1,
                       "audit_event_id": audit_id}
            self._audit(tenant, intake, actor_id, key, draft, pending,
                "identity.decision_proposed.v1", reason, at, audit_id=audit_id)
            candidate_ids = [row["match_candidate_id"] for row in self.engine.query(
                "SELECT match_candidate_id FROM identity.match_candidates WHERE tenant_id=? AND match_case_id=? ORDER BY rank",
                (tenant, case_id))]
            contradictory = case["contradictory_signals"]
            self.graph._outbox(DomainEvent(event_type="match.review_required", tenant_id=tenant, aggregate_type="match_case",
                aggregate_id=case_id, aggregate_version=case["version"]+1, partition_key=tenant+":"+case_id,
                correlation_id=intake["correlation_id"], actor_ref=actor_id, producer="matcher_service",
                occurred_at=datetime.fromisoformat(at), schema_ref="#/payloads/MatchReviewRequiredV1",
                payload={"match_case_id": case_id, "intake_id": intake["intake_id"], "outcome": case["outcome"],
                         "confidence": float(case["confidence"]), "candidate_ids": candidate_ids,
                         "contradictory_signals": json.loads(contradictory) if isinstance(contradictory, str) else contradictory}))
            return {"decision_id": key, "status": "PENDING_REVIEW", "version": 2, "job_id": None,
                    "audit_event_id": audit_id, "correlation_id": intake["correlation_id"],
                    "match_case_id": case_id, "match_case_version": case["version"]+1}


    def review(self, tenant: str, decision_id: str, *, expected_decision_version: int,
               expected_case_version: int, reviewer_id: str, approve: bool, reason: str,
               risk_acknowledged: bool) -> dict:
        reviewer_id, decision_id = _uuid(reviewer_id), _uuid(decision_id)
        if (type(approve) is not bool or type(risk_acknowledged) is not bool or len(reason.strip()) < 3
                or (approve and risk_acknowledged is not True)):
            raise ValueError("An explicit review and reason/risk acknowledgement are required")
        with self.graph.transaction(tenant):
            decision = self.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? "
                "AND match_decision_id=? FOR UPDATE", (tenant, decision_id))
            if decision["version"] != _version(expected_decision_version) or decision["status"] != "PENDING_REVIEW":
                raise IdentityConflictError("IDENTITY_DECISION_VERSION_CONFLICT")
            if reviewer_id == decision["proposer_subject_id"]:
                raise IdentityConflictError("SECOND_ACTOR_REQUIRED")
            case, intake = self._context(tenant, decision["match_case_id"], expected_case_version)
            if decision["after_graph"].get("match_case_version") != case["version"]:
                raise IdentityConflictError("IDENTITY_PROPOSAL_CASE_BINDING_CONFLICT")
            if approve and decision["after_graph"].get("intake_version") != intake["version"]:
                raise IdentityConflictError("INTAKE_VERSION_CONFLICT")
            plan = canonical_identity_plan(decision["decision_type"], decision["after_graph"]["plan"])
            snapshot = self._snapshot(tenant, case, intake, decision["decision_type"], plan) if approve else None
            at, audit_id = self.engine.query_one("SELECT clock_timestamp() AS now")["now"], str(uuid4())
            status = "APPROVED" if approve else "REJECTED"
            review = {"reviewer_subject_id": reviewer_id, "status": status, "reason": reason,
                      "risk_acknowledged": risk_acknowledged, "reviewed_at": at, "audit_event_id": audit_id}
            after = {**decision["after_graph"], "review": review, "match_case_version": case["version"]+1,
                     "review_snapshot": snapshot}
            if self.engine.execute("UPDATE identity.match_decisions SET status=?,reviewer_subject_id=?,reviewed_at=?,"
                "version=version+1,after_graph=?::jsonb WHERE tenant_id=? AND match_decision_id=? "
                "AND status='PENDING_REVIEW' AND version=?", (status, reviewer_id, at, json.dumps(after),
                 tenant, decision_id, decision["version"])).rowcount != 1:
                raise IdentityConflictError("IDENTITY_DECISION_VERSION_CONFLICT")
            if self.engine.execute("UPDATE identity.match_cases SET status=?,version=version+1,updated_at=? "
                "WHERE tenant_id=? AND match_case_id=? AND status='PENDING_REVIEW' AND version=?",
                (status, at, tenant, case["match_case_id"], case["version"])).rowcount != 1:
                raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
            before_value = {"decision_id": decision_id, "status": "PENDING_REVIEW", "version": decision["version"],
                            "plan": plan, "match_case_id": case["match_case_id"], "match_case_version": case["version"]}
            after_value = {**before_value, **review, "version": decision["version"]+1, "match_case_version": case["version"]+1}
            self._audit(tenant, intake, reviewer_id, decision_id, before_value, after_value,
                "identity.decision_approved.v1" if approve else "identity.decision_rejected.v1", reason, at, audit_id=audit_id)
            self.graph._match_event(tenant, {**decision, "reviewer_subject_id": reviewer_id}, status=status,
                case_version=case["version"]+1, correlation_id=intake["correlation_id"], at=at)
            return {"decision_id": decision_id, "status": status, "version": decision["version"]+1,
                    "match_case_id": case["match_case_id"], "match_case_version": case["version"]+1,
                    "job_id": None, "audit_event_id": audit_id, "correlation_id": intake["correlation_id"]}


__all__ = ["CanonicalIdentityDecisionRepository", "canonical_identity_plan"]
