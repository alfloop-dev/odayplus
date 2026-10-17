"""Original 90/180/365-day collection due tasks; no invented POS labels.

The queue's real available_at admits a due job. This consumer records that
collection is due and the automatic measurement owner is unavailable. A due
notification job may finish while collection explicitly remains incomplete.
"""
from contextlib import nullcontext
from datetime import UTC, datetime

from shared.audit.events import AuditEvent
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.netplan_outcomes import (
    DUE_JOB_TYPE,
    HORIZON_DAYS,
    PROGRESS_COLLECTION,
    TASK_COLLECTION,
    aware,
    content_hash,
    retained_collection_progress,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.jobs.queue import JobStatus, NonRetryableJobError


def utc_now():
    return datetime.now(UTC)


def handle_netplan_outcome_due(job, persistence):
    """Use the stored tenant task, source fingerprint and current lease fence."""
    payload = job.payload
    required = {"tenant_id", "scenario_id", "task_id", "execution_id", "horizon_days", "due_at", "task_source_sha256"}
    if (job.job_type != DUE_JOB_TYPE or not isinstance(payload, dict) or set(payload) != required
            or any(type(payload.get(key)) is not str or not payload[key].strip() for key in required - {"horizon_days"})
            or type(payload["horizon_days"]) is not int or payload["horizon_days"] not in HORIZON_DAYS):
        raise NonRetryableJobError("NetPlan due task payload is not source bound")
    engine, queue, audit = persistence.engine, persistence.job_queue, persistence.audit_log
    if (engine is None or not isinstance(queue, DurableJobQueue) or queue._engine is not engine
            or not isinstance(audit, DurableAuditLog) or audit._engine is not engine):
        raise RuntimeError("NetPlan due task SQL owners are unavailable")
    repository = persistence._scoped_repository("netplan_repository", payload["tenant_id"])
    documents = getattr(repository, "_store", None)
    if (not isinstance(documents, TenantScopedDocumentStore) or documents.engine is not engine
            or documents.tenant_id != payload["tenant_id"]):
        raise RuntimeError("NetPlan due task tenant owner is unavailable")
    with repository.scenario_transaction(payload["scenario_id"]):
        if getattr(engine, "dialect", None) == "postgresql":
            engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id = ? FOR UPDATE", (job.job_id,))
        claimed = queue.get(job.job_id)
        now = aware(utc_now())
        if (claimed is None or claimed.job_type != DUE_JOB_TYPE or claimed.payload != payload
                or claimed.status != JobStatus.RUNNING or job.status != JobStatus.RUNNING
                or claimed.fence_token != job.fence_token or claimed.locked_by != job.locked_by
                or not claimed.locked_by or claimed.lease_expires_at is None
                or aware(claimed.lease_expires_at) <= now):
            raise RuntimeError("NetPlan due task lease ownership changed")
        task = documents.get(TASK_COLLECTION, payload["task_id"])
        if (not isinstance(task, dict) or content_hash(task) != payload["task_source_sha256"]
                or task.get("collection_job_id") != job.job_id
                or any(task.get(key) != payload[key] for key in required - {"task_source_sha256"})
                or task.get("status") != "SCHEDULED"):
            raise NonRetryableJobError("NetPlan due task source no longer matches")
        try:
            due = aware(datetime.fromisoformat(task["due_at"].replace("Z", "+00:00")))
        except (ValueError, TypeError) as exc:
            raise NonRetryableJobError("NetPlan due task timestamp is invalid") from exc
        if now < due:
            raise RuntimeError("NetPlan collection period is not due")
        scenario = repository.get_scenario(payload["scenario_id"])
        solve = repository.get_solve(payload["scenario_id"])
        execution = repository.get_execution(payload["scenario_id"])
        approval = next((row for row in repository.list_approvals(payload["scenario_id"])
                         if row.approval_id == task.get("approval_id")), None)
        if (scenario is None or scenario.tenant_id != payload["tenant_id"] or solve is None
                or execution is None or execution.execution_id != payload["execution_id"]
                or execution.scenario_id != scenario.scenario_id or approval is None
                or not approval.authentic_approval_verified
                or content_hash(approval.to_dict()) != task.get("approval_source_sha256")
                or scenario.selected_candidate_id != approval.selected_candidate_id
                or solve.problem_hash != approval.solver_problem_hash or solve.is_stale(scenario)
                or aware(execution.executed_at).isoformat() != task.get("executed_at")):
            raise NonRetryableJobError("NetPlan due task canonical authority changed")
        from modules.netplan.application.planning import NetPlanService
        subject = NetPlanService._selected_approval_subject(scenario, solve)
        if tuple(execution.actions) != subject.actions or subject.action_signature != tuple(sorted(approval.selected_action_signature)):
            raise NonRetryableJobError("NetPlan due task selected execution changed")
        previous = retained_collection_progress(documents, audit, task)
        if previous is not None:
            if (not isinstance(previous, dict) or previous.get("task_source_sha256") != payload["task_source_sha256"]
                    or previous.get("job_id") != job.job_id):
                raise NonRetryableJobError("NetPlan collection progress source changed")
            # A retry/read must not reset DUE or erase future recorded
            # collection facts. No replacement write occurs on this path.
            return
        sink = getattr(audit, "_worm_sink", None)
        delivery_scope = nullcontext()
        if sink is not None:
            from shared.audit.durable_delivery import DurableAuditDelivery
            delivery_scope = audit.transactional_worm_sink(DurableAuditDelivery(engine, payload["tenant_id"], queue, sink))
        with delivery_scope:
            progress_source = {**payload, "job_id": job.job_id, "state": "DUE_SOURCE_UNAVAILABLE",
                               "due_observed_at": now.isoformat(),
                               "collected_outcome_id": None, "measured_value": None,
                               "mature": False, "training_eligible": False,
                               "reasonCode": "NETPLAN_REALIZED_MARGIN_PROVIDER_NOT_BOUND"}
            event = audit.record(AuditEvent(event_type="netplan.outcome_collection_due.v1",
                actor="netplan-outcome-worker", action="mark_collection_due",
                resource="netplan/scenarios/" + scenario.scenario_id, outcome="source_unavailable",
                correlation_id=job.correlation_id,
                metadata={**payload, "job_id": job.job_id, "fence_token": job.fence_token,
                          "measurement_available": False, "reason_code": "NETPLAN_REALIZED_MARGIN_PROVIDER_NOT_BOUND",
                          "progress_sha256": content_hash(progress_source)}))
            progress = {**progress_source, "audit_event_id": event.event_id}
            if not documents.put_if_absent(PROGRESS_COLLECTION, task["task_id"], progress,
                                            group_key=scenario.scenario_id):
                raise RuntimeError("NetPlan collection progress reservation conflict")
