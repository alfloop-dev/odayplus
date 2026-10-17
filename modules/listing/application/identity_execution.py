"""Accept and settle a canonical identity job without hiding failed graph writes.

Job admission supplies workflow.jobs and its approved attempt/deadline policy.
This service does not invent that policy or manufacture an Intake/match case.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from modules.listing.domain.identity_graph import IdentityConflictError
from shared.jobs.queue import JobFenceRejectedError, JobRecord, JobStatus


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class IdentityExecutionService:
    def __init__(self, graph: Any) -> None:
        self.graph, self.engine = graph, graph.engine

    def _delivery(self, tenant: str, decision_id: str, delivery: JobRecord) -> JobRecord:
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        current = self.graph.queue.get(delivery.job_id)
        now = datetime.fromisoformat(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])
        if (current is None or current.job_type != self.graph.job_type or current.status != JobStatus.RUNNING
                or not current.locked_by or current.locked_by != delivery.locked_by
                or current.fence_token != delivery.fence_token or current.payload != delivery.payload
                or self.graph.delivery_reference(current) != {"tenant_id": tenant, "decision_id": decision_id, "job_id": delivery.job_id}
                or current.lease_expires_at is None or current.lease_expires_at <= now):
            raise JobFenceRejectedError("IDENTITY_DELIVERY_FENCE_REJECTED")
        return current

    def _event(self, tenant: str, job: dict, decision: dict, intake: dict, *,
               event_type: str, before: dict, after: dict, case_version: int, result: str) -> None:
        at = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
        self.graph.audit_sink.persist({
            "audit_event_id": str(uuid4()), "tenant_id": tenant, "resource_type": "identity_decision",
            "resource_id": decision["match_decision_id"], "action": event_type, "result": result,
            "service_principal": "identity-execution", "correlation_id": job["correlation_id"],
            "occurred_at": at, "before_value": before, "after_value": after, "reason": decision["reason"],
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
        self.graph._match_event(tenant, decision, status=after["status"], case_version=case_version,
                                correlation_id=job["correlation_id"], at=at)

    def begin(self, tenant: str, decision_id: str, delivery: JobRecord) -> int:
        """A preaccept failure rolls back and leaves the decision APPROVED."""
        if getattr(self.engine._local, "connection", None) is not None:
            raise IdentityConflictError("IDENTITY_ACCEPTANCE_MUST_OWN_TRANSACTION")
        with self.graph.transaction(tenant):
            current = self._delivery(tenant, decision_id, delivery)
            job = self.graph._one(
                "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE", (tenant, delivery.job_id),
            )
            if job["status"] == "SUCCEEDED":
                # execute() verifies the persisted decision/result/audit and
                # returns the original receipt without another graph mutation.
                return job["fence_token"]
            eligible = self.engine.query_one(
                "SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_id=? AND status IN ('QUEUED','RETRYING') "
                "AND timeout_at>clock_timestamp() AND cancellation_requested_at IS NULL "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=clock_timestamp()) AND attempt<max_attempts",
                (tenant, delivery.job_id),
            )
            if (eligible is None or job["job_type"] != self.graph.job_type
                    or job["aggregate_type"] != "identity_decision" or job["aggregate_id"] != decision_id
                    or job["idempotency_key"] != decision_id):
                raise JobFenceRejectedError("IDENTITY_ADMISSION_FENCE_REJECTED")
            decision = self.graph._one(
                "SELECT * FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=? FOR UPDATE",
                (tenant, decision_id),
            )
            case, intake = self.graph._case(tenant, decision["match_case_id"])
            if (decision["status"] != "APPROVED" or case["status"] != "APPROVED"
                    or decision["decision_type"] not in {"MERGE", "SPLIT"}
                    or not decision["reviewer_subject_id"] or decision["reviewed_at"] is None
                    or decision["reviewer_subject_id"] == decision["proposer_subject_id"]
                    or not decision["risk_acknowledged"]):
                raise IdentityConflictError("IDENTITY_DECISION_NOT_APPROVED")
            plan = decision["after_graph"]["plan"]
            payload = job["payload"]
            if (payload.get("decision_version") != decision["version"]
                    or payload.get("match_case_version") != case["version"]
                    or payload.get("plan_sha256") != hashlib.sha256(_json(plan).encode()).hexdigest()):
                raise JobFenceRejectedError("IDENTITY_ACCEPTANCE_BINDING_REJECTED")
            self.graph._resources(tenant, plan, intake)
            sources = plan["source_property_ids"] if decision["decision_type"] == "MERGE" else [plan["source_property_id"]]
            self.graph._edges(tenant, sources, plan["expected_edge_versions"])
            candidates = self.engine.query(
                "SELECT candidate_site_id,version FROM expansion.candidate_sites WHERE tenant_id=? "
                "AND property_id=ANY(?::uuid[]) ORDER BY candidate_site_id FOR UPDATE", (tenant, sources),
            )
            candidate_plan = plan["candidate_reassignment_plan"]
            if (len({c["candidate_site_id"] for c in candidate_plan}) != len(candidate_plan)
                    or {c["candidate_site_id"]: c["expected_version"] for c in candidate_plan}
                    != {c["candidate_site_id"]: c["version"] for c in candidates}):
                raise IdentityConflictError("CANDIDATE_VERSION_CONFLICT")
            fence = job["fence_token"] + 1
            payload = {**payload, "decision_version": decision["version"] + 1,
                       "match_case_version": case["version"] + 1,
                       "delivery_fence": current.fence_token, "delivery_worker": current.locked_by}
            for sql, params in (
                ("UPDATE identity.match_decisions SET status='EXECUTING',version=version+1 "
                 "WHERE tenant_id=? AND match_decision_id=? AND version=? AND status='APPROVED'",
                 (tenant, decision_id, decision["version"])),
                ("UPDATE identity.match_cases SET status='EXECUTING',version=version+1,updated_at=clock_timestamp() "
                 "WHERE tenant_id=? AND match_case_id=? AND version=? AND status='APPROVED'",
                 (tenant, case["match_case_id"], case["version"])),
                ("UPDATE workflow.jobs SET status='RUNNING',checkpoint='EXECUTING',attempt=attempt+1,fence_token=?,"
                 "lease_expires_at=?,heartbeat_at=clock_timestamp(),payload=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                 "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=?",
                 (fence, current.lease_expires_at.isoformat(), _json(payload), tenant, delivery.job_id, job["version"], job["fence_token"])),
            ):
                if self.engine.execute(sql, params).rowcount != 1:
                    raise IdentityConflictError("VERSION_CONFLICT")
            self._event(tenant, job, decision, intake, event_type="identity.execution_started.v1",
                        before={"status": "APPROVED"}, after={"status": "EXECUTING", "job_id": delivery.job_id,
                        "business_fence": fence}, case_version=case["version"] + 1, result="SUCCEEDED")
            # Policy/audit work may be slow. Acceptance cannot be committed
            # after either the transport lease or canonical deadline expires.
            self._delivery(tenant, decision_id, delivery)
            if self.engine.query_one(
                "SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_id=? AND timeout_at>clock_timestamp()",
                (tenant, delivery.job_id),
            ) is None:
                raise JobFenceRejectedError("IDENTITY_BUSINESS_LEASE_EXPIRED")
            return fence

    def fail(self, tenant: str, decision_id: str, delivery: JobRecord, *, business_fence: int) -> None:
        """Settle only the still-owned attempt after graph SQL has rolled back."""
        if getattr(self.engine._local, "connection", None) is not None:
            raise IdentityConflictError("IDENTITY_FAILURE_MUST_OWN_TRANSACTION")
        with self.graph.transaction(tenant):
            job, decision, case, intake = self.graph._fence(tenant, decision_id, delivery, business_fence,
                                                         allow_cancellation=True)
            if job["status"] != "RUNNING":
                raise JobFenceRejectedError("IDENTITY_FAILED_ATTEMPT_NOT_OWNED")
            cancelled = job["cancellation_requested_at"] is not None
            job_status = "CANCELLED" if cancelled else "FAILED"
            failure = {"status": "FAILED", "job_id": delivery.job_id, "business_fence": business_fence,
                       "attempt": job["attempt"], "reason_code": "IDENTITY_CANCELLATION_REQUESTED" if cancelled
                       else "IDENTITY_GRAPH_TRANSACTION_FAILED"}
            after_graph = {**decision["after_graph"], "execution_failure": failure}
            for sql, params in (
                ("UPDATE identity.match_decisions SET status='FAILED',version=version+1,after_graph=?::jsonb "
                 "WHERE tenant_id=? AND match_decision_id=? AND status='EXECUTING' AND version=?",
                 (_json(after_graph), tenant, decision_id, decision["version"])),
                ("UPDATE identity.match_cases SET status='FAILED',version=version+1,updated_at=clock_timestamp() "
                 "WHERE tenant_id=? AND match_case_id=? AND status='EXECUTING' AND version=?",
                 (tenant, case["match_case_id"], case["version"])),
                ("UPDATE workflow.jobs SET status=?,checkpoint=?,last_error=?::jsonb,version=version+1,"
                 "updated_at=clock_timestamp() WHERE tenant_id=? AND job_id=? AND status='RUNNING' AND version=? AND fence_token=?",
                 (job_status, job_status, _json(failure), tenant, delivery.job_id, job["version"], business_fence)),
            ):
                if self.engine.execute(sql, params).rowcount != 1:
                    raise JobFenceRejectedError("IDENTITY_FAILED_ATTEMPT_NOT_OWNED")
            self._event(tenant, job, decision, intake, event_type="identity.execution_failed.v1",
                        before={"status": "EXECUTING"}, after={**after_graph, "status": "FAILED"},
                        case_version=case["version"] + 1, result="FAILED")
            self._delivery(tenant, decision_id, delivery)
            if self.engine.query_one(
                "SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_id=? "
                "AND timeout_at>clock_timestamp() AND lease_expires_at>clock_timestamp()",
                (tenant, delivery.job_id),
            ) is None:
                raise JobFenceRejectedError("IDENTITY_BUSINESS_LEASE_EXPIRED")

    def run(self, delivery: JobRecord) -> dict:
        with self.engine.transaction():
            reference = self.graph.delivery_reference(delivery)
        tenant, decision_id = reference["tenant_id"], reference["decision_id"]
        UUID(tenant)
        UUID(decision_id)
        fence = self.begin(tenant, decision_id, delivery)
        try:
            return self.graph.execute(tenant, decision_id, delivery, business_fence=fence)
        except Exception:
            # A lost/expired fence may prevent failure settlement; such an
            # attempt belongs to reconciliation, never to this stale worker.
            self.fail(tenant, decision_id, delivery, business_fence=fence)
            raise


__all__ = ["IdentityExecutionService"]
