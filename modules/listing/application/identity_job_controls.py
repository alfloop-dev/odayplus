"""Stop native identity work without granting a new execution or replay."""

from __future__ import annotations

import hashlib
from uuid import uuid4

from modules.listing.domain.identity_graph import IdentityConflictError
from shared.infrastructure.persistence.identity_decisions_sql import (
    CanonicalIdentityDecisionRepository,
)
from shared.jobs.queue import JobStatus


class IdentityJobControls:
    def __init__(self, graph):
        self.graph, self.engine = graph, graph.engine
        self.decisions = CanonicalIdentityDecisionRepository(graph)

    def context(self, tenant: str, decision_id: str, *, for_update: bool = True) -> dict:
        """Caller owns the snapshot; mutation callers lock in worker order."""
        reference = self.graph._one("SELECT job_id FROM workflow.jobs WHERE tenant_id=? "
            "AND aggregate_id=? AND aggregate_type='identity_decision' AND job_type='identity-execution'",
            (tenant, decision_id))
        job_id = reference["job_id"]
        self.graph._one(
            ("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE"
             if for_update else "SELECT job_id FROM durable_jobs WHERE job_id=?"),
            (job_id,),
        )
        delivery = self.graph.queue.get(job_id)
        job = self.graph._one(
            ("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE"
             if for_update else "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?"),
            (tenant, job_id))
        if (delivery is None or delivery.job_type != self.graph.job_type
                or set(delivery.payload) != {"job_id", "tenant_id", "attempt_token"}
                or delivery.payload.get("job_id") != job_id or delivery.payload.get("tenant_id") != tenant
                or not isinstance(delivery.payload.get("attempt_token"), str)
                or hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()
                != job["payload"].get("attempt_token_sha256")
                or job["aggregate_id"] != decision_id or job["idempotency_key"] != decision_id):
            raise IdentityConflictError("IDENTITY_JOB_DELIVERY_BINDING_REJECTED")
        selected = self.decisions.get(tenant, decision_id, for_update=for_update)
        return {**selected, "job": job, "delivery": delivery}

    @staticmethod
    def summary(selected: dict) -> dict:
        job = selected["job"]
        return {"job_id": job["job_id"], "decision_id": selected["decision"]["match_decision_id"],
            "match_case_id": selected["match_case"]["match_case_id"], "intake_id": selected["intake"]["intake_id"],
            "version": job["version"], "status": job["status"], "checkpoint": job["checkpoint"],
            "attempt": job["attempt"], "max_attempts": job["max_attempts"], "timeout_at": job["timeout_at"],
            "cancellation_requested_at": job["cancellation_requested_at"],
            "correlation_id": job["correlation_id"]}

    def cancel(self, tenant: str, decision_id: str, *, expected_version: int, actor_id: str, reason: str) -> dict:
        with self.graph.transaction(tenant):
            selected = self.context(tenant, decision_id)
            job, delivery = selected["job"], selected["delivery"]
            if (job["version"] != expected_version or job["status"] not in {"QUEUED", "RETRYING", "RUNNING"}
                    or job["cancellation_requested_at"] is not None
                    or delivery.status not in {JobStatus.QUEUED, JobStatus.RUNNING}
                    or (job["status"] == "RUNNING" and delivery.status != JobStatus.RUNNING)):
                raise IdentityConflictError("IDENTITY_JOB_CANNOT_CANCEL")
            # No worker owns a queued delivery. A claimed delivery must be
            # acknowledged by its still-current worker, never by this API.
            immediate = delivery.status == JobStatus.QUEUED
            status = "CANCELLED" if immediate else job["status"]
            at = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            if self.engine.execute("UPDATE workflow.jobs SET status=?,cancellation_requested_at=?,"
                "version=version+1,updated_at=? WHERE tenant_id=? AND job_id=? AND version=?",
                (status, at, at, tenant, job["job_id"], expected_version)).rowcount != 1:
                raise IdentityConflictError("VERSION_CONFLICT")
            if immediate:
                self.graph.queue.update_status(job["job_id"], JobStatus.CANCELLED,
                    expected_version=delivery.version, fence_token=delivery.fence_token)
            selected["job"] = {**job, "status": status, "cancellation_requested_at": at,
                               "version": expected_version + 1}
            result = self.summary(selected)
            audit_id = str(uuid4())
            self.decisions._audit(tenant, selected["intake"], actor_id, decision_id,
                {"job_id": job["job_id"], "status": job["status"], "version": expected_version}, result,
                "identity.cancellation_requested.v1", reason, at, audit_id=audit_id)
            return {**result, "audit_event_id": audit_id}
