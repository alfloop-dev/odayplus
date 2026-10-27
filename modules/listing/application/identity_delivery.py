"""Reserve native identity work and authenticate opaque delivery references.

Execution policy and signing material are deployment inputs. Neither this
module nor its test configuration grants a production rollout or approval.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from modules.listing.domain.identity_graph import IdentityConflictError
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import DomainEvent
from shared.infrastructure.persistence.identity_decisions_sql import (
    CanonicalIdentityDecisionRepository,
    canonical_identity_plan,
)
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobRequest, JobStatus

IDENTITY_JOB_TYPE = "identity-execution"
IDENTITY_POLICY_ENV = "ODP_IDENTITY_EXECUTION_POLICY_JSON"
IDENTITY_SIGNING_KEY_ENV = "ODP_IDENTITY_ATTEMPT_SIGNING_KEY"


class IdentityDeliveryUnavailable(RuntimeError):
    pass


def identity_execution_policy() -> dict:
    """Require an explicit policy; matching-stage budgets are not a fallback."""
    try:
        p = json.loads(os.environ.get(IDENTITY_POLICY_ENV, ""))
        if not isinstance(p, dict) or set(p) != {"policy_id", "version", "max_attempts", "deadline_seconds", "backoff_seconds"}:
            raise ValueError
        if (not isinstance(p["policy_id"], str) or not p["policy_id"].strip()
                or type(p["version"]) is not int or p["version"] < 1
                or type(p["max_attempts"]) is not int or p["max_attempts"] < 1
                or type(p["deadline_seconds"]) is not int or p["deadline_seconds"] < 1
                or not isinstance(p["backoff_seconds"], list)
                or len(p["backoff_seconds"]) != p["max_attempts"] - 1
                or any(type(v) not in {int, float} or not math.isfinite(v) or v <= 0 for v in p["backoff_seconds"])):
            raise ValueError
        return p
    except (ValueError, TypeError, OverflowError) as exc:
        raise IdentityDeliveryUnavailable("IDENTITY_EXECUTION_POLICY_UNAVAILABLE") from exc


class IdentityDeliveryAuthenticator:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def sign(self, job_id: str, tenant: str, generation: int) -> str:
        key = os.environ.get(IDENTITY_SIGNING_KEY_ENV, "").encode()
        if len(key) < 32:
            raise IdentityDeliveryUnavailable("IDENTITY_ATTEMPT_SIGNING_KEY_UNAVAILABLE")
        content = f"identity-execution:{job_id}:{tenant}:{generation}"
        return f"{generation}." + hmac.new(key, content.encode(), hashlib.sha256).hexdigest()

    def reference(self, delivery) -> dict:
        payload = delivery.payload
        try:
            if set(payload) != {"job_id", "tenant_id", "attempt_token"} or payload["job_id"] != delivery.job_id:
                raise ValueError
            tenant, job_id = str(UUID(payload["tenant_id"])), str(UUID(payload["job_id"]))
            token = payload["attempt_token"]
            generation = int(token.split(".", 1)[0])
            if generation < 1 or not hmac.compare_digest(token, self.sign(job_id, tenant, generation)):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise JobFenceRejectedError("IDENTITY_ATTEMPT_TOKEN_REJECTED") from exc
        # Verify the token before binding any tenant or loading SQL business
        # input. graph.transaction also refuses an already bound foreign tenant.
        current = self.engine.query_one("SELECT current_setting('app.tenant_id',true) AS tenant")["tenant"]
        if current and current != tenant:
            raise JobFenceRejectedError("IDENTITY_ATTEMPT_TENANT_REJECTED")
        self.engine.query_one("SELECT set_config('app.tenant_id',?,true)", (tenant,))
        row = self.engine.query_one("SELECT aggregate_id,aggregate_type,job_type,payload FROM workflow.jobs "
            "WHERE tenant_id=? AND job_id=?", (tenant, job_id))
        if row is None or row["aggregate_type"] != "identity_decision" or row["job_type"] != IDENTITY_JOB_TYPE:
            raise JobFenceRejectedError("IDENTITY_ADMISSION_REFERENCE_REJECTED")
        business = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        if (business.get("delivery_generation") != generation
                or business.get("attempt_token_sha256") != hashlib.sha256(token.encode()).hexdigest()):
            raise JobFenceRejectedError("IDENTITY_ATTEMPT_GENERATION_REJECTED")
        return {"job_id": job_id, "tenant_id": tenant, "decision_id": row["aggregate_id"]}


class IdentityJobAdmission:
    def __init__(self, graph: Any) -> None:
        self.graph, self.engine = graph, graph.engine
        self.decisions = CanonicalIdentityDecisionRepository(graph)
        self.auth = IdentityDeliveryAuthenticator(self.engine)

    def enqueue(self, tenant: str, decision_id: str, *, expected_version: int, actor_id: str, before=None) -> dict:
        policy = identity_execution_policy()
        if actor_id != "native-intake-identity":
            UUID(actor_id)
        with self.graph.transaction(tenant):
            if before is not None:
                before()
            decision = self.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? "
                "AND match_decision_id=? FOR UPDATE", (tenant, decision_id))
            case, intake = self.graph._case(tenant, decision["match_case_id"])
            service = decision.get("proposer_service_principal") is not None
            if service != (actor_id == "native-intake-identity"):
                raise PermissionError("IDENTITY_ADMISSION_ACTOR_KIND_CONFLICT")
            if decision["decision_type"] == "DUPLICATE" and not service and actor_id != decision["reviewer_subject_id"]:
                raise PermissionError("IDENTITY_ADMISSION_REVIEWER_REQUIRED")
            if (decision["version"] != expected_version or decision["status"] != "APPROVED"
                    or case["status"] != "APPROVED"
                    or decision["after_graph"].get("match_case_version") != case["version"]
                    or decision["after_graph"].get("intake_version") != intake["version"]):
                raise IdentityConflictError("IDENTITY_ADMISSION_REVIEW_BINDING_CONFLICT")
            self.decisions.require_approval(tenant, decision, error_code="IDENTITY_ADMISSION_REVIEW_BINDING_CONFLICT")
            plan = canonical_identity_plan(decision["decision_type"], decision["after_graph"]["plan"])
            self.decisions._snapshot(tenant, case, intake, decision["decision_type"], plan,
                confirmation_actor_id=decision["proposer_subject_id"], reviewer_actor_id=decision["reviewer_subject_id"],
                reason=decision["reason"], before=before)
            if before is not None:
                before()
            prior = self.engine.query_one("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type=? "
                "AND idempotency_key=?", (tenant, IDENTITY_JOB_TYPE, decision_id))
            if prior is not None:
                raise IdentityConflictError("IDENTITY_JOB_ALREADY_ADMITTED")
            job_id = str(uuid5(UUID(decision_id), IDENTITY_JOB_TYPE))
            token = self.auth.sign(job_id, tenant, 1)
            at = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            deadline = (datetime.fromisoformat(at) + timedelta(seconds=policy["deadline_seconds"])).isoformat()
            payload = {"decision_version": decision["version"], "match_case_version": case["version"],
                "plan_sha256": hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "policy": policy, "delivery_generation": 1,
                "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest(), "admitted_by": actor_id}
            if before is not None:
                before()
            self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,status,"
                "checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
                "VALUES (?,?,?,'identity_decision',?,'QUEUED','APPROVED',0,?,1,?,?::jsonb,?,?)",
                (job_id, tenant, IDENTITY_JOB_TYPE, decision_id, policy["max_attempts"], deadline,
                 json.dumps(payload), intake["correlation_id"], decision_id))
            delivery, created = self.graph.queue.enqueue(JobRequest(IDENTITY_JOB_TYPE,
                {"job_id": job_id, "tenant_id": tenant, "attempt_token": token},
                idempotency_key="identity-execution:"+tenant+":"+decision_id, job_id=job_id),
                correlation_id=intake["correlation_id"])
            if before is not None:
                before()
            if not created or delivery.job_id != job_id:
                raise IdentityConflictError("IDENTITY_DELIVERY_RESERVATION_CONFLICT")
            self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?", (policy["max_attempts"], job_id))
            audit_id = str(uuid4())
            receipt = {"decision_id": decision_id, "status": "APPROVED", "version": decision["version"],
                "job_id": job_id, "job_status": "QUEUED", "audit_event_id": audit_id,
                "correlation_id": intake["correlation_id"], "timeout_at": deadline}
            self.decisions._audit(tenant, intake, None if service else actor_id, decision_id, None, receipt,
                "identity.execution_requested.v1", decision["reason"], at, audit_id=audit_id,
                service_principal="native-intake-identity" if service else None)
            return receipt

    def _owned(self, delivery):
        from modules.listing.application.identity_execution import IdentityExecutionService

        reference = self.auth.reference(delivery)
        tenant, decision_id = reference["tenant_id"], reference["decision_id"]
        current = IdentityExecutionService(self.graph)._delivery(tenant, decision_id, delivery)
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
            (tenant, delivery.job_id))
        return reference, current, job

    def settle_delivery(self, delivery, *, error: Exception | None = None) -> None:
        """Settle a committed result or a still-owned preaccept failure.

        Execution failures already settled by the graph coordinator remain
        FAILED and require authorized replay; they are not silently reapproved.
        Retryable preaccept storage/audit failures follow the saved policy.
        """
        with self.engine.transaction():
            reference = self.auth.reference(delivery)
        tenant = reference["tenant_id"]
        with self.graph.transaction(tenant):
            reference, current, job = self._owned(delivery)
            if job["status"] == "SUCCEEDED":
                self.graph.queue.update_status(delivery.job_id, JobStatus.SUCCEEDED,
                    expected_version=current.version, fence_token=current.fence_token)
                return
            if job["status"] == "CANCELLED" and job["cancellation_requested_at"] is not None:
                self.graph.queue.update_status(delivery.job_id, JobStatus.CANCELLED,
                    expected_version=current.version, fence_token=current.fence_token)
                return
            if error is None:
                raise JobFenceRejectedError("IDENTITY_RESULT_NOT_COMMITTED")
            if job["status"] not in {"QUEUED", "RETRYING", "FAILED"}:
                raise JobFenceRejectedError("IDENTITY_FAILED_ATTEMPT_REQUIRES_RECONCILIATION")
            preaccept = job["status"] in {"QUEUED", "RETRYING"}
            now = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            cancelled = preaccept and job["cancellation_requested_at"] is not None
            stopped = preaccept and (cancelled or datetime.fromisoformat(job["timeout_at"]) <= datetime.fromisoformat(now)
                                    or job["attempt"] >= job["max_attempts"])
            # _owned has already revalidated the live transport fence. A
            # deadline/cancellation of still-queued business work is terminal,
            # whereas a lost execution fence still belongs to reconciliation.
            if isinstance(error, JobFenceRejectedError) and not stopped:
                raise error
            policy = job["payload"]["policy"]
            if not stopped and policy != identity_execution_policy():
                raise IdentityDeliveryUnavailable("IDENTITY_EXECUTION_POLICY_CHANGED")
            attempt = job["attempt"] + int(preaccept and job["attempt"] < job["max_attempts"])
            retryable = (getattr(error, "sqlstate", None) in {"40001", "40P01"}
                         or isinstance(error, CanonicalAuditUnavailable))
            due = None
            if preaccept and not stopped and retryable and attempt < job["max_attempts"]:
                due = datetime.fromisoformat(now) + timedelta(seconds=policy["backoff_seconds"][attempt-1])
                if due >= datetime.fromisoformat(job["timeout_at"]):
                    due = None
            status, code = ("RETRYING", "IDENTITY_PREACCEPT_RETRY") if due else ("FAILED", "IDENTITY_EXECUTION_FAILED")
            if cancelled:
                status, code = "CANCELLED", "IDENTITY_CANCELLATION_REQUESTED"
            if self.engine.execute("UPDATE workflow.jobs SET status=?,attempt=?,last_error=?::jsonb,next_attempt_at=?,"
                "version=version+1,updated_at=? WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=?",
                (status, attempt, json.dumps({"code": code, "error_class": type(error).__name__}),
                 due.isoformat() if due else None, now, tenant, delivery.job_id, job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("IDENTITY_FAILED_ATTEMPT_NOT_OWNED")
            context = self.decisions.get(tenant, reference["decision_id"])
            self.decisions._audit(tenant, context["intake"], job["payload"]["admitted_by"], reference["decision_id"],
                {"job_id": delivery.job_id, "status": job["status"], "attempt": job["attempt"]},
                {"job_id": delivery.job_id, "status": status, "attempt": attempt, "error_code": code},
                "identity.delivery_cancelled.v1" if cancelled else "identity.delivery_retry_requested.v1" if due else "identity.delivery_failed.v1",
                context["decision"]["reason"], now, audit_id=str(uuid4()))
            self.graph.queue.update_status(delivery.job_id,
                JobStatus.CANCELLED if cancelled else JobStatus.QUEUED if due else JobStatus.FAILED,
                delivery_state=None if cancelled else JobDeliveryState.RETRYING if due else JobDeliveryState.DEAD_LETTER,
                expected_version=current.version, fence_token=current.fence_token, error_message=code)
            if due:
                self.engine.execute("UPDATE durable_jobs SET available_at=? WHERE job_id=?", (due.isoformat(), delivery.job_id))
            elif not cancelled:
                self.graph._outbox(DomainEvent(event_type="job.dead_lettered", producer="job_platform", tenant_id=tenant,
                    aggregate_type="job", aggregate_id=delivery.job_id, aggregate_version=job["version"]+1,
                    partition_key=tenant+":"+delivery.job_id, correlation_id=job["correlation_id"],
                    occurred_at=datetime.fromisoformat(now), schema_ref="#/payloads/JobDeadLetteredV1",
                    sensitive_fields=["payload.last_error_details"],
                    payload={"job_id": delivery.job_id, "job_type": IDENTITY_JOB_TYPE, "checkpoint": job["checkpoint"],
                             "attempt": attempt, "error_code": code, "dead_lettered_at": now,
                             "last_error_details": {"error_class": type(error).__name__}}))

    def heartbeat(self, job_id: str, *, expected_version: int, fence_token: int) -> int:
        current = self.graph.queue.get(job_id)
        if current is None or current.fence_token != fence_token or current.version != expected_version:
            raise JobFenceRejectedError("IDENTITY_DELIVERY_FENCE_REJECTED")
        with self.engine.transaction():
            reference = self.auth.reference(current)
        with self.graph.transaction(reference["tenant_id"]):
            _, current, job = self._owned(current)
            version = self.graph.queue.heartbeat(job_id, expected_version=expected_version, fence_token=fence_token)
            renewed = self.graph.queue.get(job_id)
            if job["status"] == "RUNNING":
                if (job["payload"].get("delivery_fence") != fence_token
                        or job["payload"].get("delivery_worker") != current.locked_by):
                    raise JobFenceRejectedError("IDENTITY_ACCEPTANCE_BINDING_REJECTED")
                if self.engine.execute("UPDATE workflow.jobs SET heartbeat_at=clock_timestamp(),lease_expires_at=?,"
                    "version=version+1,updated_at=clock_timestamp() WHERE tenant_id=? AND job_id=? AND version=? "
                    "AND fence_token=? AND timeout_at>clock_timestamp() AND cancellation_requested_at IS NULL",
                    (renewed.lease_expires_at.isoformat(), reference["tenant_id"], job_id, job["version"], job["fence_token"])).rowcount != 1:
                    raise JobFenceRejectedError("IDENTITY_BUSINESS_FENCE_REJECTED")
            return version


def run_identity_delivery(delivery, graph) -> None:
    from modules.listing.application.identity_execution import IdentityExecutionService

    owner = IdentityJobAdmission(graph)
    try:
        IdentityExecutionService(graph).run(delivery)
    except Exception as exc:
        owner.settle_delivery(delivery, error=exc)
    else:
        owner.settle_delivery(delivery)


__all__ = ["IDENTITY_JOB_TYPE", "IdentityDeliveryAuthenticator", "IdentityDeliveryUnavailable", "IdentityJobAdmission"]
