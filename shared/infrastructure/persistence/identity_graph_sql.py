"""Canonical identity graph storage on the approved Intake PostgreSQL schema.

This port consumes an already accepted execution, not an HTTP proposal. Its
caller owns proposal/review and job admission. It never creates a case, assumes
a version for a legacy document, or treats APPROVED as an executed graph.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from modules.listing.domain.identity_graph import (
    IdentityConflictError,
    IdentityCycleError,
    IdentityNotFoundError,
)
from shared.domain.events import DomainEvent, validate_event
from shared.jobs.queue import JobFenceRejectedError, JobRecord, JobStatus


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _time(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


class CanonicalIdentityGraphRepository:
    """One SQL transaction fences the graph, business job, audit and outbox.

    The injected sink is the same CanonicalIntakeAuditSink as the Intake store;
    its server policy resolves retention and its delivery shares this engine.
    Transport lease and workflow.jobs fence are separate authorities.
    """

    job_type = "identity-execution"

    def __init__(self, engine: Any, queue: Any, audit_sink: Any, *, delivery_authenticator: Any = None) -> None:
        if (getattr(engine, "dialect", None) != "postgresql"
                or getattr(queue, "_engine", None) is not engine
                or getattr(getattr(audit_sink, "writer", None), "engine", None) is not engine):
            raise ValueError("Canonical identity requires one PostgreSQL engine")
        self.engine, self.queue, self.audit_sink = engine, queue, audit_sink
        self.delivery_authenticator = delivery_authenticator

    def delivery_reference(self, delivery: JobRecord) -> dict:
        if self.delivery_authenticator is not None:
            return self.delivery_authenticator.reference(delivery)
        # Explicit component fixtures retain their already accepted internal
        # transport. Runtime factory composition always injects authentication.
        return delivery.payload

    @contextmanager
    def transaction(self, tenant_id: str):
        """Never silently run graph writes in an outer READ COMMITTED unit."""
        UUID(tenant_id)
        nested = getattr(self.engine._local, "connection", None) is not None
        with self.engine.transaction():
            if not nested:
                self.engine.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            elif self.engine.query_one("SHOW transaction_isolation")["transaction_isolation"] != "serializable":
                raise IdentityConflictError("IDENTITY_SERIALIZABLE_TRANSACTION_REQUIRED")
            current = self.engine.query_one("SELECT current_setting('app.tenant_id', true) AS tenant")["tenant"]
            if current and current != tenant_id:
                raise PermissionError("TENANT_SCOPE_DENIED")
            self.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (tenant_id,))
            yield

    def _one(self, sql: str, params: tuple) -> dict:
        row = self.engine.query_one(sql, params)
        if row is None:
            raise IdentityNotFoundError("CANONICAL_IDENTITY_RESOURCE_NOT_FOUND")
        for name in ("before_graph", "after_graph", "payload", "result"):
            if isinstance(row.get(name), str):
                row[name] = json.loads(row[name])
        return row

    def _case(self, tenant: str, case_id: str) -> tuple[dict, dict]:
        case = self._one(
            "SELECT * FROM identity.match_cases WHERE tenant_id=? AND match_case_id=? FOR UPDATE",
            (tenant, case_id),
        )
        intake = self._one(
            "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
            (tenant, case["intake_id"]),
        )
        return case, intake

    def case_context(self, tenant: str, case_id: str) -> dict:
        """Select the actual canonical Intake anchor; no first-case fallback."""
        with self.transaction(tenant):
            case, intake = self._case(tenant, case_id)
            return {"match_case": case, "intake": intake}

    def _fence(self, tenant: str, decision_id: str, delivery: JobRecord,
               business_fence: int, *, allow_cancellation: bool = False) -> tuple[dict, dict, dict, dict]:
        # Lock the physical transport row before inspecting queue.get(). A
        # concurrent lease reclaim cannot invalidate the fence before commit.
        self._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        current = self.queue.get(delivery.job_id)
        now = _time(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])
        if (current is None or current.job_type != self.job_type
                or current.status != JobStatus.RUNNING
                or not current.locked_by or current.locked_by != delivery.locked_by
                or current.fence_token != delivery.fence_token
                or current.payload != delivery.payload
                or current.lease_expires_at is None or current.lease_expires_at <= now
                or self.delivery_reference(current) != {"tenant_id": tenant, "decision_id": decision_id,
                                                       "job_id": delivery.job_id}):
            raise JobFenceRejectedError("IDENTITY_DELIVERY_FENCE_REJECTED")
        job = self._one(
            "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
            (tenant, delivery.job_id),
        )
        if (job["job_type"] != self.job_type or job["aggregate_type"] != "identity_decision"
                or job["aggregate_id"] != decision_id or job["idempotency_key"] != decision_id
                or job["status"] not in {"RUNNING", "SUCCEEDED"} or job["fence_token"] != business_fence):
            raise JobFenceRejectedError("IDENTITY_BUSINESS_FENCE_REJECTED")
        completed = job["status"] == "SUCCEEDED"
        if not completed and ((job["cancellation_requested_at"] is not None and not allow_cancellation)
                or job["lease_expires_at"] is None or _time(job["lease_expires_at"]) <= now
                or _time(job["timeout_at"]) <= now):
            raise JobFenceRejectedError("IDENTITY_BUSINESS_FENCE_REJECTED")
        decision = self._one(
            "SELECT * FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=? FOR UPDATE",
            (tenant, decision_id),
        )
        case, intake = self._case(tenant, decision["match_case_id"])
        if ((not completed and (decision["status"] != "EXECUTING" or case["status"] != "EXECUTING"))
                or not decision["reviewer_subject_id"] or not decision["risk_acknowledged"]
                or decision["reviewer_subject_id"] == decision["proposer_subject_id"]
                or decision["reviewed_at"] is None):
            raise IdentityConflictError("IDENTITY_EXECUTION_NOT_ACCEPTED")
        acceptance = job["payload"]
        if ((not completed and (acceptance.get("decision_version") != decision["version"]
                or acceptance.get("match_case_version") != case["version"]
                or acceptance.get("delivery_fence") != current.fence_token
                or acceptance.get("delivery_worker") != current.locked_by))
                or acceptance.get("plan_sha256") != hashlib.sha256(_json(decision["after_graph"]["plan"]).encode()).hexdigest()):
            raise JobFenceRejectedError("IDENTITY_ACCEPTANCE_BINDING_REJECTED")
        return job, decision, case, intake

    def _no_hold(self, tenant: str, kind: str, resource_id: str, *, held: bool = False) -> None:
        if held or self.engine.query_one(
            "SELECT legal_hold_id FROM audit.legal_holds WHERE tenant_id=? "
            "AND subject_type=? AND subject_id=? AND released_at IS NULL",
            (tenant, kind, resource_id),
        ):
            raise IdentityConflictError("LEGAL_HOLD_CONFLICT")

    def _resources(self, tenant: str, plan: dict, intake: dict) -> dict[str, dict]:
        versions = plan["expected_property_versions"]
        if not versions:
            raise IdentityConflictError("PROPERTY_VERSIONS_REQUIRED")
        properties = {}
        for key, version in sorted(versions.items()):
            row = self._one(
                "SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=? FOR UPDATE",
                (tenant, key),
            )
            if type(version) is not int or version < 1 or row["version"] != version:
                raise IdentityConflictError("VERSION_CONFLICT")
            if row["status"] != "ACTIVE" or row["current_redirect_property_id"] is not None:
                raise IdentityConflictError("PROPERTY_TERMINAL")
            self._no_hold(tenant, "property", key)
            properties[key] = row
        self._no_hold(tenant, "intake", intake["intake_id"], held=intake["legal_hold"])
        if intake["deleted_at"] or intake["processing_state"] in {"CANCELLED", "FAILED", "QUARANTINED"}:
            raise IdentityConflictError("INTAKE_TERMINAL")
        # SERIALIZABLE also fences phantoms; per-row versions guard all rows
        # selected at proposal time, even if another writer uses another lock.
        listings = self.engine.query(
            "SELECT * FROM expansion.listings WHERE tenant_id=? AND (property_id=ANY(?::uuid[]) "
            "OR listing_id IN (SELECT listing_id FROM identity.source_identity_edges WHERE tenant_id=? "
            "AND property_id=ANY(?::uuid[]) AND effective_to IS NULL)) "
            "ORDER BY listing_id FOR UPDATE", (tenant, list(properties), tenant, list(properties)),
        )
        expected = plan["expected_listing_versions"]
        if (any(type(v) is not int or v < 1 for v in expected.values())
                or {row["listing_id"]: row["version"] for row in listings} != expected):
            raise IdentityConflictError("LISTING_VERSION_CONFLICT")
        for row in listings:
            self._no_hold(tenant, "listing", row["listing_id"], held=row["legal_hold"])
            if row["lifecycle_state"] in {"ARCHIVED", "QUARANTINED"}:
                raise IdentityConflictError("LISTING_TERMINAL")
        return properties

    def _edges(self, tenant: str, source_ids: list[str], expected: dict) -> list[dict]:
        rows = self.engine.query(
            "SELECT * FROM identity.source_identity_edges WHERE tenant_id=? "
            "AND property_id=ANY(?::uuid[]) AND effective_to IS NULL ORDER BY edge_id FOR UPDATE",
            (tenant, source_ids),
        )
        if (any(type(v) is not int or v < 1 for v in expected.values())
                or {r["edge_id"]: r["edge_version"] for r in rows} != expected):
            raise IdentityConflictError("SOURCE_EDGE_VERSION_CONFLICT")
        return rows

    def _move_edge(self, tenant: str, row: dict, target: str, decision_id: str, at: str) -> str:
        changed = self.engine.execute(
            "UPDATE identity.source_identity_edges SET effective_to=? WHERE tenant_id=? "
            "AND edge_id=? AND edge_version=? AND effective_to IS NULL AND effective_from<?",
            (at, tenant, row["edge_id"], row["edge_version"], at),
        )
        if changed.rowcount != 1:
            raise IdentityConflictError("SOURCE_EDGE_VERSION_CONFLICT")
        edge_id = str(uuid4())
        self.engine.execute(
            "INSERT INTO identity.source_identity_edges (edge_id,tenant_id,source_id,source_entity_id,"
            "listing_id,property_id,match_strategy,confidence,decision_id,supersedes_edge_id,"
            "effective_from,edge_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (edge_id, tenant, row["source_id"], row["source_entity_id"], row["listing_id"], target,
             row["match_strategy"], row["confidence"], decision_id, row["edge_id"], at,
             row["edge_version"] + 1),
        )
        return edge_id

    def _redirect(self, tenant: str, source: str, target: str, decision_id: str, at: str) -> str:
        # UNION (not UNION ALL) bounds traversal even if corrupt historical
        # data already contains a cycle. The new edge must not reach itself.
        cycle = self.engine.query_one(
            "WITH RECURSIVE reachable(property_id) AS (SELECT ?::uuid UNION "
            "SELECT r.to_property_id FROM identity.property_redirects r JOIN reachable p "
            "ON r.from_property_id=p.property_id WHERE r.tenant_id=? AND r.reversed_at IS NULL) "
            "SELECT property_id FROM reachable WHERE property_id=?::uuid",
            (target, tenant, source),
        )
        if cycle:
            raise IdentityCycleError("PROPERTY_REDIRECT_CYCLE")
        redirect_id = str(uuid4())
        self.engine.execute(
            "INSERT INTO identity.property_redirects (redirect_id,tenant_id,from_property_id,"
            "to_property_id,decision_id,effective_from) VALUES (?,?,?,?,?,?)",
            (redirect_id, tenant, source, target, decision_id, at),
        )
        return redirect_id

    def _candidates(self, tenant: str, source_ids: list[str], plan: list[dict],
                    properties: dict[str, dict]) -> list[dict]:
        candidates = self.engine.query(
            "SELECT * FROM expansion.candidate_sites WHERE tenant_id=? AND property_id=ANY(?::uuid[]) "
            "ORDER BY candidate_site_id FOR UPDATE", (tenant, source_ids),
        )
        indexed = {entry["candidate_site_id"]: entry for entry in plan}
        if len(indexed) != len(plan) or set(indexed) != {c["candidate_site_id"] for c in candidates}:
            raise IdentityConflictError("CANDIDATE_DISPOSITION_REQUIRED")
        changed = []
        for row in candidates:
            item = indexed[row["candidate_site_id"]]
            if type(item["expected_version"]) is not int or item["expected_version"] != row["version"]:
                raise IdentityConflictError("CANDIDATE_VERSION_CONFLICT")
            self._no_hold(tenant, "candidate_site", row["candidate_site_id"])
            disposition = item["disposition"]
            if disposition in {"KEEP_HISTORICAL", "REQUIRE_REVIEW"}:
                if item.get("target_property_id") is not None:
                    raise IdentityConflictError("CANDIDATE_PLAN_CONFLICT")
                continue
            target = item.get("target_property_id")
            if disposition != "REASSIGN" or target not in properties or target in source_ids:
                raise IdentityConflictError("CANDIDATE_PLAN_CONFLICT")
            if row["status"] not in {"REJECTED", "OPENED"} and self.engine.query_one(
                "SELECT candidate_site_id FROM expansion.candidate_sites WHERE tenant_id=? AND property_id=? "
                "AND target_format_code=? AND status NOT IN ('REJECTED','OPENED') AND candidate_site_id<>? FOR UPDATE",
                (tenant, target, row["target_format_code"], row["candidate_site_id"]),
            ):
                raise IdentityConflictError("CANDIDATE_PLAN_CONFLICT")
            try:
                result = self.engine.execute(
                    "UPDATE expansion.candidate_sites SET property_id=?,version=version+1,updated_at=clock_timestamp() "
                    "WHERE tenant_id=? AND candidate_site_id=? AND version=?",
                    (target, tenant, row["candidate_site_id"], row["version"]),
                )
            except Exception as exc:
                if (getattr(exc, "sqlstate", None) == "23505"
                        and getattr(getattr(exc, "diag", None), "constraint_name", None) == "ux_active_candidate_property_format"):
                    raise IdentityConflictError("CANDIDATE_PLAN_CONFLICT") from exc
                raise
            if result.rowcount != 1:
                raise IdentityConflictError("CANDIDATE_VERSION_CONFLICT")
            changed.append({"candidate_site_id": row["candidate_site_id"],
                            "from_property_id": row["property_id"], "to_property_id": target,
                            "version": row["version"] + 1})
        return changed

    def _outbox(self, event: DomainEvent) -> None:
        if errors := validate_event(event):
            raise IdentityConflictError("IDENTITY_EVENT_CONTRACT_CONFLICT: " + "; ".join(errors))
        # ODP-SD-INTAKE-EVENTS-001 transport.outbox_retention_days=30;
        # independent of the server's audit retention policy.
        self.engine.execute(
            "INSERT INTO workflow.outbox_events (tenant_id,event_id,event_type,event_version,aggregate_type,"
            "aggregate_id,aggregate_version,partition_key,payload,correlation_id,occurred_at,retention_until) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event.tenant_id, event.event_id, event.event_type, event.event_version, event.aggregate_type,
             event.aggregate_id, event.aggregate_version, event.partition_key, _json(event.to_dict()),
             event.correlation_id, event.occurred_at.isoformat(), (event.occurred_at + timedelta(days=30)).isoformat()),
        )

    def _match_event(self, tenant: str, decision: dict, *, status: str, case_version: int,
                     correlation_id: str, at: str) -> None:
        case_id = decision["match_case_id"]
        self._outbox(DomainEvent(
            event_type="match.decided", tenant_id=tenant, aggregate_type="match_case", aggregate_id=case_id,
            aggregate_version=case_version, partition_key=tenant + ":" + case_id, correlation_id=correlation_id,
            producer="identity_resolution_service", schema_ref="#/payloads/MatchDecidedV1", occurred_at=_time(at),
            payload={"match_decision_id": decision["match_decision_id"], "match_case_id": case_id,
                     "decision_type": decision["decision_type"], "status": status,
                     "proposer_subject_id": decision["proposer_subject_id"],
                     "reviewer_subject_id": decision["reviewer_subject_id"]},
        ))

    def execute(self, tenant: str, decision_id: str, delivery: JobRecord, *, business_fence: int) -> dict:
        """Commit a previously accepted MERGE/SPLIT or roll back every write.

        Admission and failure settlement are intentionally separate commands:
        a caller must persist EXECUTING before invoking this method, and record
        FAILED after a failed transaction. No auto-replay of serialized writes.
        """
        if getattr(self.engine._local, "connection", None) is not None:
            raise IdentityConflictError("IDENTITY_EXECUTION_MUST_OWN_TRANSACTION")
        with self.transaction(tenant):
            job, decision, case, intake = self._fence(tenant, decision_id, delivery, business_fence)
            if job["status"] == "SUCCEEDED":
                result = job["result"]
                audit = self.audit_sink.writer.get_event(tenant, result["audit_event_id"])
                if (decision["after_graph"].get("execution") != result
                        or result.get("decision_id") != decision_id or result.get("job_id") != delivery.job_id
                        or audit is None or audit["action"] != "identity.executed.v1"
                        or audit["resource_id"] != decision_id or audit["after_value"].get("execution") != result):
                    raise IdentityConflictError("IDENTITY_EXECUTION_RECEIPT_CONFLICT")
                return result
            plan = decision["after_graph"]["plan"]
            properties = self._resources(tenant, plan, intake)
            kind = decision["decision_type"]
            assignments: dict[str, str] = {}
            if kind == "MERGE":
                sources, target = plan["source_property_ids"], plan["target_property_id"]
                if not sources or len(set(sources)) != len(sources) or target in sources:
                    raise IdentityConflictError("IDENTITY_SELF_OR_DUPLICATE_TARGET")
                if set(properties) != set(sources) | {target}:
                    raise IdentityConflictError("PROPERTY_VERSION_SET_CONFLICT")
                edges = self._edges(tenant, sources, plan["expected_edge_versions"])
                assignments = {edge["edge_id"]: target for edge in edges}
            elif kind == "SPLIT":
                sources = [plan["source_property_id"]]
                partitions = plan["partitions"]
                if len(partitions) < 2:
                    raise IdentityConflictError("IDENTITY_PARTITIONS_REQUIRED")
                for partition in partitions:
                    target = partition["target_property_id"]
                    if target not in properties or target in sources or not partition["source_identity_edge_ids"]:
                        raise IdentityConflictError("IDENTITY_PARTITION_TARGET_CONFLICT")
                    for edge_id in partition["source_identity_edge_ids"]:
                        if edge_id in assignments:
                            raise IdentityConflictError("IDENTITY_PARTITION_OVERLAP")
                        assignments[edge_id] = target
                if set(properties) != set(sources) | {p["target_property_id"] for p in partitions}:
                    raise IdentityConflictError("PROPERTY_VERSION_SET_CONFLICT")
                edges = self._edges(tenant, sources, plan["expected_edge_versions"])
                if set(assignments) != {edge["edge_id"] for edge in edges}:
                    raise IdentityConflictError("IDENTITY_PARTITION_SET_CONFLICT")
            else:
                raise IdentityConflictError("IDENTITY_EXECUTION_KIND_UNSUPPORTED")
            at = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            changed_candidates = self._candidates(tenant, sources, plan["candidate_reassignment_plan"], properties)
            edge_ids = [self._move_edge(tenant, edge, assignments[edge["edge_id"]], decision_id, at) for edge in edges]
            redirect_ids = [self._redirect(tenant, source, plan["target_property_id"], decision_id, at)
                            for source in sources] if kind == "MERGE" else []
            versions = {}
            for property_id, row in properties.items():
                state = ("MERGED" if kind == "MERGE" else "SPLIT") if property_id in sources else "ACTIVE"
                redirect = plan["target_property_id"] if kind == "MERGE" and property_id in sources else None
                updated = self.engine.query_one(
                    "UPDATE identity.properties SET status=?,current_redirect_property_id=?,version=version+1,"
                    "updated_at=? WHERE tenant_id=? AND property_id=? AND version=? RETURNING version",
                    (state, redirect, at, tenant, property_id, row["version"]),
                )
                if updated is None:
                    raise IdentityConflictError("VERSION_CONFLICT")
                versions[property_id] = updated["version"]
            audit_id = str(uuid4())
            result = {"decision_id": decision_id, "status": "EXECUTED", "job_id": delivery.job_id,
                      "audit_event_id": audit_id,
                      "property_versions": versions, "created_edge_ids": edge_ids,
                      "created_redirect_ids": redirect_ids, "candidate_reassignments": changed_candidates}
            after_graph = {**decision["after_graph"], "execution": result}
            for sql, params in (
                ("UPDATE identity.match_decisions SET status='EXECUTED',version=version+1,executed_at=?,after_graph=?::jsonb "
                 "WHERE tenant_id=? AND match_decision_id=? AND status='EXECUTING' AND version=?",
                 (at, _json(after_graph), tenant, decision_id, decision["version"])),
                ("UPDATE identity.match_cases SET status='EXECUTED',version=version+1,updated_at=? "
                 "WHERE tenant_id=? AND match_case_id=? AND status='EXECUTING' AND version=?",
                 (at, tenant, case["match_case_id"], case["version"])),
                ("UPDATE workflow.jobs SET status='SUCCEEDED',checkpoint='EXECUTED',result=?::jsonb,version=version+1,"
                 "updated_at=? WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=?",
                 (_json(result), at, tenant, delivery.job_id, job["version"], business_fence)),
            ):
                if self.engine.execute(sql, params).rowcount != 1:
                    raise IdentityConflictError("VERSION_CONFLICT")
            scope = {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")}
            self.audit_sink.persist({
                "audit_event_id": audit_id, "tenant_id": tenant, "resource_type": "identity_decision",
                "resource_id": decision_id, "action": "identity.executed.v1", "result": "SUCCEEDED",
                "service_principal": "identity-execution", "correlation_id": job["correlation_id"],
                "occurred_at": at, "before_value": decision["before_graph"], "after_value": after_graph,
                "reason": decision["reason"],
            }, scope)
            for target in sorted(set(assignments.values()) | ({plan["target_property_id"]} if kind == "MERGE" else set())):
                pairs = [(old["edge_id"], new) for old, new in zip(edges, edge_ids, strict=True)
                         if assignments[old["edge_id"]] == target]
                event = DomainEvent(
                    event_type="identity.resolution_changed", tenant_id=tenant, aggregate_type="property",
                    aggregate_id=target, aggregate_version=versions[target], partition_key=tenant + ":" + target,
                    correlation_id=job["correlation_id"], producer="identity_resolution_service",
                    schema_ref="#/payloads/IdentityResolutionChangedV1", occurred_at=_time(at),
                    payload={"decision_id": decision_id, "effective_property_id": target,
                             "closed_edge_ids": [old for old, _ in pairs],
                             "created_edge_ids": [new for _, new in pairs], "redirect_ids": redirect_ids},
                )
                self._outbox(event)
            self._match_event(tenant, decision, status="EXECUTED", case_version=case["version"] + 1,
                              correlation_id=job["correlation_id"], at=at)
            # Recheck real clocks after audit/policy work, not transaction-start
            # now(): an expired lease/deadline cannot authorize a late commit.
            eligible = self.engine.query_one(
                "SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_id=? "
                "AND timeout_at>clock_timestamp() AND lease_expires_at>clock_timestamp() "
                "AND cancellation_requested_at IS NULL",
                (tenant, delivery.job_id),
            )
            if eligible is None:
                raise JobFenceRejectedError("IDENTITY_BUSINESS_LEASE_EXPIRED")
            if current := self.queue.get(delivery.job_id):
                if current.lease_expires_at and current.lease_expires_at > _time(
                    self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
                ):
                    return result
            raise JobFenceRejectedError("IDENTITY_DELIVERY_LEASE_EXPIRED")

    def resolve_listing(self, tenant: str, listing_id: str, *, as_of: datetime | None = None) -> dict:
        """Resolve native edges/redirects without changing historical references."""
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError("as_of requires an explicit timezone")
        with self.transaction(tenant):
            listing = self._one(
                "SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
                (tenant, listing_id),
            )
            at = as_of.isoformat() if as_of else self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            if _time(listing["created_at"]) > _time(at):
                raise IdentityNotFoundError("LISTING_NOT_YET_CREATED")
            edges = self.engine.query(
                "SELECT * FROM identity.source_identity_edges WHERE tenant_id=? AND listing_id=? "
                "AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)",
                (tenant, listing_id, at, at),
            )
            if len(edges) > 1:
                raise IdentityConflictError("AMBIGUOUS_LISTING_SOURCE_IDENTITY")
            original = listing["property_id"]
            current = edges[0]["property_id"] if edges else original
            if current is None:
                raise IdentityNotFoundError("LISTING_PROPERTY_NOT_BOUND")
            path = [current]
            while True:
                redirect = self.engine.query_one(
                    "SELECT to_property_id FROM identity.property_redirects WHERE tenant_id=? "
                    "AND from_property_id=? AND effective_from<=? AND (reversed_at IS NULL OR reversed_at>?)",
                    (tenant, current, at, at),
                )
                if redirect is None:
                    break
                current = redirect["to_property_id"]
                if current in path:
                    raise IdentityCycleError("PROPERTY_REDIRECT_CYCLE")
                path.append(current)
            prop = self._one("SELECT version FROM identity.properties WHERE tenant_id=? AND property_id=?", (tenant, current))
            return {"listing_id": listing_id, "listing_version": listing["version"],
                    "property_id_at_creation": original, "effective_property_id": current,
                    "current_property_version": prop["version"], "redirect_path": path,
                    "source_edge_id": edges[0]["edge_id"] if edges else None,
                    "as_of": at}


__all__ = ["CanonicalIdentityGraphRepository"]
